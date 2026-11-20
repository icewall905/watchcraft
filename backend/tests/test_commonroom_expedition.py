"""Common Room source expedition, source health and the rss/web adapters."""
import json
import re
import time
from types import SimpleNamespace

import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from bt.commonroom import api as cr_api, curate, expedition as ex, harvest, scheduler
from bt.commonroom.adapters import Adapter, Page, rss, web
from bt.commonroom.models import CRPost, CRSource, CRSourceHunt, CRSourcePost, CRThread, CRThreadSource
from bt.db import session
from bt.hub import providers, site_recipes
from bt.hub.social_fetch import Paused
from bt.models import Profile

NOW = time.time()


def post(loc, n, topic='good'):
    return {'platform': 'fake', 'community': loc, 'external_id': f'{loc}:{n}', 'url': f'https://fake.example/{loc}/{n}',
            'title': f'A {topic} post number {n}', 'body': f'{topic} body', 'author': 'a', 'score': 1, 'comments': 0,
            'published_at': NOW - n * 60}


def fake_fetch(loc, sort, cursor):
    if loc.startswith('c/few'):
        return Page([post(loc, 1)], '')
    if loc.startswith('c/old'):
        return Page([{**post(loc, i), 'published_at': NOW - 400 * 86400} for i in range(8)], '')
    topic = 'bad' if loc.startswith('c/bad') else 'good'
    return Page([post(loc, i, topic) for i in range(8)], '')


def parse(loc):
    if not loc.startswith('c/'):
        raise ValueError('Use c/name.')
    return loc


FAKE = Adapter(key='fake', label='Fake', parse=parse, fetch=fake_fetch, sorts=('new',), example='c/name')
CALLS = []


def judge_scores(user):
    rows = re.findall(r'"n": (\d+), "type": "post", "title": "([^"]*)"', user)
    return {'scores': [{'n': int(n), 'score': 90 if 'good' in title else 10, 'why': 'x'} for n, title in rows]}


@pytest.fixture
def ai(client, monkeypatch):
    state = {'named': [], 'terms': ['homelab backups'], 'queries': ['homelab forum'], 'pick': []}
    monkeypatch.setattr(ex, 'ADAPTERS', {'fake': FAKE, 'rss': rss.ADAPTER, 'web': web.ADAPTER,
                                         'reddit': Adapter(key='reddit', label='Reddit', parse=lambda x: x,
                                                           fetch=fake_fetch)})
    monkeypatch.setattr(harvest, 'ADAPTERS', {'fake': FAKE})
    monkeypatch.setattr(cr_api, 'ADAPTERS', {'fake': FAKE})
    monkeypatch.setattr(ex.ai, 'enabled', lambda: True)
    monkeypatch.setattr(curate.semantic, 'available', lambda: (False, 'off'))
    monkeypatch.setitem(providers.PROVIDERS, 'web_search', SimpleNamespace(availability=lambda: (False, 'off')))

    def call(system, user, schema, **kw):
        CALLS.append((system, user, schema))
        props = schema['properties']
        if 'scores' in props:
            return judge_scores(user)
        if 'sources' in props:
            return {'sources': [{'platform': p, 'locator': l} for p, l in state['named']], 'search_terms': state['terms']}
        if 'queries' in props:
            return {'queries': state['queries']}
        return {'pick': state['pick']}
    monkeypatch.setattr(ex.ai, '_call', call)
    monkeypatch.setattr(curate.ai, '_call', call)
    CALLS.clear()
    return state


def make(client, headers=None):
    r = client.post('/api/hub/commonroom/threads', json={'name': 'Homelab', 'interests': 'self-hosting'},
                    headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def hunt_all():
    while ex.dispatch_due():
        pass


def links(tid):
    with session() as s:
        return {(src.locator, link.origin, link.fit, link.tried) for link, src in s.exec(
            select(CRThreadSource, CRSource).join(CRSource, CRSource.id == CRThreadSource.source_id)
            .where(CRThreadSource.thread_id == tid)).all()}


def test_new_thread_queues_expedition_and_button_conflicts(client, ai):
    t = make(client)
    got = client.get(f"/api/hub/commonroom/threads/{t['id']}/sources").json()
    assert got['hunt']['status'] == 'queued' and got['hunt']['reason'] == 'new'
    assert client.post(f"/api/hub/commonroom/threads/{t['id']}/find-sources").status_code == 409
    hunt_all()
    got = client.post(f"/api/hub/commonroom/threads/{t['id']}/find-sources").json()
    assert got['hunt']['status'] == 'queued' and got['hunt']['reason'] == 'button'


def test_named_sources_are_judged_and_only_fitting_ones_followed(client, ai):
    ai['named'] = [('fake', 'c/good'), ('fake', 'c/bad'), ('fake', 'c/few'), ('fake', 'c/old'), ('fake', 'nope'),
                   ('unknown', 'x')]
    t = make(client)
    hunt_all()
    assert links(t['id']) == {('c/good', 'ai', 8, 8)}
    with session() as s:
        hunt = s.exec(select(CRSourceHunt)).one()
        thr = s.get(CRThread, t['id'])
        src = s.exec(select(CRSource).where(CRSource.locator == 'c/good')).one()
        assert hunt.status == 'done' and hunt.found == 1
        assert thr.search_terms == ['homelab backups'] and thr.next_run == 0
        assert src.created_by_thread == t['id'] and 'expedition' in src.notes
        assert len(s.exec(select(CRSourcePost).where(CRSourcePost.source_id == src.id)).all()) == 8
    steps = ' | '.join(e['message'] for e in hunt.log)
    assert 'only 1 of 8 sample posts fit' not in steps and '0 of 8 sample posts fit' in steps
    assert 'only 1 recent posts' in steps and 'only 0 recent posts' in steps
    assert harvest.search_terms(thr) == ['self-hosting', 'homelab backups']


def test_library_sources_from_other_threads_are_offered_first(client, ai):
    other = make(client)
    ai['named'] = [('fake', 'c/good')]
    hunt_all()
    with session() as s:
        src = s.exec(select(CRSource)).one()
        src.kept = 4
        s.add(src)
        s.commit()
        lib_id = src.id
    ai['named'], ai['pick'] = [], [lib_id]
    t = make(client)
    hunt_all()
    assert links(t['id']) == {('c/good', 'ai', 8, 8)} and links(other['id']) == {('c/good', 'ai', 8, 8)}
    assert any('followed by other threads' in c[0] for c in CALLS)


def test_child_thread_never_follows_reddit(client, ai):
    with session() as s:
        kid = Profile(name='Kid', role='child')
        s.add(kid)
        s.commit()
        s.refresh(kid)
        kh = {'X-Profile-Id': str(kid.id)}
    ai['named'] = [('reddit', 'c/good'), ('fake', 'c/good2')]
    t = make(client, kh)
    hunt_all()
    assert links(t['id']) == {('c/good2', 'ai', 8, 8)}


LISTING = b'''<html><head><title>Homelab Journal</title></head><body><nav><a href="/about">About this site and team</a></nav>
''' + b''.join(b'<div class="entry"><h2><a href="/posts/%d">A good post about homelab number %d</a></h2>'
               b'<p>Summary %d</p><time datetime="2026-11-1%dT10:00:00Z"></time></div>' % (i, i, i, i % 9)
               for i in range(8)) + b'<a href="/?page=2">Older</a></body></html>'

FEED = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Good Letters</title>
''' + b''.join(b'<item><title>A good letter %d</title><link>https://letters.example/p/%d</link>'
               b'<description>good text %d</description><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>' % (i, i, i)
               for i in range(6)) + b'</channel></rss>'


def test_web_rounds_follow_feeds_and_scraped_listings_and_learn(client, ai, monkeypatch):
    monkeypatch.setitem(providers.PROVIDERS, 'web_search', SimpleNamespace(availability=lambda: (True, '')))
    rounds = iter([[{'url': 'https://letters.substack.com/p/hello', 'title': 'Letters', 'summary': ''},
                    {'url': 'https://journal.example/', 'title': 'Homelab Journal', 'summary': ''},
                    {'url': 'https://www.youtube.com/watch?v=x', 'title': 'video', 'summary': ''}]])
    monkeypatch.setattr(providers, 'web_search', lambda _u, q, safe=False: next(rounds, []))
    monkeypatch.setattr(rss.social_fetch, 'fetch', lambda url, **kw: FEED)
    pages = []

    def get_html(url, via='fetch'):
        pages.append((url, via))
        return LISTING, url
    monkeypatch.setattr(site_recipes, 'get_html', get_html)
    t = make(client)
    hunt_all()
    got = {loc for loc, *_ in links(t['id'])}
    assert 'https://letters.substack.com/feed' in got
    recipe = json.loads(next(x for x in got if x.startswith('{')))
    assert recipe['url'] == 'https://journal.example/' and recipe['item'] == 'div.entry' and recipe['page_param'] == 'page'
    assert all('youtube' not in u for u, _ in pages)
    planning = [c for c in CALLS if 'queries' in c[2]['properties']]
    assert len(planning) == 3 and 'Already tried' in planning[1][1]  # later rounds see earlier results


def test_web_adapter_parses_and_pages_a_listing(monkeypatch):
    seen = []
    monkeypatch.setattr(site_recipes, 'get_html', lambda url, via='fetch': (seen.append(url), (LISTING, url))[1])
    loc = web.parse(json.dumps({'url': 'https://journal.example/', 'item': 'div.entry', 'page_param': 'page'}))
    page = web.fetch(loc, 'new', '')
    assert len(page.posts) == 8 and page.cursor == '2'
    first = page.posts[0]
    assert first['url'] == 'https://journal.example/posts/0' and first['title'].startswith('A good post')
    assert first['body'] == 'Summary 0' and first['published_at'] > 0 and first['platform'] == 'web'
    web.fetch(loc, 'new', '2')
    assert seen[-1] == 'https://journal.example/?page=2'
    with pytest.raises(ValueError):
        web.parse('http://insecure.example/')
    with pytest.raises(ValueError):
        web.parse(json.dumps({'url': 'https://x.example/', 'item': 'div:nth-child(2)'}))
    assert web.parse('https://journal.example') == '{"url":"https://journal.example/","via":"fetch"}'


def test_rss_adapter_reads_articles(monkeypatch):
    monkeypatch.setattr(rss.social_fetch, 'fetch', lambda url, **kw: FEED)
    page = rss.fetch('https://letters.example/feed', 'new', '')
    assert len(page.posts) == 6 and page.posts[0]['community'] == 'Good Letters'
    assert page.posts[0]['url'] == 'https://letters.example/p/0' and page.posts[0]['link_url'] == ''
    assert rss.fetch('https://letters.example/feed', 'new', 'x') == Page()
    with pytest.raises(ValueError):
        rss.parse('ftp://x')


def test_known_communities_from_addresses():
    k = ex.known_community
    assert k('https://www.reddit.com/r/homelab/comments/x') == ('reddit', 'r/homelab')
    assert k('https://lemmy.ml/c/selfhosted') == ('lemmy', 'selfhosted@lemmy.ml')
    assert k('https://lemmy.world/c/LocalLLaMA') == ('lemmy', 'localllama@lemmy.world')
    assert k('https://fosstodon.org/tags/homelab') == ('mastodon', '#homelab@fosstodon.org')
    assert k('https://medium.com/tag/devops') == ('rss', 'https://medium.com/feed/tag/devops')
    assert k('https://dev.to/t/python') == ('devto', 'python')
    assert k('https://example.com/blog') is None


# --- Harvest health ------------------------------------------------------------------------------------

def _source(**kw):
    with session() as s:
        t = CRThread(profile_id=1, name='x', interests='x')
        src = CRSource(platform='fake', locator='c/one', **kw)
        s.add(t)
        s.add(src)
        s.commit()
        s.add(CRThreadSource(thread_id=t.id, source_id=src.id))
        s.commit()
        return t.id, src.id


def test_exhausted_ladders_rearm_and_pauses_are_not_failures(client, monkeypatch):
    monkeypatch.setattr(harvest, 'ADAPTERS', {'fake': FAKE})
    tid, sid = _source(exhausted=True, ladder=1, exhausted_at=time.time() - harvest.REARM_S - 10)
    assert harvest.rearm(tid) == 1
    with session() as s:
        src = s.get(CRSource, sid)
        assert not src.exhausted and src.ladder == 0
    harvest._fail(src, 'fresh', Paused('pause', 900))
    harvest._fail(src, 'fresh', providers.RateLimited('slow', retry_in=60))
    with session() as s:
        src = s.get(CRSource, sid)
        assert src.failures == 0 and src.status == 'ok' and src.next_poll > time.time() + 800
    for _ in range(harvest.BROKEN_AFTER):
        harvest._fail(src, 'fresh', ValueError('dead'))
    with session() as s:
        src = s.get(CRSource, sid)
        assert src.status == 'broken' and src.next_poll > time.time() + harvest.BROKEN_RETRY_S - 60
    assert harvest.exhausted(tid)
    with session() as s:
        src.next_poll = 0
        s.add(src)
        s.commit()
    assert not harvest.exhausted(tid)  # retried daily instead of given up for good
    assert harvest.fresh(src) == 8
    with session() as s:
        src = s.get(CRSource, sid)
        assert src.status == 'ok' and src.failures == 0 and src.hits == 8


def test_keyless_reddit_backfills_one_rung_per_run(client, monkeypatch):
    calls = []
    red = Adapter(key='reddit', label='Reddit', parse=lambda x: x, sorts=('new', 'hot'),
                  fetch=lambda loc, sort, cur: (calls.append((loc, sort)), Page([], ''))[1])
    monkeypatch.setattr(harvest, 'ADAPTERS', {'reddit': red})
    monkeypatch.setattr(harvest.reddit_oauth, 'configured', lambda: False)
    with session() as s:
        t = CRThread(profile_id=1, name='x', interests='')
        s.add(t)
        s.commit()
        for name in ('r/a', 'r/b', 'r/c'):
            src = CRSource(platform='reddit', locator=name, next_poll=time.time() + 999)
            s.add(src)
            s.commit()
            s.add(CRThreadSource(thread_id=t.id, source_id=src.id))
        s.commit()
        s.refresh(t)
        s.expunge(t)
    harvest.run(t, want_more=True)
    assert len(calls) == 1


def test_unproductive_ai_sources_are_unfollowed(client):
    tid, sid = _source(hits=ex.PRUNE_HITS)
    with session() as s:
        link = s.exec(select(CRThreadSource)).one()
        link.origin, link.created_at = 'ai', time.time() - ex.PRUNE_AFTER_S - 10
        s.add(link)
        s.commit()
    assert ex.prune_links(tid) == 1
    with session() as s:
        assert s.exec(select(CRThreadSource)).one().enabled is False


def test_cross_thread_pool_offers_other_threads_posts(client):
    with session() as s:
        a, b = CRThread(profile_id=1, name='a'), CRThread(profile_id=1, name='b')
        src = CRSource(platform='fake', locator='c/a')
        p = CRPost(url='https://fake.example/x', platform='fake', title='t')
        for row in (a, b, src, p):
            s.add(row)
        s.commit()
        s.add(CRThreadSource(thread_id=a.id, source_id=src.id))
        s.add(CRSourcePost(source_id=src.id, post_id=p.id))
        s.commit()
        assert [x.id for x in curate._cross_pool(s, b, set())] == [p.id]
        assert curate._cross_pool(s, a, set()) == []  # its own sources are not "borrowed"


def test_a_platform_that_refuses_is_skipped_for_the_rest_of_the_hunt(client, ai, monkeypatch):
    from bt import curation
    reads = []

    def blocked(loc, sort, cursor):
        reads.append(loc)
        raise curation.FetchError(404, '')
    ex.ADAPTERS['reddit'] = Adapter(key='reddit', label='Reddit', parse=lambda x: x, fetch=blocked)
    ai['named'] = [('reddit', 'r/a'), ('reddit', 'r/b'), ('fake', 'c/good')]
    t = make(client)
    hunt_all()
    assert reads == ['r/a'] and links(t['id']) == {('c/good', 'ai', 8, 8)}
