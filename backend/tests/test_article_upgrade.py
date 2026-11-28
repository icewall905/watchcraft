"""Historical web capture -> article identity upgrade, and the news coverage audit."""
import importlib.util
import json
import time
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub import article_upgrade, capture, slots
from bt.hub.models import (ArticleText, CaptureJob, CapturedAsset, CapturedPage, ContentItem, Curator,
                           CuratorDiscovery, CuratorSource, ItemVerdict, MasterQueue, Source)
from bt.models import Profile

ARTICLE = {'body': 'A long article body.', 'type': 'article', 'author': 'Ada', 'publisher': '',
           'published_at': 1234.0}


@pytest.fixture(autouse=True)
def database(monkeypatch):
    with db.engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    monkeypatch.setattr(capture.ai, 'enabled', lambda: False)
    monkeypatch.setattr(capture.article_cleanup, 'review', lambda title, body: {'body': body, 'status': 'reviewed', 'removed': 0})
    yield
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def curator(pid=1, types=('news', 'article'), name='Open Source LLMs'):
    with session() as s:
        c = Curator(profile_id=pid, name=name, content_types=list(types))
        s.add(c); s.commit()
        return c.id


def seed(cid, pid=1, url='https://www.example.org/2026/story', provider='web_search', **extra):
    with session() as s:
        item = ContentItem(url=url, title='Story', type='web', body='Archived text', provider=provider,
                           enrichment={'summary': 'Existing summary'}, first_seen=time.time(), **extra)
        s.add(item); s.flush()
        iid = item.id
        s.add(CuratorDiscovery(curator_id=cid, item_id=iid, profile_id=pid))
        s.add(CaptureJob(profile_id=pid, item_id=iid, status='complete', summary_status='ready'))
        s.add(CapturedPage(item_id=iid))
        s.add(ArticleText(item_id=iid, raw_body='Archived text', status='reviewed', version=1))
        s.commit()
    return iid


def page(monkeypatch, result, final=None):
    monkeypatch.setattr(capture.curation, 'fetch', lambda url, **kw: (b'<html></html>', final or url,
                                                                      {'content_type': 'text/html'}))
    monkeypatch.setattr(capture, 'extract_page', lambda *a: result)


def probe(iid):
    assert article_upgrade.claim(iid)
    return article_upgrade.probe(iid)


def test_dry_run_counts_only_eligible_items():
    cid, other = curator(), curator(types=('video',), name='Videos')
    seed(cid)
    seed(cid, url='https://social.example/post', provider='mastodon')
    seed(other, url='https://example.org/video-page')
    decided = seed(cid, url='https://example.org/kept')
    pdf = seed(cid, url='https://example.org/report')
    with session() as s:
        s.add(ItemVerdict(profile_id=1, item_id=decided, overruled='promoted'))
        s.add(CapturedAsset(item_id=pdf, kind='document', mime='application/pdf', data=b'%PDF-'))
        s.commit()
        stats = article_upgrade.stats(s)
    assert stats['eligible'] == 1 and stats['web_captured_items'] == 5
    assert stats['not_eligible'] == {'curator_not_news': 1, 'document': 1, 'social_post': 1,
                                     'user_or_rule_decision': 1}
    assert 'example' not in json.dumps(stats)


def test_verified_article_is_promoted_once_and_master_queued(monkeypatch):
    iid = seed(curator())
    page(monkeypatch, ARTICLE)
    assert probe(iid) == 'article'
    with session() as s:
        item = s.get(ContentItem, iid)
        assert (item.type, item.author, item.publisher, item.published_at) == ('article', 'Ada', 'example.org', 1234.0)
        assert item.url == 'https://www.example.org/2026/story' and item.provider == 'web_search'
        assert item.body == 'Archived text' and item.enrichment['summary'] == 'Existing summary'
        assert s.get(MasterQueue, 1) is not None
        assert article_upgrade.next_candidate(s) is None
    assert not article_upgrade.claim(iid)


@pytest.mark.parametrize('result,final,expected', [
    ({'body': 'Generic page.', 'type': 'web', 'author': '', 'publisher': '', 'published_at': None}, None, 'not_article'),
    ({'body': '', **{k: v for k, v in ARTICLE.items() if k != 'body'}}, None, 'no_text'),
    (ARTICLE, 'https://example.org/', 'redirected'),
])
def test_unproven_pages_are_left_alone(monkeypatch, result, final, expected):
    iid = seed(curator())
    page(monkeypatch, result, final)
    assert probe(iid) == expected
    with session() as s:
        item = s.get(ContentItem, iid)
        assert item.type == 'web' and item.meta['article_probe']['result'] == expected
        assert s.get(MasterQueue, 1) is None


def test_fetch_failure_and_concurrent_type_change(monkeypatch):
    cid = curator()
    failed, changed = seed(cid), seed(cid, url='https://example.org/other')
    monkeypatch.setattr(capture.curation, 'fetch', lambda *a, **kw: (_ for _ in ()).throw(ValueError('Offline')))
    assert probe(failed) == 'unreachable'

    def fetch(url, **kw):
        with session() as s:
            item = s.get(ContentItem, changed)
            item.type = 'recipe'
            s.add(item); s.commit()
        return b'<html></html>', url, {'content_type': 'text/html'}
    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    monkeypatch.setattr(capture, 'extract_page', lambda *a: ARTICLE)
    assert probe(changed) == 'changed'
    with session() as s:
        assert s.get(ContentItem, failed).type == 'web'
        assert s.get(ContentItem, changed).type == 'recipe'
        assert s.get(MasterQueue, 1) is None


def test_new_captures_record_their_check(monkeypatch):
    iid = seed(curator())
    with session() as s:
        job = s.get(CaptureJob, (1, iid))
        job.status, job.lease = 'running', 123
        s.add(job); s.commit()
    page(monkeypatch, {'body': 'Generic page.', 'type': 'web', 'author': '', 'publisher': '', 'published_at': None})
    capture.run(1, iid, 123)
    with session() as s:
        assert s.get(ContentItem, iid).meta['article_probe']['result'] == 'not_article'
        assert article_upgrade.next_candidate(s) is None


def test_tick_uses_capture_lane_and_gap(monkeypatch):
    cid = curator()
    first, second = seed(cid), seed(cid, url='https://example.org/second')
    page(monkeypatch, ARTICLE)
    lanes = []

    def lane(key, *args):
        lanes.append(key)
        args[-2](*args[-1:])
        return True
    monkeypatch.setattr(slots, 'spawn_lane', lane)
    now = time.time()
    assert article_upgrade.probe_tick(now)
    assert not article_upgrade.probe_tick(now + 1)  # one probe per gap
    assert article_upgrade.probe_tick(now + article_upgrade.PROBE_GAP + 1)
    assert lanes == ['local-capture', 'local-capture']
    with session() as s:
        assert {s.get(ContentItem, first).type, s.get(ContentItem, second).type} == {'article'}


def test_busy_lane_releases_claim(monkeypatch):
    iid = seed(curator())
    monkeypatch.setattr(slots, 'spawn_lane', lambda *a: False)
    assert not article_upgrade.probe_tick()
    with session() as s:
        assert 'article_probe' not in s.get(ContentItem, iid).meta


def test_coverage_audit_is_profile_scoped_and_explains_low_yield():
    with session() as s:
        other = Profile(name='Other', role='adult')
        s.add(other); s.commit()
        other_id = other.id
    quiet, busy, videos = curator(), curator(name='General News'), curator(types=('video',), name='Videos')
    for n in range(3):
        seed(quiet, url=f'https://example.org/quiet/{n}')
    with session() as s:
        web = Source(profile_id=1, provider='web_search', url='')
        feed = Source(profile_id=1, provider='rss', url='https://news.example/feed')
        s.add(web); s.add(feed); s.flush()
        s.add(CuratorSource(curator_id=quiet, source_id=web.id))
        s.add(CuratorSource(curator_id=busy, source_id=feed.id))
        for n in range(2):
            item = ContentItem(url=f'https://news.example/{n}', title='News', type='news')
            s.add(item); s.flush()
            s.add(CuratorDiscovery(curator_id=busy, item_id=item.id, profile_id=1))
        s.commit()
    assert article_upgrade.audit_tick()
    assert not article_upgrade.audit_tick()  # bounded by its interval
    with session() as s:
        mine = article_upgrade.coverage(s, 1)['curators']
        assert [(r['id'], r['reason']) for r in mine] == [
            (quiet, 'No article-type sources; 3 web findings in 7 days.')]
        assert videos not in {r['id'] for r in mine}
        assert article_upgrade.coverage(s, other_id)['curators'] == []


def test_cli_dry_run_prints_aggregates_only(capsys):
    seed(curator())
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'upgrade_captured_articles.py'
    spec = importlib.util.spec_from_file_location('upgrade_captured_articles', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['mode'] == 'dry-run' and out['before']['eligible'] == 1 and 'results' not in out
    with session() as s:
        assert s.exec(__import__('sqlmodel').select(ContentItem)).first().type == 'web'


def test_many_newer_ineligible_items_do_not_starve_older_candidate():
    cid = curator()
    old = seed(cid, url='https://example.org/older')
    with session() as s:
        item = s.get(ContentItem, old)
        item.first_seen = time.time() - 86400
        s.add(item); s.commit()
    for n in range(article_upgrade.SCAN_LIMIT + 5):
        seed(cid, url=f'https://social.example/{n}', provider='mastodon_trends')
    with session() as s:
        assert article_upgrade.stats(s)['eligible'] == 1
        assert article_upgrade.next_candidate(s) == (old, 1)


def test_python_only_exclusions_page_past_first_batch(monkeypatch):
    cid = curator()
    old = seed(cid, url='https://example.org/older')
    with session() as s:
        item = s.get(ContentItem, old)
        item.first_seen = time.time() - 86400
        s.add(item); s.commit()
    newer = {seed(cid, url=f'https://example.org/new/{n}') for n in range(3)}
    monkeypatch.setattr(article_upgrade, 'SCAN_LIMIT', 2)
    real = article_upgrade._reason
    monkeypatch.setattr(article_upgrade, '_reason',
                        lambda s, row, now, allowed: 'blocked' if row[0] in newer else real(s, row, now, allowed))
    with session() as s:
        assert article_upgrade.next_candidate(s) == (old, 1)


@pytest.mark.parametrize('error,transient', [
    (capture.curation.FetchError(429), True), (capture.curation.FetchError(503), True),
    (TimeoutError('slow'), True), (capture.curation.FetchError(404), False),
])
def test_transient_failures_retry_with_backoff_then_stop(monkeypatch, error, transient):
    iid = seed(curator())
    monkeypatch.setattr(capture.curation, 'fetch', lambda *a, **kw: (_ for _ in ()).throw(error))
    results = []
    for _ in range(article_upgrade.MAX_ATTEMPTS):
        with session() as s:
            found = article_upgrade.next_candidate(s, time.time() + article_upgrade.RETRY_AFTER + 1)
        if not found:
            break
        assert article_upgrade.claim(iid, time.time() + article_upgrade.RETRY_AFTER + 1)
        results.append(article_upgrade.probe(iid))
        with session() as s:
            marker = s.get(ContentItem, iid).meta['article_probe']
            if marker['result'] == 'retry':
                assert marker['next_at'] > time.time() + article_upgrade.RETRY_AFTER - 60
                assert article_upgrade.next_candidate(s) is None  # backing off
    if transient:
        assert results == ['retry'] * (article_upgrade.MAX_ATTEMPTS - 1) + ['unreachable']
    else:
        assert results == ['unreachable']
    with session() as s:
        assert s.get(ContentItem, iid).type == 'web'


def test_ticks_that_are_not_due_do_not_write_settings(monkeypatch):
    assert article_upgrade.audit_tick(1000.0)
    writes = []
    real = article_upgrade.insert
    monkeypatch.setattr(article_upgrade, 'insert', lambda *a: writes.append(a) or real(*a))
    monkeypatch.setattr(article_upgrade, 'update', lambda *a: writes.append(a) or (_ for _ in ()).throw(AssertionError))
    assert not article_upgrade.audit_tick(1001.0)
    assert not writes


def test_cli_batch_pauses_between_reads(monkeypatch):
    cid = curator()
    for n in range(3):
        seed(cid, url=f'https://example.org/batch/{n}')
    page(monkeypatch, ARTICLE)
    sleeps = []
    monkeypatch.setattr(article_upgrade.time, 'sleep', sleeps.append)
    assert article_upgrade.run_batch(5, gap=2.0) == {'article': 3}
    assert sleeps == [2.0, 2.0]
