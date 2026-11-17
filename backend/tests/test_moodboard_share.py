"""Mood Board sharing: copies stay profile-owned; public links show only what was linked and revoke at once."""
import time

import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from test_extension import png
from test_master_api import other_profile
from test_moodboards import make
from bt import config
from bt.db import session
from bt.hub import moodboard_share as share
from bt.hub.models import (MoodAsset, MoodBoard, MoodFeedback, MoodPin, MoodPinNote, MoodShareLink,
                           MoodShareNotice)
from bt.models import Profile


@pytest.fixture(autouse=True)
def _fresh_limits():
    for limiter in (share.PAGE_LIMIT, share.MEDIA_LIMIT, share.MISS_LIMIT):
        limiter.reset()
    yield


def add(client, bid, n=1, headers=None, **kw):
    body = {'data_base64': png(), 'title': f'Image {n}', 'source_url': f'https://shop.example/img/{n}.jpg', **kw}
    r = client.post(f'/api/hub/boards/{bid}/images', json=body, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()['pin']


def link(client, bid, **body):
    r = client.post(f'/api/hub/mood-share/boards/{bid}/links', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def token_of(path):
    return path.removeprefix('/s/')


# ---------- to a profile

def test_share_image_lands_in_recipients_inbox_with_notice(client):
    b = make(client, name='Dresses')
    p1 = add(client, b['id'], 1)
    with session() as s:
        s.add(MoodFeedback(pin_id=p1['id'], board_id=b['id'], profile_id=1, verdict='good', note='SECRET_NOTE'))
        s.add(MoodPinNote(pin_id=p1['id'], seen='SECRET_SEEN'))
        s.commit()
    other, oh = other_profile(client)
    assert [t['id'] for t in client.get('/api/hub/mood-share/targets').json()['profiles']] == [other]
    r = client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': other, 'pin_id': p1['id']})
    assert r.status_code == 200, r.text
    assert r.json()['added'] == 1
    # Again: no duplicate, no new notice.
    assert client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': other, 'pin_id': p1['id']}).json()['added'] == 0
    boards = client.get('/api/hub/boards', headers=oh).json()['boards']
    assert [x['name'] for x in boards] == ['Shared with you']
    inbox = boards[0]
    assert not inbox['enabled']
    with session() as s:
        copies = s.exec(select(MoodPin).where(MoodPin.board_id == inbox['id'])).all()
        assert len(copies) == 1 and copies[0].profile_id == other and copies[0].id != p1['id']
        assert s.get(MoodAsset, copies[0].id).data == s.get(MoodAsset, p1['id']).data
        assert not s.exec(select(MoodFeedback).where(MoodFeedback.profile_id == other)).all()
        assert not s.get(MoodPinNote, copies[0].id)
        assert len(s.exec(select(MoodPin).where(MoodPin.board_id == b['id'])).all()) == 1  # source unchanged
    # Recipient can open the copy through their own routes; sender cannot open the recipient's board.
    assert client.get(f"/api/hub/boards/{inbox['id']}/pins/{copies[0].id}/media", headers=oh).status_code == 200
    assert client.get(f"/api/hub/boards/{inbox['id']}").status_code == 404
    notices = client.get('/api/hub/mood-share/notices', headers=oh).json()['notices']
    assert len(notices) == 1 and notices[0]['count'] == 1 and notices[0]['sender'] and notices[0]['board_id'] == inbox['id']
    assert client.get('/api/hub/counts', headers=oh).json()['boards_shared_new'] == 1
    assert client.get('/api/hub/mood-share/notices').json()['notices'] == []
    # Only the recipient can mark it seen.
    client.post('/api/hub/mood-share/notices/seen', json={'ids': [notices[0]['id']]})
    assert client.get('/api/hub/counts', headers=oh).json()['boards_shared_new'] == 1
    client.post('/api/hub/mood-share/notices/seen', json={'ids': [notices[0]['id']]}, headers=oh)
    assert client.get('/api/hub/counts', headers=oh).json()['boards_shared_new'] == 0


def test_share_whole_board_is_a_paused_copy_of_visible_images(client):
    b = make(client, name='Gardens')
    add(client, b['id'], 1)
    hidden = add(client, b['id'], 2)
    client.patch(f"/api/hub/boards/{b['id']}/pins/{hidden['id']}", json={'hidden': True})
    other, oh = other_profile(client)
    r = client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': other})
    assert r.status_code == 200 and r.json()['added'] == 1
    copy = client.get('/api/hub/boards', headers=oh).json()['boards'][0]
    assert copy['name'].startswith('Gardens · from ') and not copy['enabled'] and copy['pin_count'] == 1


def test_share_to_profile_isolation(client, monkeypatch):
    b = make(client)
    pin = add(client, b['id'])
    other, oh = other_profile(client)
    url = f"/api/hub/mood-share/boards/{b['id']}/to-profile"
    assert client.post(url, json={'to': 1}).status_code == 404  # self
    assert client.post(url, json={'to': 9999}).status_code == 404
    # Another profile cannot share my board or my pin, even onto itself or back to me.
    assert client.post(url, json={'to': 1}, headers=oh).status_code == 404
    assert client.post(url, json={'to': 1, 'pin_id': pin['id']}, headers=oh).status_code == 404
    ob = make(client, name='Theirs')  # mine; try sharing another board's pin through it
    assert client.post(f"/api/hub/mood-share/boards/{ob['id']}/to-profile", json={'to': other, 'pin_id': pin['id']}).status_code == 404
    # Profiles without Mood Board access are not targets.
    real = share.boards_allowed
    monkeypatch.setattr(share, 'boards_allowed', lambda s, pid: pid != other and real(s, pid))
    assert client.get('/api/hub/mood-share/targets').json()['profiles'] == []
    assert client.post(url, json={'to': other}).status_code == 404
    with session() as s:
        assert not s.exec(select(MoodShareNotice)).all()


# ---------- public links

def test_link_stores_hash_only_and_shows_only_the_image(client, monkeypatch):
    b = make(client, name='Private board name')
    p1 = add(client, b['id'], 1, page_url='https://shop.example/p/1')
    add(client, b['id'], 2)
    created = link(client, b['id'], pin_id=p1['id'])
    token = token_of(created['path'])
    assert token.startswith('wcs_') and created['link']['hint'] == token[-4:]
    with session() as s:
        row = s.exec(select(MoodShareLink)).one()
        assert token not in row.token_hash and len(row.token_hash) == 64
    listed = client.get('/api/hub/mood-share/links').json()['links']
    assert token not in str(listed)
    monkeypatch.setattr(config, 'AUTH_MODE', 'accounts')
    assert client.get(f"/api/hub/boards/{b['id']}").status_code == 401
    r = client.get(f'/api/public/mood/{token}')
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {'kind', 'title', 'expires_at', 'pins', 'next_cursor'}
    assert body['kind'] == 'pin' and body['title'] == 'Image 1'
    assert [set(p) for p in body['pins']] == [{'id', 'title', 'width', 'height', 'media_type'}]  # no source unless opted in
    for private in ('Private board name', 'shop.example', 'profile', 'board_id'):
        assert private not in r.text
    assert 'noindex' in r.headers['x-robots-tag'] and r.headers['cache-control'] == 'no-store'
    assert r.headers['referrer-policy'] == 'no-referrer'
    media = client.get(f"/api/public/mood/{token}/pins/{p1['id']}/media")
    assert media.status_code == 200 and media.headers['content-type'] == 'image/png'
    assert 'noindex' in media.headers['x-robots-tag']
    # Another pin on the same board is out of scope.
    with session() as s:
        others = [p.id for p in s.exec(select(MoodPin)).all() if p.id != p1['id']]
    assert client.get(f'/api/public/mood/{token}/pins/{others[0]}/media').status_code == 404


def test_board_link_lists_visible_images_and_optional_sources(client):
    b = make(client, name='Moodboard')
    add(client, b['id'], 1)
    hidden = add(client, b['id'], 2)
    client.patch(f"/api/hub/boards/{b['id']}/pins/{hidden['id']}", json={'hidden': True})
    other_board = make(client, name='Elsewhere')
    elsewhere = add(client, other_board['id'], 3)
    token = token_of(link(client, b['id'], include_source=True)['path'])
    body = client.get(f'/api/public/mood/{token}').json()
    assert body['kind'] == 'board' and body['title'] == 'Moodboard'
    assert [p['title'] for p in body['pins']] == ['Image 1']
    assert body['pins'][0]['source_url'] == 'https://shop.example/img/1.jpg'
    assert client.get(f"/api/public/mood/{token}/pins/{hidden['id']}/media").status_code == 404
    assert client.get(f"/api/public/mood/{token}/pins/{elsewhere['id']}/media").status_code == 404


def test_revocation_is_immediate_and_owner_only(client):
    b = make(client)
    p1 = add(client, b['id'])
    created = link(client, b['id'])
    token, lid = token_of(created['path']), created['link']['id']
    assert client.get(f'/api/public/mood/{token}').status_code == 200
    _, oh = other_profile(client)
    assert client.get('/api/hub/mood-share/links', headers=oh).json()['links'] == []
    assert client.delete(f'/api/hub/mood-share/links/{lid}', headers=oh).status_code == 404
    assert client.get(f'/api/public/mood/{token}').status_code == 200
    assert client.delete(f'/api/hub/mood-share/links/{lid}').status_code == 200
    assert client.get(f'/api/public/mood/{token}').status_code == 404
    assert client.get(f"/api/public/mood/{token}/pins/{p1['id']}/media").status_code == 404
    # Another profile cannot create links on my board or for my pin.
    assert client.post(f"/api/hub/mood-share/boards/{b['id']}/links", json={}, headers=oh).status_code == 404


def test_expired_link_is_gone(client, monkeypatch):
    b = make(client)
    add(client, b['id'])
    token = token_of(link(client, b['id'], expires_days=1)['path'])
    assert client.get(f'/api/public/mood/{token}').status_code == 200
    real = time.time
    monkeypatch.setattr(share.time, 'time', lambda: real() + 86400 + 5)
    assert client.get(f'/api/public/mood/{token}').status_code == 404
    assert client.get('/api/hub/mood-share/links').json()['links'][0]['expired']
    assert client.post(f"/api/hub/mood-share/boards/{b['id']}/links", json={'expires_days': 3}).status_code == 422


def test_deleted_board_id_reuse_does_not_revive_link(client):
    b = make(client)
    add(client, b['id'])
    token = token_of(link(client, b['id'])['path'])
    assert client.delete(f"/api/hub/boards/{b['id']}").status_code == 200
    with session() as s:  # Simulate SQLite handing the id to a new board later.
        s.add(MoodBoard(id=b['id'], profile_id=1, name='Brand new private', interests='x'))
        s.commit()
    assert client.get(f'/api/public/mood/{token}').status_code == 404


def test_children_cannot_create_links_and_bad_tokens_are_rate_limited(client):
    b = make(client)
    add(client, b['id'])
    token = token_of(link(client, b['id'])['path'])
    other, oh = other_profile(client)
    with session() as s:
        p = s.get(Profile, other)
        p.role = 'child'
        s.add(p)
        s.commit()
    assert client.post(f"/api/hub/mood-share/boards/{b['id']}/links", json={}, headers=oh).status_code == 403
    for _ in range(20):
        assert client.get('/api/public/mood/wcs_' + 'x' * 43).status_code == 404
    assert client.get('/api/public/mood/wcs_' + 'y' * 43).status_code == 429
    # A blocked client cannot use a valid link either until the window passes.
    assert client.get(f'/api/public/mood/{token}').status_code == 429
    share.MISS_LIMIT.reset()
    for _ in range(share.PAGE_LIMIT.limit - 1):
        client.get(f'/api/public/mood/{token}')
    assert client.get(f'/api/public/mood/{token}').status_code == 429


def test_public_routes_have_no_listing(client):
    for path in ('/api/public/mood', '/api/public/mood/', '/api/public/'):
        assert client.get(path).status_code in (404, 405)


def test_deleted_boards_drop_their_links_and_notices(client):
    b = make(client)
    pin = add(client, b['id'])
    keep = make(client, name='Keep')
    add(client, keep['id'], 5)
    link(client, b['id'], pin_id=pin['id'])
    link(client, b['id'])
    link(client, keep['id'])
    other, oh = other_profile(client)
    client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': other})
    copy = client.get('/api/hub/boards', headers=oh).json()['boards'][0]
    assert client.get('/api/hub/counts', headers=oh).json()['boards_shared_new'] == 1
    client.delete(f"/api/hub/boards/{b['id']}")
    assert [x['board_name'] for x in client.get('/api/hub/mood-share/links').json()['links']] == ['Keep']
    with session() as s:
        assert len(s.exec(select(MoodShareLink)).all()) == 1
    client.delete(f"/api/hub/boards/{copy['id']}", headers=oh)
    assert client.get('/api/hub/counts', headers=oh).json()['boards_shared_new'] == 0


def test_sharing_does_not_unhide_what_the_recipient_removed(client):
    b = make(client)
    pin = add(client, b['id'])
    other, oh = other_profile(client)
    client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': other, 'pin_id': pin['id']})
    inbox = client.get('/api/hub/boards', headers=oh).json()['boards'][0]
    with session() as s:
        copy = s.exec(select(MoodPin).where(MoodPin.board_id == inbox['id'])).one()
    client.patch(f"/api/hub/boards/{inbox['id']}/pins/{copy.id}", json={'hidden': True}, headers=oh)
    assert client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': other, 'pin_id': pin['id']}).json()['added'] == 0
    with session() as s:
        assert s.get(MoodPin, copy.id).hidden


def test_limiter_is_bounded_and_groups_ipv6(monkeypatch):
    lim = share._Limiter(2, 60)
    monkeypatch.setattr(share._Limiter, 'MAX_CLIENTS', 3)
    for n in range(10):
        assert lim.hit(f'10.0.0.{n}')
    assert len(lim.hits) == 3
    assert lim.hit('a') and lim.hit('a') and not lim.hit('a') and lim.blocked('a')
    class R:
        def __init__(self, host):
            self.client = type('C', (), {'host': host})()
    assert share._client(R('2001:db8:1:2:aaaa::1')) == share._client(R('2001:db8:1:2:bbbb::9'))
    assert share._client(R('2001:db8:1:3::1')) != share._client(R('2001:db8:1:2::1'))
    assert share._client(R('192.0.2.7')) == '192.0.2.7'
    assert share._client(R('::ffff:192.0.2.7')) == '192.0.2.7'


# ---------- to a child profile

def child_profile(client):
    """A child in the household means grown-ups need a PIN: give the test admin one and unlock it."""
    from test_access import protect_admin
    admin = protect_admin(client)
    assert client.post(f"/api/profiles/{admin['id']}/unlock", json={'pin': '4826'}).status_code == 200
    with session() as s:
        kid = Profile(name='Kid', role='child')
        s.add(kid); s.commit()
        return kid.id, {'X-Profile-Id': str(kid.id)}


def judge(monkeypatch, safe_titles=None, calls=None):
    """Stub the vision model: 'safe' unless the call is told otherwise; records batches."""
    from bt.hub import ai, kidsafe
    monkeypatch.setattr(kidsafe, '_vision', lambda: True)
    monkeypatch.setattr(share, 'vision_image', lambda data: 'img', raising=False)
    import bt.hub.moodboards as mb
    monkeypatch.setattr(mb, 'vision_image', lambda data: 'img')
    def call(system, user, schema, **kw):
        assert 'child-safety' in system and kw.get('images')
        n = len(kw['images'])
        (calls if calls is not None else []).append(n)
        verdicts = safe_titles(n) if safe_titles else [True] * n
        return {'verdicts': [{'n': i + 1, 'kid_safe': v, 'reason': ''} for i, v in enumerate(verdicts)]}
    monkeypatch.setattr(ai, '_call', call)


def test_child_profiles_are_share_targets_and_get_a_child_friendly_inbox(client, monkeypatch):
    b = make(client, name='Cats')
    pin = add(client, b['id'])
    kid, kh = child_profile(client)
    calls = []
    judge(monkeypatch, calls=calls)
    targets = client.get('/api/hub/mood-share/targets').json()['profiles']
    assert [(t['id'], t['child']) for t in targets] == [(kid, True)]
    r = client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': kid, 'pin_id': pin['id']})
    assert r.status_code == 200, r.text
    assert r.json()['added'] == 1 and r.json()['held'] == 0 and calls == [1]
    boards = client.get('/api/hub/boards', headers=kh).json()['boards']
    assert [x['name'] for x in boards] == ['Shared with you']
    with session() as s:
        inbox = s.get(MoodBoard, boards[0]['id'])
        assert inbox.child_friendly and inbox.strictness == 'strict' and inbox.profile_id == kid
    notices = client.get('/api/hub/mood-share/notices', headers=kh).json()['notices']
    assert len(notices) == 1
    assert client.get('/api/hub/counts', headers=kh).json()['boards_shared_new'] == 1
    assert client.post('/api/hub/mood-share/notices/seen', json={'ids': [notices[0]['id']]}, headers=kh).status_code == 200


def test_images_that_fail_the_kid_check_are_held_back(client, monkeypatch):
    b = make(client, name='Mixed')
    for n in range(1, 4):
        add(client, b['id'], n)
    kid, kh = child_profile(client)
    judge(monkeypatch, safe_titles=lambda n: [True, False, True][:n])
    r = client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': kid})
    assert r.status_code == 200, r.text
    assert r.json()['added'] == 2 and r.json()['held'] == 1
    copy = client.get('/api/hub/boards', headers=kh).json()['boards'][0]
    assert copy['pin_count'] == 2
    with session() as s:
        assert s.get(MoodBoard, copy['id']).child_friendly
    # A single rejected image sends nothing.
    judge(monkeypatch, safe_titles=lambda n: [False] * n)
    pin = add(client, b['id'], 9)
    r = client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': kid, 'pin_id': pin['id']})
    assert r.status_code == 409 and 'kid-safety' in r.json()['detail']


def test_child_share_fails_closed_without_a_vision_model_or_on_errors(client, monkeypatch):
    from bt.hub import ai, kidsafe
    b = make(client)
    pin = add(client, b['id'])
    kid, _ = child_profile(client)
    url = f"/api/hub/mood-share/boards/{b['id']}/to-profile"
    monkeypatch.setattr(kidsafe, '_vision', lambda: False)
    assert client.post(url, json={'to': kid, 'pin_id': pin['id']}).status_code == 409
    judge(monkeypatch)
    monkeypatch.setattr(ai, '_call', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('model down')))
    assert client.post(url, json={'to': kid, 'pin_id': pin['id']}).status_code == 409
    with session() as s:
        assert not s.exec(select(MoodPin).where(MoodPin.profile_id == kid)).all()
        assert not s.exec(select(MoodShareNotice)).all()


def test_child_friendly_board_images_go_to_a_child_without_another_check(client, monkeypatch):
    from bt.hub import ai
    b = make(client, name='Kid board', child_friendly=True)
    pin = add(client, b['id'])
    kid, _ = child_profile(client)
    monkeypatch.setattr(ai, '_call', lambda *a, **k: pytest.fail('no model call expected'))
    r = client.post(f"/api/hub/mood-share/boards/{b['id']}/to-profile", json={'to': kid, 'pin_id': pin['id']})
    assert r.status_code == 200 and r.json()['added'] == 1
