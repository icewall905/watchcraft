"""News server pagination: filters before limits, stable cursors, honest per-story counts."""
import time

from test_curation import client  # noqa: F401  (isolated DB/API fixture)
from test_master_api import add, new_story, other_profile

from bt.db import session
from bt.hub import api as hub_api
from bt.hub.models import ContentItem, CuratorDiscovery, Topic, TopicItem

WORDS = ('amber birch cedar dahlia elm fern ginkgo hazel iris juniper kelp larch maple nettle oak pine quince rowan '
         'sage teak ulmus vine willow yew zinnia').split()


def bulk(n, *, pid=1, prefix='Report', start=None, step=60.0, curator=1, provider='rss', publisher=None):
    """n independent stories (distinct titles), newest first from `start`, in one transaction."""
    start = time.time() - 60 if start is None else start
    ids = []
    with session() as s:
        for k in range(n):
            title = f'{prefix} {prefix.lower()}{k} {WORDS[k % len(WORDS)]}{k}'
            it = ContentItem(url=f'https://example.org/bulk/{prefix}/{pid}/{k}/{start}', type='news', title=title,
                             publisher=publisher or f'Outlet {k % 40}', provider=provider,
                             published_at=start - k * step, first_seen=start - k * step)
            s.add(it); s.flush()
            s.add(CuratorDiscovery(curator_id=curator, item_id=it.id, profile_id=pid, score=60))
            ids.append(it.id)
        s.commit()
    return ids


def walk(client, params, headers=None, cap=50):
    pages, cursor = [], None
    for _ in range(cap):
        r = client.get('/api/hub/news', params={**params, **({'cursor': cursor} if cursor else {})}, headers=headers or {})
        assert r.status_code == 200, r.text
        body = r.json()
        pages.append(body)
        cursor = body['next_cursor']
        if not cursor:
            return pages
    raise AssertionError('cursor never ended')


def keys(pages):
    return [g['key'] for p in pages for g in p['groups']]


def ids_of(pages):
    return {i['id'] for p in pages for g in p['groups'] for i in g['items']}


def topic(name, keywords=None, muted=False, pid=1):
    with session() as s:
        t = Topic(profile_id=pid, name=name, kind='news', keywords=keywords or [], muted=muted)
        s.add(t); s.commit()
        return t.id


def test_quiet_topic_stays_reachable_behind_many_newer_items(client):
    bulk(1250, step=30)
    quiet = add(title='Zephyr turbine pilot completes first season')
    linked = add(title='Unrelated wording entirely')
    with session() as s:
        for iid in (quiet, linked):
            it = s.get(ContentItem, iid)
            it.published_at = it.first_seen = time.time() - 5 * 86400
            s.add(it)
        tid = Topic(profile_id=1, name='Wind', kind='news', keywords=['ZEPHYR'])
        s.add(tid); s.flush()
        s.add(TopicItem(topic_id=tid.id, item_id=linked))
        s.commit()
        tid = tid.id
    for mode in ('latest', 'foryou'):
        got = client.get('/api/hub/news', params={'topic': tid, 'mode': mode, 'limit': 30}).json()
        assert {i['id'] for g in got['groups'] for i in g['items']} == {quiet, linked}
        assert got['total_stories'] == 2 and got['next_cursor'] is None
    # Without the topic the quiet story is still in the week, just further down.
    assert quiet in ids_of(walk(client, {'limit': 100}))


def test_more_than_300_stories_traverse_without_repeats_or_gaps(client):
    ids = set(bulk(340))
    latest = walk(client, {'limit': 100})
    assert len(latest) == 4 and latest[0]['total_stories'] == 340 and 'supply' in latest[0] and 'supply' not in latest[1]
    assert len(keys(latest)) == len(set(keys(latest))) == 340 and ids_of(latest) == ids
    stamps = [g['latest'] for p in latest for g in p['groups']]
    assert stamps == sorted(stamps, reverse=True)
    visit = time.time()
    foryou = walk(client, {'mode': 'foryou', 'seed': 'v1', 'visit_at': visit, 'limit': 100})
    small = walk(client, {'mode': 'foryou', 'seed': 'v1', 'visit_at': visit, 'limit': 7})
    assert keys(foryou) == keys(small) and len(set(keys(foryou))) == 340 and ids_of(foryou) == ids
    # Legacy call (no limit/cursor) is the first 300 of the same order.
    legacy = client.get('/api/hub/news', params={'mode': 'foryou', 'seed': 'v1', 'visit_at': visit}).json()
    assert [g['key'] for g in legacy['groups']] == keys(foryou)[:300] and legacy['next_cursor']
    assert client.get('/api/hub/news', params={'limit': 500}).json()['limit'] == 100


def test_latest_cursor_survives_new_arrivals_and_window_cap(client, monkeypatch):
    monkeypatch.setattr(hub_api, 'NEWS_WINDOW', 40)
    ids = set(bulk(120, start=time.time() - 3600))
    first = client.get('/api/hub/news', params={'limit': 25}).json()
    assert first['total_capped'] is True
    bulk(5, prefix='Arrival')  # newer than everything: must not shift later pages
    rest = walk(client, {'limit': 25, 'cursor': first['next_cursor']})
    seen = [g['key'] for g in first['groups']] + keys(rest)
    assert len(seen) == len(set(seen)) == 120
    assert ids_of([first, *rest]) == ids


def test_foryou_pages_are_stable_while_stories_arrive_mid_visit(client):
    bulk(40)
    visit = time.time()
    params = {'mode': 'foryou', 'seed': 'mid', 'visit_at': visit, 'limit': 10}
    first = client.get('/api/hub/news', params=params).json()
    time.sleep(0.01)
    late = set(bulk(3, prefix='Late', start=time.time(), step=-0.001))
    rest = walk(client, {**params, 'cursor': first['next_cursor']})
    order = [g['key'] for g in first['groups']] + keys(rest)
    assert len(order) == len(set(order)) == 43
    # Arrivals during the visit follow the visit's ranking instead of reshuffling it.
    assert {i['id'] for p in rest[-1:] for g in p['groups'][-3:] for i in g['items']} == late


def test_pages_never_cross_profiles(client):
    mine = set(bulk(45))
    pid, headers = other_profile(client)
    theirs = set(bulk(35, pid=pid, prefix='Theirs'))
    for mode in ('latest', 'foryou'):
        assert ids_of(walk(client, {'mode': mode, 'limit': 20})) == mine
        assert ids_of(walk(client, {'mode': mode, 'limit': 20}, headers=headers)) == theirs
    # Another profile's cursor only ever pages that profile's own stories.
    cursor = client.get('/api/hub/news', params={'limit': 20}).json()['next_cursor']
    page = client.get('/api/hub/news', params={'limit': 20, 'cursor': cursor}, headers=headers).json()
    assert {i['id'] for g in page['groups'] for i in g['items']} <= theirs


def test_muted_topic_membership_and_keywords_filter_in_sql(client):
    keep = add(title='Harbour council meets again')
    loud = add(title='Celebrity GOSSIP roundup')
    linked = add(title='Plain headline with no keyword')
    with session() as s:
        t = Topic(profile_id=1, name='Gossip', kind='news', keywords=['gossip'], muted=True)
        s.add(t); s.flush()
        s.add(TopicItem(topic_id=t.id, item_id=linked)); s.commit()
    got = client.get('/api/hub/news', params={'limit': 30}).json()
    assert {i['id'] for g in got['groups'] for i in g['items']} == {keep}
    assert loud and got['total_stories'] == 1


def test_story_counts_outlets_and_discussions_separately(client):
    st = new_story()
    a = add(story=st, title='Rocket launch succeeds', publisher='Wire')
    b = add(story=st, title='Rocket launch success', publisher='wire')
    c = add(story=st, title='Rocket launch report', publisher='Daily')
    hn = add(story=st, title='Rocket launch thread', publisher='news.ycombinator.com')
    with session() as s:
        it = s.get(ContentItem, hn)
        it.provider = 'hackernews'
        s.add(it); s.commit()
    g = client.get('/api/hub/news', params={'limit': 5}).json()['groups'][0]
    assert {i['id'] for i in g['items']} == {a, b, c, hn}
    assert (g['article_count'], g['publisher_count'], g['discussion_count']) == (4, 2, 1)


def test_cursor_is_validated_and_tied_to_its_query(client):
    bulk(12)
    cursor = client.get('/api/hub/news', params={'limit': 5}).json()['next_cursor']
    assert client.get('/api/hub/news', params={'limit': 5, 'cursor': 'garbage!'}).status_code == 422
    assert client.get('/api/hub/news', params={'limit': 5, 'cursor': cursor, 'mode': 'foryou'}).status_code == 422
    assert client.get('/api/hub/news', params={'limit': 5, 'cursor': cursor, 'days': 30}).status_code == 422
    assert client.get('/api/hub/news', params={'limit': 5, 'cursor': cursor}).status_code == 200


def test_foryou_capped_window_does_not_shift_when_stories_arrive(client, monkeypatch):
    monkeypatch.setattr(hub_api, 'NEWS_WINDOW', 40)
    original = set(bulk(60, start=time.time() - 3600))
    visit = time.time()
    params = {'mode': 'foryou', 'seed': 'cap', 'visit_at': visit, 'limit': 10}
    first = client.get('/api/hub/news', params=params).json()
    assert first['total_capped'] is True
    time.sleep(0.01)
    late = set(bulk(5, prefix='Arrival', start=time.time(), step=-0.001))
    rest = walk(client, {**params, 'cursor': first['next_cursor']})
    order = [g['key'] for g in first['groups']] + keys(rest)
    assert len(order) == len(set(order)) == 45
    shown = ids_of([first, *rest])
    # The visit's 40-item window stays whole; arrivals follow it.
    assert late <= shown and len(shown & original) == 40
    window = ids_of(walk(client, {'mode': 'foryou', 'seed': 'cap', 'visit_at': visit, 'limit': 100}))
    assert window & original == shown & original


def test_overflowing_cursor_is_rejected_not_a_crash(client):
    import base64
    import json
    bulk(3)
    tag = client.get('/api/hub/news', params={'mode': 'foryou', 'limit': 1}).json()['next_cursor']
    raw = json.loads(base64.urlsafe_b64decode(tag + '=' * (-len(tag) % 4)))
    for bad in ('{"t":"%s","o":1e999,"v":1,"s":"x"}' % raw['t'], '{"t":"%s","o":"x","v":1,"s":"x"}' % raw['t'],
                '{"t":"%s","o":1,"v":1e999,"s":"x"}' % raw['t'], '[1]'):
        cursor = base64.urlsafe_b64encode(bad.encode()).decode().rstrip('=')
        assert client.get('/api/hub/news', params={'mode': 'foryou', 'limit': 1, 'cursor': cursor}).status_code == 422


def test_topic_keywords_match_danish_and_german_case_insensitively(client):
    oe = add(title='Österreich wählt neu')
    aa = add(title='ÅRHUS havn får ny bro')
    ae = add(title='Ærø færgen sejler igen')
    strasse = add(title='Neue STRASSE in Berlin')
    other = add(title='Unrelated headline here')
    follow = topic('Nachbarn', keywords=['österreich', 'Århus', 'ærø', 'straße'])
    got = client.get('/api/hub/news', params={'topic': follow, 'limit': 30}).json()
    assert {i['id'] for g in got['groups'] for i in g['items']} == {oe, aa, ae, strasse}
    topic('Muted', keywords=['ÖSTERREICH', 'århus'], muted=True)
    got = client.get('/api/hub/news', params={'limit': 30}).json()
    assert {i['id'] for g in got['groups'] for i in g['items']} == {ae, strasse, other}


def test_capped_window_still_returns_whole_stories(client, monkeypatch):
    monkeypatch.setattr(hub_api, 'NEWS_WINDOW', 10)
    st = new_story()
    fresh = add(story=st, title='Bridge collapse inquiry opens', publisher='Wire')
    old = add(story=st, title='Bridge collapse first reports', publisher='Daily')
    with session() as s:
        it = s.get(ContentItem, old)
        it.published_at = it.first_seen = time.time() - 3 * 86400
        s.add(it); s.commit()
    bulk(20, start=time.time() - 7200)
    for mode in ('latest', 'foryou'):
        pages = walk(client, {'mode': mode, 'limit': 30})
        group = next(g for p in pages for g in p['groups'] if g['story_id'] == st)
        assert {i['id'] for i in group['items']} == {fresh, old}
        assert (group['article_count'], group['publisher_count']) == (2, 2)
        assert sum(1 for p in pages for g in p['groups'] if g['story_id'] == st) == 1
