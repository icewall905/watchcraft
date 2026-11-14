"""T-121: per-interest replenishment — saturated interests cannot starve others, quiet ones back off."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-news-fairness-'))

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub import news_supply
from bt.hub.engine import NEWS_RUN_FLOOR, next_run_after, news_run_budget
from bt.hub.models import (ArticleText, CaptureJob, ContentItem, Curator, CuratorDiscovery, CuratorRun,
                           ItemState, ItemVerdict, NewsImpression, Topic)
from bt.hub.news_supply import interest_decision, interest_plan
from bt.hub.shelf import snapshot as shelf_snapshot
from bt.models import Profile

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    news_supply.clear_cache()
    with session() as s:
        s.get(Profile, 1).role = 'adult'
        s.add(Profile(id=2, name='Two', role='adult'))
        s.commit()
    yield
    news_supply.clear_cache()


def curator(s, cid, *, pid=1, interval=6, **kw):
    c = Curator(id=cid, profile_id=pid, name=f'Interest {cid}', kind='watch', enabled=True,
                interval_hours=interval, content_types=['news'], last_status='ok', max_per_run=30,
                next_run=NOW + 3600, **kw)
    s.add(c)
    return c


def item(s, key, *, pid=1, cids=(1,), age=3600, tier='keep', ready=True, title=None, kind='news'):
    it = ContentItem(url=f'https://fair.example/{pid}/{key}', type=kind, title=title or f'Story {key}',
                     body='Archived body text.', enrichment={'summary': 'AI summary.'},
                     publisher=f'Pub {hash(key) % 5}', first_seen=NOW - age, published_at=NOW - age)
    s.add(it)
    s.flush()
    for cid in cids:
        s.add(CuratorDiscovery(curator_id=cid, item_id=it.id, profile_id=pid, found_at=NOW - age))
    if tier:
        s.add(ItemVerdict(profile_id=pid, item_id=it.id, tier=tier))
    s.add(ArticleText(item_id=it.id, raw_body=it.body, status='reviewed' if ready else 'queued', version=1))
    s.add(CaptureJob(profile_id=pid, item_id=it.id, status='complete' if ready else 'queued',
                     summary_status='ready' if ready else 'pending'))
    return it


def stock(s, cid, n, *, pid=1, prefix=None):
    for i in range(n):
        item(s, f'{prefix or cid}-{i}', pid=pid, cids=(cid,), age=600 + i)


def runs(s, cid, n, *, found_in_latest=False):
    """n completed scheduled runs, newest first spaced an hour apart."""
    for k in range(n):
        start = NOW - (k + 1) * 3600
        s.add(CuratorRun(curator_id=cid, profile_id=1, started_at=start, finished_at=start + 60,
                         status='ok', trigger='schedule'))
    if found_in_latest:
        it = ContentItem(url=f'https://fair.example/run-find-{cid}', type='article', title='Found',
                         first_seen=NOW - 3600 + 30)
        s.add(it)
        s.flush()
        s.add(CuratorDiscovery(curator_id=cid, item_id=it.id, profile_id=1, found_at=NOW - 3600 + 30))


def test_saturated_interest_cannot_starve_an_empty_one_even_when_profile_is_full():
    with session() as s:
        general, niche = curator(s, 1), curator(s, 2)
        stock(s, 1, 300)  # fills the profile-wide ready shelf and candidate buffer alone
        s.commit()
        assert news_supply.snapshot(s, 1, NOW)['met'] is True

        assert news_run_budget(s, niche, 'schedule', NOW) == (NEWS_RUN_FLOOR, 30)
        assert next_run_after(s, niche, NOW) == NOW + 3600
        assert news_run_budget(s, general, 'schedule', NOW) == (30, None)
        assert next_run_after(s, general, NOW) == NOW + 6 * 3600


def test_saturated_interest_is_not_boosted_while_profile_total_is_short():
    with session() as s:
        general, niche = curator(s, 1), curator(s, 2)
        stock(s, 1, 120)  # 2 interests: unseen share 50, saturated at 100
        stock(s, 2, 1)
        s.commit()
        assert news_supply.snapshot(s, 1, NOW)['met'] is False
        assert interest_decision(s, general, NOW)['state'] == 'saturated'
        assert news_run_budget(s, general, 'schedule', NOW) == (30, None)
        assert news_run_budget(s, niche, 'schedule', NOW) == (NEWS_RUN_FLOOR, 30)


def test_seen_stock_makes_an_interest_underserved_again():
    with session() as s:
        general = curator(s, 1)
        stock(s, 1, 300)
        s.commit()
        assert interest_decision(s, general, NOW)['boost'] is False
        for row in s.exec(CuratorDiscovery.__table__.select()).all():
            s.add(NewsImpression(profile_id=1, item_id=row.item_id, first_shown=NOW, last_shown=NOW))
        s.commit()
        decision = interest_decision(s, general, NOW)
        assert decision['state'] == 'underserved' and decision['boost'] is True


def test_deep_unseen_backlog_with_trickle_intake_is_not_boosted():
    assert news_supply.classify({'unseen_ready': 500, 'new_24h': 10},
                                {'unseen_share': 20, 'daily_share': 100})[0] == 'saturated'
    with session() as s:
        slow = curator(s, 1)
        stock(s, 1, 2)                       # 2 new today
        for i in range(250):                 # deep unseen backlog from earlier days
            item(s, f'old-{i}', cids=(1,), age=2 * 86400 + i)
        s.commit()
        assert interest_decision(s, slow, NOW)['state'] == 'saturated'
        assert news_run_budget(s, slow, 'schedule', NOW) == (30, None)
        assert next_run_after(s, slow, NOW) == NOW + 6 * 3600


def test_profile_is_met_when_every_interest_meets_its_share():
    with session() as s:
        a, b = curator(s, 1), curator(s, 2)
        stock(s, 1, 60)   # shares for 2 interests: 50 unseen, 50 new/day; 60 is ok, not saturated
        stock(s, 2, 60)
        s.commit()
        assert news_supply.snapshot(s, 1, NOW)['met'] is False  # candidate buffer still short
        plan = interest_plan(s, 1, NOW)
        assert plan['unseen_share'] * 2 >= news_supply.UNSEEN_TARGET
        assert plan['totals']['unseen_ready'] >= 2 * plan['unseen_share']
        assert plan['profile_met'] is True
        for c in (a, b):
            d = interest_decision(s, c, NOW, plan=plan)
            assert d['state'] == 'ok' and d['boost'] is False
            assert next_run_after(s, c, NOW) == NOW + 6 * 3600


def test_quiet_interest_backs_off_exponentially_up_to_its_interval():
    with session() as s:
        quiet = curator(s, 2)
        s.commit()
        assert next_run_after(s, quiet, NOW) == NOW + 3600  # empty interest, no history yet
        runs(s, 2, 3)
        s.commit()
        assert next_run_after(s, quiet, NOW) == NOW + 2 * 3600
        decision = interest_decision(s, quiet, NOW)
        assert decision['quiet'] and decision['quiet_runs'] == 3 and decision['quiet_reason']
        s.exec(CuratorRun.__table__.delete())
        runs(s, 2, 4)
        s.commit()
        assert next_run_after(s, quiet, NOW) == NOW + 4 * 3600
        s.exec(CuratorRun.__table__.delete())
        runs(s, 2, 8)
        s.commit()
        assert next_run_after(s, quiet, NOW) == NOW + 6 * 3600  # never beyond the configured interval


def test_a_new_article_resets_the_quiet_streak():
    with session() as s:
        c = curator(s, 2)
        runs(s, 2, 5, found_in_latest=True)
        s.commit()
        assert news_supply.quiet_streak(s, 2) == 0
        assert next_run_after(s, c, NOW) == NOW + 3600


def test_interest_counts_are_distinct_items_matching_news_visibility():
    with session() as s:
        curator(s, 1)
        curator(s, 2)
        s.add(Topic(profile_id=1, name='Muted', kind='news', muted=True, keywords=['blocked']))
        shared = item(s, 'shared', cids=(1, 2))           # one item, both interests
        seen = item(s, 'seen', cids=(1,))
        s.add(NewsImpression(profile_id=1, item_id=seen.id))
        read = item(s, 'read', cids=(1,))
        s.add(ItemState(profile_id=1, item_id=read.id, read_at=NOW))
        item(s, 'pending-review', cids=(1,), tier=None)
        item(s, 'pending-capture', cids=(2,), ready=False)
        item(s, 'old', cids=(1,), age=2 * 86400)          # ready but not new today
        item(s, 'muted', cids=(1,), title='A blocked headline')
        item(s, 'rejected', cids=(1,), tier='reject')
        item(s, 'video', cids=(1,), kind='video')
        s.commit()

        plan = interest_plan(s, 1, NOW)
        assert plan['interests'][1] == {'ready': 3, 'unseen_ready': 2, 'new_24h': 3, 'qualified': 4,
                                        'pending_review': 1, 'pending_capture': 0}
        assert plan['interests'][2] == {'ready': 1, 'unseen_ready': 1, 'new_24h': 2, 'qualified': 2,
                                        'pending_review': 0, 'pending_capture': 1}
        # Profile totals never double-count the shared item.
        assert plan['totals']['ready'] == 3 == plan['supply']['ready_count']
        assert plan['totals']['unseen_ready'] == 2
        assert shared.id is not None


def test_profiles_are_isolated():
    with session() as s:
        curator(s, 1)
        curator(s, 9, pid=2)
        a = item(s, 'mine', cids=(1,))
        stock(s, 9, 5, pid=2)
        s.add(NewsImpression(profile_id=2, item_id=a.id))  # another profile's impression
        s.commit()

        mine = interest_plan(s, 1, NOW)
        assert mine['interests'] == {1: {'ready': 1, 'unseen_ready': 1, 'new_24h': 1, 'qualified': 1,
                                         'pending_review': 0, 'pending_capture': 0}}
        assert interest_plan(s, 2, NOW)['interests'][9]['ready'] == 5


def test_shelf_exposes_per_interest_fairness_and_upkeep_skips_saturated(monkeypatch):
    from bt.hub import shelf_maintenance as upkeep
    with session() as s:
        curator(s, 1, last_run=NOW - 7200)
        curator(s, 2, last_run=NOW - 7200)
        stock(s, 1, 300)
        s.commit()
        articles = shelf_snapshot(s, 1, NOW)['articles']
        assert articles['unseen_ready'] == 300 and articles['new_24h'] == 300 and articles['daily_target'] == 100
        general, niche = articles['interests']
        for key in ('ready', 'target', 'fresh_24h', 'publishers', 'needs_refill', 'last_success', 'next_run'):
            assert key in general  # backward compatible
        assert general['saturated'] and not general['boost']
        assert niche['state'] == 'underserved' and niche['boost'] and niche['needs_refill']
        assert niche['unseen_ready'] == 0 and niche['new_24h'] == 0

    monkeypatch.setattr(upkeep, '_claim', lambda now: True)
    queued = upkeep.maintain(NOW)['profiles']['1']['collectors_queued']
    assert queued == [2]


def test_engine_plan_is_cached_briefly_per_profile(monkeypatch):
    calls = []
    real = news_supply._scan
    monkeypatch.setattr(news_supply, '_scan', lambda *a: calls.append(a) or real(*a))
    with session() as s:
        c = curator(s, 1)
        s.commit()
        news_run_budget(s, c, 'schedule', NOW)
        next_run_after(s, c, NOW + 1)
        assert len(calls) == 1
        news_supply.clear_cache()
        next_run_after(s, c, NOW)
        assert len(calls) == 2


def test_shelf_shows_why_an_interest_finds_no_articles(monkeypatch):
    from bt.hub import article_upgrade
    with session() as s:
        curator(s, 1); curator(s, 2)
        stock(s, 1, 5)
        s.commit()
    monkeypatch.setattr(article_upgrade, 'coverage', lambda s, pid: {'checked_at': NOW, 'curators': [
        {'id': 2, 'reason': 'No article-type sources; 44 web findings in 7 days.'}] if pid == 1 else []})
    with session() as s:
        rows = {i['id']: i for i in shelf_snapshot(s, 1, NOW)['articles']['interests']}
    assert rows[2]['coverage_reason'].startswith('No article-type sources')
    assert rows[1]['coverage_reason'] == ''

