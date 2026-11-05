"""News shelf supply counts only distinct items that passed the master curator."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-news-supply-'))

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub.engine import next_run_after
from bt.hub.models import ContentItem, Curator, CuratorDiscovery, ItemState, ItemVerdict, Topic
from bt.hub.news_supply import snapshot

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield


def add_item(s, n, *, pid=1, kind='news', age=3600, tier='keep', curator=1, title=None, summary=''):
    item = ContentItem(url=f'https://news.example/{n}', type=kind, title=title or f'Report {n}', summary=summary,
                       first_seen=NOW - age, published_at=NOW - age)
    s.add(item)
    s.flush()
    s.add(CuratorDiscovery(curator_id=curator, item_id=item.id, profile_id=pid))
    if tier:
        s.add(ItemVerdict(profile_id=pid, item_id=item.id, tier=tier))
    return item


def test_counts_distinct_qualified_recent_articles_and_reports_shortfall():
    with session() as s:
        for n in range(101):
            item = add_item(s, n)
            # A second curator finding the same article must not inflate supply.
            s.add(CuratorDiscovery(curator_id=2, item_id=item.id, profile_id=1))
        add_item(s, 'set-aside', tier='set_aside')
        add_item(s, 'reject', tier='reject')
        add_item(s, 'old', age=8 * 86400)
        add_item(s, 'video', kind='video')
        dismissed = add_item(s, 'dismissed')
        s.add(ItemState(profile_id=1, item_id=dismissed.id, dismissed=True))
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result == {'target': 100, 'window_days': 7, 'available': 101, 'shortfall': 0,
                          'met': True, 'pending_review': 0, 'as_of': NOW}


def test_pending_items_do_not_count_as_curated_and_profiles_are_isolated():
    with session() as s:
        add_item(s, 'kept', pid=1)
        add_item(s, 'other-profile', pid=2)
        add_item(s, 'needs-review', pid=1, tier=None)
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['available'] == 1
        assert result['shortfall'] == 99
        assert not result['met']
        assert result['pending_review'] == 1
        assert snapshot(s, 2, NOW)['available'] == 1


def test_muted_news_topic_keywords_and_name_match_title_or_summary():
    with session() as s:
        s.add(Topic(profile_id=1, name='Local politics', kind='news', muted=True,
                    keywords=['city council']))
        add_item(s, 'title-match', title='City Council approves the plan')
        summary = add_item(s, 'summary-match')
        summary.summary = 'The City Council approved the plan.'
        s.add(summary)
        add_item(s, 'other-profile-match', pid=2, title='City Council approves plan')
        # Empty keywords follow the News API's fallback to the topic name.
        s.add(Topic(profile_id=1, name='Sports', kind='news', muted=True, keywords=[]))
        add_item(s, 'name-match', title='Sports league adds two teams')
        s.add(Topic(profile_id=1, name='Øresund', kind='news', muted=True, keywords=['ØRESUND']))
        add_item(s, 'unicode-match', title='øresund bridge update')
        add_item(s, 'kept', title='Harbour bridge project receives funding')
        s.commit()

        assert snapshot(s, 1, NOW)['available'] == 1
        assert snapshot(s, 2, NOW)['available'] == 1


def test_deficit_shortens_only_enabled_scheduled_news_watchers(monkeypatch):
    from bt.hub import news_supply

    monkeypatch.setattr(news_supply, 'snapshot', lambda *_: {'met': False})
    with session() as s:
        base = dict(profile_id=1, name='News', interval_hours=6, enabled=True,
                    kind='watch', content_types=['news'], last_status='ok')
        curator = Curator(**base)
        assert next_run_after(s, curator, NOW) == NOW + 3600

        # A configured one-hour cadence is already at the cap.
        curator.interval_hours = 1
        assert next_run_after(s, curator, NOW) == NOW + 3600
        # Manual, paused, research, and non-news curators keep their own schedule.
        cases = [dict(enabled=False), dict(kind='research'), dict(interval_hours=0),
                 dict(content_types=['web'])]
        for changes in cases:
            values = {**base, **changes}
            c = Curator(**values)
            interval = c.interval_hours
            expected = NOW + interval * 3600 if interval else 1e12
            assert next_run_after(s, c, NOW) == expected
        failed = Curator(**{**base, 'last_status': 'failed'})
        assert next_run_after(s, failed, NOW) == NOW + 6 * 3600
        partial = Curator(**{**base, 'last_status': 'partial'})
        assert next_run_after(s, partial, NOW) == NOW + 6 * 3600


def test_curated_news_target_restores_configured_cadence(monkeypatch):
    from bt.hub import news_supply

    monkeypatch.setattr(news_supply, 'snapshot', lambda *_: {'met': True})
    with session() as s:
        curator = Curator(profile_id=1, name='News', interval_hours=12, enabled=True,
                          kind='watch', content_types=['article'], last_status='ok')
        assert next_run_after(s, curator, NOW) == NOW + 12 * 3600
