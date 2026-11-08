"""News For you: a different, stable mix on every visit, built from impressions."""
import time

from sqlmodel import select

from test_curation import client  # noqa: F401  (isolated DB/API fixture)
from test_master_api import add, other_profile

from bt.db import session
from bt.hub import news_mix
from bt.hub.models import ItemState, NewsImpression


def lead_ids(client, visit_at, seed='s', headers=None):
    r = client.get('/api/hub/news', params={'mode': 'foryou', 'seed': seed, 'visit_at': visit_at}, headers=headers or {})
    assert r.status_code == 200
    return [g['items'][0]['id'] for g in r.json()['groups']], r.json()


def test_each_visit_leads_with_stories_not_shown_before(client):
    words = 'alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu amber cobalt indigo scarlet'.split()
    ids = [add(title=f'{w} {w}word {w}thing', publisher=f'Pub{n}') for n, w in enumerate(words)]
    first_visit = time.time() - 7200
    first, body = lead_ids(client, first_visit, seed='a')
    assert sorted(first) == sorted(ids) and body['mode'] == 'foryou'
    # Same visit: identical order, even after recording what was shown.
    assert client.post('/api/hub/news/seen', json={'item_ids': first[:10], 'visit_at': first_visit}).json() == {'recorded': 10}
    assert lead_ids(client, first_visit, seed='a')[0] == first
    # Next visit: the ten already shown sink below every unseen story.
    second, body = lead_ids(client, time.time(), seed='b')
    assert set(second[:20]) == set(first[10:])
    assert set(second[20:]) == set(first[:10])
    shown = {g['items'][0]['id']: g for g in body['groups']}
    assert all(shown[i]['seen'] for i in first[:10]) and not any(shown[i]['seen'] for i in first[10:])
    assert body['previous_visit'] is not None


def test_new_since_last_visit_and_read_stories_leave_the_mix(client):
    old = add(title='Older seen story about trains')
    client.post('/api/hub/news/seen', json={'item_ids': [old], 'visit_at': time.time() - 3600})
    time.sleep(0.01)
    fresh = add(title='Brand new story about ferries')
    done = add(title='Finished story about buses')
    client.post(f'/api/hub/items/{done}/state', json={'read': True})
    with session() as s:
        assert s.get(ItemState, (1, done)).read_at
    order, body = lead_ids(client, time.time() + 1)
    assert done not in order and order[0] == fresh
    groups = {g['items'][0]['id']: g for g in body['groups']}
    assert groups[fresh]['new'] and not groups[old]['new'] and body['new_count'] == 1
    latest = client.get('/api/hub/news').json()
    assert done in {i['id'] for g in latest['groups'] for i in g['items']}


def test_seen_is_profile_scoped_and_validated(client):
    mine = add(title='Mine only')
    pid, headers = other_profile(client)
    theirs = add(pid=pid, title='Theirs only')
    r = client.post('/api/hub/news/seen', json={'item_ids': [mine, theirs], 'visit_at': time.time()})
    assert r.json() == {'recorded': 1}
    with session() as s:
        assert {(r.profile_id, r.item_id) for r in s.exec(select(NewsImpression)).all()} == {(1, mine)}
    assert client.get('/api/hub/news', params={'mode': 'shuffle'}).status_code == 422
    assert client.post('/api/hub/news/seen', json={'item_ids': list(range(301)), 'visit_at': 1}).status_code == 422
    assert lead_ids(client, time.time(), headers=headers)[0] == [theirs]


def test_rotation_spreads_publishers():
    now = time.time()
    groups = [{'key': f'k{n}', 'latest': now - n, 'items': [{'id': n, 'publisher': 'Big' if n < 10 else f'P{n}',
               'first_seen': now - n, 'state': {}}]} for n in range(20)]
    out = news_mix.rank(groups, seen={}, seed='x', visit_at=now, previous_visit=None)
    pubs = [g['items'][0]['publisher'] for g in out[:8]]
    assert max(pubs.count(p) for p in set(pubs)) <= 3
    assert all(pubs[i] != pubs[i + 1] or pubs[i] != 'Big' for i in range(len(pubs) - 1))


def test_lead_prefers_unread_summarised_article():
    now = time.time()
    plain = {'id': 1, 'publisher': 'A', 'published_at': now, 'state': {}}
    rich = {'id': 2, 'publisher': 'B', 'published_at': now - 60, 'state': {}, 'enrichment': {'summary': 'Grounded.'}, 'local_image': True}
    out = news_mix.rank([{'key': 's', 'latest': now, 'items': [plain, rich]}], seen={}, seed='', visit_at=now, previous_visit=None)
    assert out[0]['items'][0]['id'] == 2


def test_reading_during_a_visit_keeps_that_visits_order(client):
    words = 'amber birch cedar dahlia elm fern ginkgo hazel iris juniper'.split()
    ids = [add(title=f'{w} {w}leaf {w}root', publisher=f'Pub{n}') for n, w in enumerate(words)]
    visit = time.time() - 60
    before, _ = lead_ids(client, visit, seed='keep')
    for iid in before[:3]:
        client.post(f'/api/hub/items/{iid}/state', json={'read': True})
    assert lead_ids(client, visit, seed='keep')[0] == before
    # The next visit drops finished stories.
    later, _ = lead_ids(client, time.time() + 1, seed='next')
    assert not set(before[:3]) & set(later) and len(later) == len(ids) - 3


def test_visit_at_must_be_finite(client):
    assert client.get('/api/hub/news', params={'mode': 'foryou', 'visit_at': 'nan'}).status_code == 422
    r = client.post('/api/hub/news/seen', content='{"item_ids": [1], "visit_at": NaN}', headers={'Content-Type': 'application/json'})
    assert r.status_code == 422
