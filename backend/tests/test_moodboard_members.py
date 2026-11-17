"""Shared Mood Boards: members work on one board; child limits while a child is on it; grown-ups see and override
what the kid check hid."""
import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from test_extension import png
from test_master_api import other_profile
from test_moodboard_share import add, child_profile, judge
from test_moodboards import make
from bt.db import session
from bt.hub import moodboard_share as share
from bt.hub.models import MoodBoard, MoodBoardMember, MoodPin, MoodShareNotice


@pytest.fixture(autouse=True)
def _fresh_limits():
    for limiter in (share.PAGE_LIMIT, share.MEDIA_LIMIT, share.MISS_LIMIT):
        limiter.reset()
    yield


def members(bid):
    return f'/api/hub/mood-share/boards/{bid}/members'


def judge_reasons(monkeypatch, unsafe=(), calls=None):
    """Vision stub: images whose position (1-based, per call) is in `unsafe` fail with a reason."""
    from bt.hub import ai, kidsafe
    import bt.hub.moodboards as mb
    monkeypatch.setattr(kidsafe, '_vision', lambda: True)
    monkeypatch.setattr(mb, 'vision_image', lambda data: 'img')

    def call(system, user, schema, **kw):
        n = len(kw['images'])
        (calls if calls is not None else []).append(n)
        return {'verdicts': [{'n': i, 'kid_safe': i not in unsafe, 'reason': '' if i not in unsafe else 'Scary face'}
                             for i in range(1, n + 1)]}
    monkeypatch.setattr(ai, '_call', call)


def test_member_sees_and_adds_owner_keeps_settings(client):
    b = make(client, name='Together', enabled=False)
    add(client, b['id'], 1)
    other, oh = other_profile(client)
    assert client.get(f"/api/hub/boards/{b['id']}", headers=oh).status_code == 404
    r = client.post(members(b['id']), json={'to': other})
    assert r.status_code == 200, r.text
    assert [m['id'] for m in r.json()['members']] == [other]
    assert client.post(members(b['id']), json={'to': other}).status_code == 409
    listed = client.get('/api/hub/boards', headers=oh).json()['boards']
    assert [(x['id'], x['role'], x['owner']['name']) for x in listed] == [(b['id'], 'member', listed[0]['owner']['name'])]
    d = client.get(f"/api/hub/boards/{b['id']}", headers=oh).json()
    assert len(d['pins']) == 1 and client.get(d['pins'][0]['thumb_url'], headers=oh).status_code == 200
    pin = add(client, b['id'], 2, headers=oh)
    assert pin['profile_id'] == other
    owner_view = client.get(f"/api/hub/boards/{b['id']}").json()
    assert {p['added_by'] for p in owner_view['pins']} >= {'Other'}
    # The owner is told; the adder is not.
    notes = client.get('/api/hub/mood-share/notices').json()['notices']
    assert [(n['kind'], n['count']) for n in notes] == [('added', 1)]
    add(client, b['id'], 3, headers=oh)
    assert [(n['kind'], n['count']) for n in client.get('/api/hub/mood-share/notices').json()['notices']] == [('added', 2)]
    assert client.get('/api/hub/mood-share/notices', headers=oh).json()['notices'][0]['kind'] == 'member'
    # Owner-only: settings, delete, reshuffle, links, invites, send a copy.
    base = f"/api/hub/boards/{b['id']}"
    assert client.patch(base, headers=oh, json={'name': 'Mine'}).status_code == 404
    assert client.delete(base, headers=oh).status_code == 404
    assert client.post(f'{base}/reshuffle', headers=oh).status_code == 404
    assert client.post(f"/api/hub/mood-share/boards/{b['id']}/links", headers=oh, json={}).status_code == 404
    assert client.post(members(b['id']), headers=oh, json={'to': other}).status_code == 404
    # Members can save, review and remove images.
    assert client.patch(f"{base}/pins/{pin['id']}", headers=oh, json={'saved': True}).status_code == 200
    assert client.put(f"{base}/pins/{pin['id']}/feedback", headers=oh, json={'verdict': 'good'}).status_code == 200
    # Leaving keeps the board with its owner; the member's images stay.
    assert client.delete(members(b['id']) + f'/{other}', headers=oh).status_code == 200
    assert client.get(base, headers=oh).status_code == 404
    assert len(client.get(base).json()['pins']) == 3


def test_only_the_owner_removes_others(client):
    b = make(client, name='Two', enabled=False)
    other, oh = other_profile(client)
    client.post(members(b['id']), json={'to': other})
    with session() as s:
        from bt.models import Profile
        third = Profile(name='Third', role='adult'); s.add(third); s.commit(); tid = third.id
    client.post(members(b['id']), json={'to': tid})
    assert client.delete(members(b['id']) + f'/{tid}', headers=oh).status_code == 403
    assert client.delete(members(b['id']) + f'/{tid}').status_code == 200


def test_child_member_gets_child_limits_and_grown_ups_see_and_override(client, monkeypatch):
    b = make(client, name='Ria', enabled=False)
    for n in (1, 2, 3):
        add(client, b['id'], n)
    kid, kh = child_profile(client)
    r = client.post(members(b['id']), json={'to': kid})
    assert r.status_code == 200, r.text
    base = f"/api/hub/boards/{b['id']}"
    board = client.get(base).json()['board']
    assert board['child_friendly'] and board['strictness'] == 'strict' and board['kids']
    # Unchecked pictures stay away from the child until the sweep judged them.
    kid_view = client.get(base, headers=kh).json()
    assert kid_view['pins'] == [] and kid_view['board']['pin_count'] == 0
    assert client.patch(base, json={'child_friendly': False}).status_code == 403
    judge_reasons(monkeypatch, unsafe={2})
    assert share.kid_sweep() == 3
    grown = {p['title']: p for p in client.get(base).json()['pins']}
    assert len(grown) == 3
    bad = next(p for p in grown.values() if p['kid_state'] == 'hidden')
    assert bad['kid_reason'] == 'Scary face'
    assert client.get(base).json()['board']['kid_hidden_count'] == 1
    kid_pins = client.get(base, headers=kh).json()['pins']
    assert len(kid_pins) == 2 and bad['id'] not in {p['id'] for p in kid_pins}
    assert 'kid_reason' not in kid_pins[0] and 'kid_state' not in kid_pins[0]
    assert client.get(bad['image_url'], headers=kh).status_code == 404
    assert client.get(bad['thumb_url'], headers=kh).status_code == 404
    # Children cannot override; grown-ups can, both ways.
    kid_url = f"{base}/pins/{bad['id']}/kid"
    assert client.post(kid_url, headers=kh, json={'visible': True}).status_code == 403
    r = client.post(kid_url, json={'visible': True})
    assert r.status_code == 200 and r.json()['pin']['kid_state'] == 'override' and 'Allowed by' in r.json()['pin']['kid_reason']
    assert len(client.get(base, headers=kh).json()['pins']) == 3
    assert client.post(kid_url, json={'visible': False}).status_code == 200
    assert len(client.get(base, headers=kh).json()['pins']) == 2
    # A grown-up's failing add is kept for grown-ups and hidden from the child, with the reason.
    judge_reasons(monkeypatch, unsafe={1})
    pin = add(client, b['id'], 9)
    assert pin['kid_state'] == 'hidden' and pin['kid_reason'] == 'Scary face'
    assert pin['id'] not in {p['id'] for p in client.get(base, headers=kh).json()['pins']}
    assert client.get('/api/hub/mood-share/notices', headers=kh).json()['notices'][0]['kind'] == 'member'  # not told
    # The child's own failing add is refused; a passing one is shared with everybody.
    assert client.post(f'{base}/images', headers=kh, json={'data_base64': png(), 'title': 'x'}).status_code == 422
    judge_reasons(monkeypatch)
    mine = add(client, b['id'], 10, headers=kh)
    assert mine['id'] in {p['id'] for p in client.get(base).json()['pins']}
    # Children cannot invite or take a copy.
    assert client.post(members(b['id']), headers=kh, json={'to': kid}).status_code == 403


def test_sweep_errors_leave_pictures_unchecked(client, monkeypatch):
    b = make(client, name='Wait', enabled=False)
    add(client, b['id'], 1)
    kid, kh = child_profile(client)
    client.post(members(b['id']), json={'to': kid})
    from bt.hub import ai, kidsafe
    monkeypatch.setattr(kidsafe, '_vision', lambda: True)
    monkeypatch.setattr('bt.hub.moodboards.vision_image', lambda data: 'img')
    monkeypatch.setattr(ai, '_call', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))
    assert share.kid_sweep() == 0
    with session() as s:
        assert [p.kid_state for p in s.exec(select(MoodPin).where(MoodPin.board_id == b['id'])).all()] == ['']
    assert client.get(f"/api/hub/boards/{b['id']}", headers=kh).json()['pins'] == []


def test_childs_own_board_with_a_grown_up_member(client, monkeypatch):
    kid, kh = child_profile(client)
    judge_reasons(monkeypatch)
    b = client.post('/api/hub/boards', headers=kh, json={'name': 'Mine', 'interests': 'cats', 'enabled': False}).json()['board']
    own = add(client, b['id'], 1, headers=kh)
    with session() as s:  # the child's own curator finds are trusted
        s.add(MoodPin(board_id=b['id'], profile_id=kid, title='Found', source_url='https://e.x/found.jpg', image_url=''))
        s.commit()
    # Children cannot invite; the grown-up joins through the CLI-equivalent helper.
    with session() as s:
        board = s.get(MoodBoard, b['id'])
        share.add_member(s, board, 1, kid)
        s.commit()
    judge_reasons(monkeypatch, unsafe={1})
    grown_add = add(client, b['id'], 2)
    assert grown_add['kid_state'] == 'hidden'
    titles = {p['title'] for p in client.get(f"/api/hub/boards/{b['id']}", headers=kh).json()['pins']}
    assert titles == {own['title'], 'Found'}
    assert len(client.get(f"/api/hub/boards/{b['id']}").json()['pins']) == 3


def test_search_and_extension_follow_membership(client, monkeypatch):
    from bt.hub import moodboard_search
    b = make(client, name='Ext', enabled=False)
    add(client, b['id'], 1)
    other, oh = other_profile(client)
    client.post(members(b['id']), json={'to': other})
    with session() as s:
        assert s.exec(select(MoodPin.id).where(moodboard_search._visible(s, other))).all()
    key = client.post('/api/ext/keys', headers=oh, json={}).json()['key']
    listed = client.get('/api/ext/v1/boards', headers={'Authorization': f'Bearer {key}'}).json()['boards']
    assert [(x['id'], x['pin_count']) for x in listed] == [(b['id'], 1)]


def test_profile_deletion_and_board_deletion_clean_up(client):
    b = make(client, name='Gone', enabled=False)
    other, oh = other_profile(client)
    client.post(members(b['id']), json={'to': other})
    mine = add(client, b['id'], 1, headers=oh)
    assert client.delete(f'/api/profiles/{other}').status_code in (200, 204)
    with session() as s:
        assert s.get(MoodBoardMember, (b['id'], other)) is None
        assert s.get(MoodPin, mine['id']) is not None  # their image stays on the owner's board
    other2, oh2 = other_profile(client)
    client.post(members(b['id']), json={'to': other2})
    assert client.delete(f"/api/hub/boards/{b['id']}").status_code == 200
    with session() as s:
        assert not s.exec(select(MoodBoardMember).where(MoodBoardMember.board_id == b['id'])).all()
        assert not s.exec(select(MoodShareNotice).where(MoodShareNotice.board_id == b['id'])).all()


def test_cli_merge_folds_a_childs_copy_into_the_shared_board(client, monkeypatch):
    from bt.hub import board_members
    b = make(client, name='Shared', enabled=False)
    add(client, b['id'], 1)
    kid, kh = child_profile(client)
    judge_reasons(monkeypatch)
    copy = client.post('/api/hub/boards', headers=kh, json={'name': 'Copy', 'interests': 'x', 'enabled': False}).json()['board']
    add(client, copy['id'], 1, headers=kh)  # same source as the shared board's image: skipped
    add(client, copy['id'], 2, headers=kh)
    assert board_members.main(['add', str(b['id']), str(kid)]) == 0
    result = board_members.merge(copy['id'], b['id'])
    assert result == {'copied': 1, 'skipped': 1, 'deleted': copy['id']}
    with session() as s:
        assert s.get(MoodBoard, copy['id']) is None
    kid_pins = client.get(f"/api/hub/boards/{b['id']}", headers=kh).json()['pins']
    assert [p['title'] for p in kid_pins] == ['Image 2']  # the merged, already-checked image; the other waits for the sweep
