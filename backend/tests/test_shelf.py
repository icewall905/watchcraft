"""Shelf inventory reports ready article volume and per-interest deficits."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-shelf-'))

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub.models import (ArticleText, CaptureJob, ContentItem, Curator, CuratorDiscovery,
                           ItemState, ItemVerdict, Story, StoryItem, Topic)
from bt.hub.shelf import snapshot
from bt.models import Profile

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    with session() as s:
        s.get(Profile, 1).role = 'adult'
        s.add(Profile(id=2, name='Two', role='adult'))
        s.commit()
    yield


def add_ready(s, n, *, pid=1, curator=1, age=3600, publisher='Publisher A', kind='news',
              title=None, story_id=None):
    item = ContentItem(url=f'https://shelf.example/{n}', type=kind,
                       title=title or f'Article {n}', summary='',
                       body='Full locally archived article body.',
                       enrichment={'summary': 'Short overview.'}, publisher=publisher,
                       first_seen=NOW - age, published_at=NOW - age)
    s.add(item)
    s.flush()
    s.add_all([
        CuratorDiscovery(curator_id=curator, item_id=item.id, profile_id=pid),
        ItemVerdict(profile_id=pid, item_id=item.id, tier='keep'),
        ArticleText(item_id=item.id, raw_body=item.body, status='reviewed', version=1),
        CaptureJob(profile_id=pid, item_id=item.id, status='complete', summary_status='ready'),
    ])
    if story_id is not None:
        s.add(StoryItem(item_id=item.id, story_id=story_id))
    return item


def add_curator(s, cid, name, *, pid=1, types=('news',), **kw):
    values = dict(id=cid, profile_id=pid, name=name, kind='watch', enabled=True,
                  interval_hours=6, content_types=list(types), last_success=NOW - 3600,
                  next_run=NOW + 3600)
    values.update(kw)
    c = Curator(**values)
    s.add(c)
    return c


def test_counts_strict_ready_shelf_and_interest_gaps_even_when_total_is_full():
    with session() as s:
        add_curator(s, 1, 'Science')
        add_curator(s, 2, 'Local news', types=('news', 'article'))
        # Fully stocked aggregate, but only one interest has any matching items.
        for n in range(100):
            add_ready(s, n, curator=1, publisher=f'Publisher {n % 4}', age=3600 + n)
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['as_of'] == NOW
        shelf = result['articles']
        assert shelf['ready'] == 100
        assert shelf['target'] == 100
        assert shelf['fresh_24h'] == 100
        assert shelf['publishers'] == 4
        assert shelf['stories'] == 100
        assert shelf['oldest_ready_at'] == NOW - 3699
        assert shelf['needs_refill'] is True
        assert [x['ready'] for x in shelf['interests']] == [100, 0]
        assert all(x['target'] == 20 for x in shelf['interests'])
        assert shelf['interests'][1]['needs_refill'] is True
        assert shelf['needs_refill'] is True


def test_readiness_mutes_verdicts_archive_state_and_profiles_are_respected():
    with session() as s:
        add_curator(s, 1, 'News')
        s.add(Topic(profile_id=1, name='Muted topic', kind='news', muted=True, keywords=['blocked phrase']))
        add_ready(s, 'kept')
        muted = add_ready(s, 'muted', title='Blocked phrase in headline')
        read = add_ready(s, 'read')
        archived = add_ready(s, 'archived')
        rejected = add_ready(s, 'rejected')
        other_profile = add_ready(s, 'other', pid=2, curator=9)
        s.add(ItemState(profile_id=1, item_id=read.id, read_at=NOW - 1))
        s.add(ItemState(profile_id=1, item_id=archived.id, archived=True))
        s.get(ItemVerdict, (1, rejected.id)).tier = 'reject'
        s.commit()

        result = snapshot(s, 1, NOW)['articles']
        assert result['ready'] == 1
        assert result['stories'] == 1
        assert result['interests'][0]['ready'] == 1
        assert snapshot(s, 2, NOW)['articles']['ready'] == 1
        assert snapshot(s, 999, NOW)['articles']['ready'] == 0


def test_freshness_story_dedup_and_diversity_are_measured_per_interest():
    with session() as s:
        add_curator(s, 1, 'Technology')
        # Same clustered story across two URLs occupies one shelf slot.
        story = Story(kind='news', title='One event')
        s.add(story)
        s.flush()
        add_ready(s, 'story-a', curator=1, age=2 * 86400, publisher='Known A', story_id=story.id)
        add_ready(s, 'story-b', curator=1, age=3600, publisher='Known A', story_id=story.id)
        for n in range(2, 8):
            add_ready(s, n, curator=1, age=4 * 86400, publisher='Known A')
        s.commit()

        interest = snapshot(s, 1, NOW)['articles']['interests'][0]
        assert interest['ready'] == 7
        assert interest['fresh_24h'] == 1
        assert interest['publishers'] == 1
        assert interest['needs_refill'] is True


def test_only_enabled_scheduled_watch_article_interests_are_included():
    with session() as s:
        add_curator(s, 1, 'All types', types=())
        add_curator(s, 2, 'Article', types=('article',))
        add_curator(s, 3, 'Video', types=('video',))
        add_curator(s, 4, 'Manual', types=('news',), interval_hours=0)
        add_curator(s, 5, 'Paused', types=('news',), enabled=False)
        add_curator(s, 6, 'Research', types=('news',), kind='research')
        s.commit()

        assert [x['name'] for x in snapshot(s, 1, NOW)['articles']['interests']] == ['All types', 'Article']
