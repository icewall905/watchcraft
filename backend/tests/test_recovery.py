"""Worker recovery: interrupted runs, stale capture leases and the downloader pass.

A crash must never leave work stuck in a running state, and must never lose a job that
was in flight. These are the paths that run once at startup.
"""
import os
import tempfile
import time

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-recovery-'))

import pytest
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, session
from bt.hub import capture, engine
from bt.hub.models import CaptureJob, ContentItem, Curator, CuratorRun, ItemState


H = {'X-Profile-Id': '1'}


@pytest.fixture(autouse=True)
def _schema():
    init_db()


@pytest.fixture
def clean():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def test_recover_stale_fails_interrupted_runs_and_leaves_live_ones(clean):
    now = time.time()
    with session() as s:
        stale = Curator(profile_id=1, name='Interrupted', last_status='running', next_run=now - 60)
        live = Curator(profile_id=1, name='Still going', last_status='running', next_run=now + 600)
        s.add(stale); s.add(live); s.flush()
        old = CuratorRun(curator_id=stale.id, profile_id=1, status='running', started_at=now - engine.LEASE_S - 60)
        fresh = CuratorRun(curator_id=live.id, profile_id=1, status='running', started_at=now - 5)
        s.add(old); s.add(fresh); s.commit()
        old_id, fresh_id, stale_id, live_id = old.id, fresh.id, stale.id, live.id

    engine.recover_stale()

    with session() as s:
        old, fresh = s.get(CuratorRun, old_id), s.get(CuratorRun, fresh_id)
        assert old.status == 'failed' and old.finished_at
        assert 'Interrupted' in old.errors[-1]['message']
        assert fresh.status == 'running' and fresh.finished_at is None
        # A curator whose lease expired is no longer reported as running.
        assert s.get(Curator, stale_id).last_status == 'failed'
        assert s.get(Curator, live_id).last_status == 'running'


def test_a_stale_capture_lease_is_claimed_again(clean, monkeypatch):
    """A worker that died mid-capture must not hold the job forever."""
    spawned = []
    monkeypatch.setattr(capture.slots, 'spawn', lambda *a, **k: spawned.append(a) or True)
    with session() as s:
        item = ContentItem(url='https://example.org/a', type='news', title='A', summary='A')
        s.add(item); s.flush()
        s.add(ItemState(profile_id=1, item_id=item.id))
        s.add(CaptureJob(profile_id=1, item_id=item.id, status='running', lease=time.time() - 60))
        s.commit()
        iid = item.id
    assert capture.dispatch() is True
    assert spawned, 'the expired lease was re-claimed'
    with session() as s:
        job = s.get(CaptureJob, (1, iid))
        assert job.status == 'running' and job.lease > time.time() and job.attempts == 1


def test_a_live_capture_lease_is_left_alone(clean, monkeypatch):
    monkeypatch.setattr(capture.slots, 'spawn', lambda *a, **k: pytest.fail('a live lease must not be re-claimed'))
    with session() as s:
        item = ContentItem(url='https://example.org/b', type='news', title='B', summary='B')
        s.add(item); s.flush()
        s.add(ItemState(profile_id=1, item_id=item.id))
        s.add(CaptureJob(profile_id=1, item_id=item.id, status='running', lease=time.time() + 600))
        s.commit()
    assert capture.dispatch() is False


def test_a_capture_job_for_a_profile_that_lost_access_is_failed(clean, monkeypatch):
    monkeypatch.setattr(capture.slots, 'spawn', lambda *a, **k: pytest.fail('no work for a profile without access'))
    with session() as s:
        item = ContentItem(url='https://example.org/c', type='news', title='C', summary='C')
        s.add(item); s.flush()
        # No ItemState and no discovery: the profile cannot see this item any more.
        s.add(CaptureJob(profile_id=1, item_id=item.id, status='queued', next_run=0))
        s.commit()
        iid = item.id
    assert capture.dispatch() is False
    with session() as s:
        job = s.get(CaptureJob, (1, iid))
        assert job.status == 'failed' and 'no longer has access' in job.error
