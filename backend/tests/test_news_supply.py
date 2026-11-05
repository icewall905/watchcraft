"""News shelf supply counts only distinct items that passed the master curator."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-news-supply-'))

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub.engine import next_run_after, news_run_budget
from bt.hub.models import (ArticleText, CaptureJob, ContentItem, Curator, CuratorDiscovery,
                           ItemState, ItemVerdict, Source, Topic)
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
                       body='A locally archived article body with enough detail to read.',
                       enrichment={'summary': 'A concise AI summary.'},
                       first_seen=NOW - age, published_at=NOW - age)
    s.add(item)
    s.flush()
    s.add(CuratorDiscovery(curator_id=curator, item_id=item.id, profile_id=pid))
    if tier:
        s.add(ItemVerdict(profile_id=pid, item_id=item.id, tier=tier))
        s.add(ArticleText(item_id=item.id, raw_body=item.body, status='reviewed', version=1))
        s.add(CaptureJob(profile_id=pid, item_id=item.id, status='complete', summary_status='ready'))
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
        assert result == {'target': 100, 'candidate_target': 300, 'window_days': 7, 'available': 101,
                          'shortfall': 0, 'ready_count': 101, 'ready_target': 100, 'ready_shortfall': 0,
                          'candidate_count': 101, 'candidate_shortfall': 199,
                          'ready_met': True, 'candidate_buffer_met': False, 'candidate_met': False,
                          'met': False, 'pending_review': 0, 'as_of': NOW}


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
        assert result['candidate_count'] == 1
        assert result['candidate_shortfall'] == 299
        assert result['ready_count'] == 1
        assert result['ready_shortfall'] == 99
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


@pytest.mark.parametrize('body,archive,version,job,summary_status,summary', [
    ('', 'reviewed', 1, 'complete', 'ready', 'AI summary'),
    ('Readable text', 'failed', 1, 'complete', 'ready', 'AI summary'),
    ('Readable text', 'reviewed', 0, 'complete', 'ready', 'AI summary'),
    ('Readable text', 'reviewed', 1, 'queued', 'ready', 'AI summary'),
    ('Readable text', 'reviewed', 1, 'partial', 'failed', 'AI summary'),
    ('Readable text', 'reviewed', 1, 'complete', 'failed', 'AI summary'),
    ('Readable text', 'reviewed', 1, 'complete', 'ready', ''),
])
def test_quality_approved_candidate_is_not_ready_until_archive_and_ai_summary_are_complete(
        body, archive, version, job, summary_status, summary):
    with session() as s:
        item = add_item(s, 'incomplete')
        item.body = body
        item.enrichment = {'summary': summary}
        article_text = s.get(ArticleText, item.id)
        article_text.status, article_text.version = archive, version
        capture = s.get(CaptureJob, (1, item.id))
        capture.status, capture.summary_status = job, summary_status
        s.add_all([item, article_text, capture])
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['candidate_count'] == 1
        assert result['available'] == 1
        assert result['shortfall'] == 99
        assert result['ready_count'] == 0
        assert result['ready_shortfall'] == 100
        assert not result['ready_met']


def test_candidate_buffer_and_ready_target_are_independent():
    with session() as s:
        for n in range(300):
            add_item(s, n)
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['candidate_count'] == 300
        assert result['candidate_buffer_met']
        assert result['available'] == 300
        assert result['ready_count'] == 300
        assert result['ready_met']
        assert result['met']


def test_read_or_archived_quality_approved_items_remain_approved_but_do_not_stock_the_ready_shelf():
    with session() as s:
        read = add_item(s, 'read')
        archived = add_item(s, 'archived')
        s.add_all([ItemState(profile_id=1, item_id=read.id, read_at=NOW),
                   ItemState(profile_id=1, item_id=archived.id, archived=True)])
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['available'] == 2
        assert result['candidate_count'] == 2
        assert result['ready_count'] == 0


def test_news_run_budget_only_grows_for_scheduled_enabled_article_collectors(monkeypatch):
    from bt.hub import news_supply
    monkeypatch.setattr(news_supply, 'snapshot', lambda *_: {'met': False})
    with session() as s:
        curator = Curator(profile_id=1, name='News', interval_hours=6, enabled=True,
                          kind='watch', content_types=['news'], max_per_run=30)
        assert news_run_budget(s, curator, 'schedule', NOW) == (100, 30)
        assert news_run_budget(s, curator, 'manual', NOW) == (30, None)
        curator.enabled = False
        assert news_run_budget(s, curator, 'schedule', NOW) == (30, None)
        curator.enabled = True
        curator.content_types = ['news', 'article', 'web', 'project']
        assert news_run_budget(s, curator, 'schedule', NOW) == (100, 30)
        curator.content_types = ['article']
        curator.max_per_run = 180
        assert news_run_budget(s, curator, 'schedule', NOW) == (180, 180)
        curator.max_per_run = 500  # Legacy values never get silently reduced below 150; API max is 200.
        assert news_run_budget(s, curator, 'schedule', NOW) == (200, 200)
        monkeypatch.setattr(news_supply, 'snapshot', lambda *_: {'met': True})
        curator.max_per_run = 30
        assert news_run_budget(s, curator, 'schedule', NOW) == (30, None)


def test_candidate_screening_round_robins_publishers_before_returning_to_a_busy_publisher():
    from bt.hub.engine import fresh_candidates
    busy = Source(id=1, profile_id=1, provider='rss', url='https://busy.example/feed')
    alternate = Source(id=2, profile_id=1, provider='rss', url='https://alternate.example/feed')

    def row(source, publisher, n):
        return [source, '', {'url': f'https://example.org/{publisher}/{n}', 'publisher': publisher}, 50, '']

    batch = [row(busy, 'Busy Publisher', i) for i in range(10)] + [row(alternate, 'Other Publisher', 0)]
    chosen = fresh_candidates(batch, set(), 5)
    assert [entry[2]['publisher'] for entry in chosen] == [
        'Busy Publisher', 'Other Publisher', 'Busy Publisher', 'Busy Publisher', 'Busy Publisher']


def test_mixed_curator_uses_extra_screening_slots_for_articles_only():
    from bt.hub.engine import fresh_candidates
    source = Source(id=1, profile_id=1, provider='rss', url='https://mixed.example/feed')
    news = [[source, '', {'url': f'https://news.example/{n}', 'type': 'news', 'publisher': f'News {n}'}, 50, '']
            for n in range(100)]
    other = [[source, '', {'url': f'https://other.example/{n}', 'type': 'web', 'publisher': f'Other {n}'}, 50, '']
             for n in range(100)]

    batch = fresh_candidates(news + other, set(), 100, nonarticle_limit=30)
    assert len(batch) == 100
    assert sum(row[2]['type'] == 'web' for row in batch) == 30
    assert sum(row[2]['type'] == 'news' for row in batch) == 70


def test_partial_capture_with_reviewed_body_and_ready_ai_summary_counts_as_ready():
    with session() as s:
        item = add_item(s, 'optional-image-failed')
        job = s.get(CaptureJob, (1, item.id))
        job.status = 'partial'
        s.add(job)
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['available'] == 1
        assert result['ready_count'] == 1


def test_empty_preserved_raw_article_text_does_not_count_as_a_full_archive():
    with session() as s:
        item = add_item(s, 'empty-raw')
        archive = s.get(ArticleText, item.id)
        archive.raw_body = ''
        s.add(archive)
        s.commit()

        result = snapshot(s, 1, NOW)
        assert result['available'] == 1
        assert result['ready_count'] == 0
