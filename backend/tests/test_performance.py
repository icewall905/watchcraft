"""Performance guards: the hot read paths must not issue a query per row.

Timing assertions are flaky on shared machines, so these count SQL statements instead.
A query count that grows with the number of categories or items is an N+1 regression.
"""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-perf-'))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.main import app
from bt.models import Candidate, Category, Video

H = {'X-Profile-Id': '1'}


@pytest.fixture
def client():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield TestClient(app, raise_server_exceptions=True)
    SQLModel.metadata.drop_all(db.engine)
    init_db()


class Counter:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        event.listen(db.engine, 'before_cursor_execute', self._record)
        return self

    def __exit__(self, *_):
        event.remove(db.engine, 'before_cursor_execute', self._record)

    def _record(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(statement)

    def __len__(self):
        return len(self.statements)


def _seed(categories: int, per_category: int, offset: int = 0):
    with session() as s:
        for c in range(categories):
            cat = Category(profile_id=1, name=f'Playlist {c}', emoji='▶', color='#7c5cff')
            s.add(cat); s.flush()
            for k in range(per_category):
                vid = f"p{offset + c:02d}{k:08d}"  # exactly 11 chars, unique per (playlist, item)
                s.add(Video(id=vid, title=f'Video {c}-{k}', channel_name='Test', duration_s=60))
                s.add(Candidate(category_id=cat.id, video_id=vid, status='accepted', score=80,
                                summary='why', position=k))
        s.commit()


def test_home_feed_query_count_does_not_grow_with_categories(client):
    """One playlist and six playlists must cost the same number of statements."""
    _seed(1, 3)
    with Counter() as small:
        assert client.get('/api/feed/home', headers=H).status_code == 200
    _seed(5, 3, offset=1)  # five more playlists, same shape
    with Counter() as large:
        assert client.get('/api/feed/home', headers=H).status_code == 200
    # Each playlist needs its own item query plus one batched offline lookup; more than that is a regression.
    assert len(large) <= len(small) + 2 * 5, (
        f'home feed issued {len(small)} statements for 1 playlist and {len(large)} for 6; '
        'that is a per-row query')
    assert len(large) < 60, f'home feed issued {len(large)} statements'


def test_category_list_query_count_is_bounded(client):
    _seed(6, 4)
    with Counter() as counted:
        assert client.get('/api/categories', headers=H).status_code == 200
    assert len(counted) < 25, f'category list issued {len(counted)} statements'
