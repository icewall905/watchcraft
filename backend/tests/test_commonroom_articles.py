"""Whole articles behind Commons posts: judged in full, real headlines, summaries of the complete text."""
import time

from test_curation import client  # noqa: F401
from test_commonroom import fake_platform, run_all, thread  # noqa: F401
from test_commonroom_fun import _feed, _pick
from bt.commonroom import articles, curate
from bt.commonroom.models import CRPost
from bt.db import session

ARTICLE = ('<html><head><meta property="og:title" content="Are Safety Coffins Real?"></head><body><article>'
           '<p>Weird Medical History · Engineering</p><h1>The Coffin That Waved a Flag</h1>'
           + ''.join(f'<p>Paragraph {n} about the safety coffin, its patent, the bell and the flag.</p>'
                     for n in range(40)) + '</article></body></html>').encode()
LISTING = 'Illustration Engineering The Coffin That Waved a Flag In 1896, Count Michel received a patent'


def test_better_title_strips_labels_and_teaser_but_keeps_normal_titles():
    text = 'Weird Medical History · Engineering\n\nThe Coffin That Waved a Flag\n\nIn 1896...'
    assert articles.better_title(LISTING, 'Are Safety Coffins Real?', text) == 'The Coffin That Waved a Flag'
    assert articles.better_title('Plain headline here', 'Plain headline here | Site', '') == 'Plain headline here'


def test_teaser_posts_are_judged_on_the_whole_article(client, monkeypatch):
    t = thread(client)
    pid = _pick(t, 'https://site.example/coffin', platform='web')
    with session() as s:
        p = s.get(CRPost, pid)
        p.title, p.body = LISTING, 'In 1896, Count Michel received a patent...'
        s.add(p)
        s.commit()
        p = s.get(CRPost, pid)
        s.expunge(p)
    monkeypatch.setattr(articles.curation, 'fetch', lambda url, **k: ARTICLE)
    assert articles.complete([p]) == 1
    assert p.title == 'The Coffin That Waved a Flag' and 'Paragraph 39' in p.body
    with session() as s:
        row = s.get(CRPost, pid)
        assert row.meta['teaser'].startswith('In 1896') and 'Paragraph 39' in row.body
    assert articles.complete([p]) == 0  # once


def test_kept_posts_get_a_summary_of_the_complete_article(client, monkeypatch):
    t = thread(client)
    own = _pick(t, 'https://site.example/coffin', platform='web')
    link = _pick(t, 'https://forum.example/t/1', link='https://blog.example/post')
    video = _pick(t, 'https://forum.example/t/2', link='https://www.youtube.com/watch?v=abcdefghijk')
    monkeypatch.setattr(articles.curation, 'fetch', lambda url, **k: ARTICLE)
    monkeypatch.setattr(articles.ai, 'enabled', lambda: True)
    seen = []

    def summarize(rows):
        seen.extend(rows)
        return {n: f'Summary {n} of {len(r["body"])} chars' for n, r in enumerate(rows)}
    monkeypatch.setattr(articles.ai, 'summarize_items', summarize)
    articles.sweep(limit=5)
    assert len(seen) == 2 and all('Paragraph 39' in r['body'] for r in seen)  # the whole text, not a teaser
    with session() as s:
        o, l, v = s.get(CRPost, own), s.get(CRPost, link), s.get(CRPost, video)
        assert o.meta['summary'].startswith('Summary') and 'Paragraph 39' in o.body
        assert l.meta['summary'] and 'Paragraph 39' in l.meta['article']['text'] and 'Paragraph' not in l.body
        assert 'summary' not in (v.meta or {})  # not an article
    cards = {c['post_id']: c for c in _feed(client)}
    assert cards[own]['summary'].startswith('Summary')
    detail = client.get(f'/api/hub/commonroom/posts/{link}').json()
    assert detail['article']['url'] == 'https://blog.example/post'
    assert articles.sweep(limit=5) == 0  # done once


def test_failed_summary_is_retried_a_few_times(client, monkeypatch):
    t = thread(client)
    own = _pick(t, 'https://site.example/coffin2', platform='web')
    monkeypatch.setattr(articles.curation, 'fetch', lambda url, **k: ARTICLE)
    monkeypatch.setattr(articles.ai, 'enabled', lambda: True)
    monkeypatch.setattr(articles.ai, 'summarize_items', lambda rows: {})
    for _ in range(5):
        articles.sweep()
    with session() as s:
        assert s.get(CRPost, own).meta['summary_tries'] == 3


def test_curation_run_completes_teasers_before_judging(client, monkeypatch):
    from test_commonroom import FAKE, Page, post
    monkeypatch.setattr(FAKE, 'fetch', lambda loc, sort, cur: Page(
        [{**post(1), 'platform': 'web', 'url': 'https://site.example/a', 'title': LISTING, 'body': 'teaser'}]
        if sort == 'new' else [], ''))
    monkeypatch.setattr(curate.ai, 'enabled', lambda: True)
    judged = []

    def judge(system, user, schema, **k):
        judged.append(user)
        return {'scores': [{'n': 1, 'score': 90, 'fun': 10, 'kind': 'deepdive', 'why': 'ok'}]}
    monkeypatch.setattr(curate.ai, '_call', judge)
    monkeypatch.setattr(articles.curation, 'fetch', lambda url, **k: ARTICLE)
    t = thread(client)
    with session() as s:
        from bt.commonroom.models import CRThread
        thr = s.get(CRThread, t['id'])
        thr.discovered_at, thr.want_more = time.time(), False
        s.add(thr)
        s.commit()
    run_all()
    assert judged and 'Paragraph 25' in judged[0] and 'The Coffin That Waved a Flag' in judged[0]
