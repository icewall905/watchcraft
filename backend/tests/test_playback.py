import os
import time
import tempfile

os.environ.setdefault("BT_DATA_DIR", tempfile.mkdtemp(prefix="bt-playback-test-"))

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import text
from bt.db import init_db, session
from bt.main import app
from bt.models import PlaybackTarget


def pair_screen(c, target_id, code, headers=None):
    return c.post(f'/api/playback/targets/{target_id}/pair', json={'code': code}, headers=headers)


def test_registration_pairing_delivery_ack_offline_and_receiver_credentials():
    with TestClient(app) as c:
        r = c.post('/api/playback/targets', json={'name': ' Media center '}).json()
        path = f'/api/playback/targets/{r["id"]}'
        headers = {'X-Receiver-Token': r['token']}
        assert r['name'] == 'Media center'
        assert len(r['pair_code']) == 6 and r['pair_code'].isdigit() and r['paired'] is False
        # The receiver rediscovers its pairing code without re-registering.
        assert c.post(path + '/heartbeat', json={}, headers=headers).json()['pair_code'] == r['pair_code']
        targets = c.get('/api/playback/targets').json()
        public = next(t for t in targets if t['id'] == r['id'])
        assert public['online'] and public['paired'] is False
        joined = str(targets)
        assert 'token' not in public and 'token_hash' not in public and 'pair_code' not in public
        assert r['pair_code'] not in joined  # the code is only for the registered receiver
        assert c.post(path + '/heartbeat', json={}).status_code == 403
        assert c.delete(path).status_code == 403
        # Senders must pair with the screen before the mailbox accepts commands.
        assert c.post(path + '/commands', json={'action': 'pause'}).status_code == 403
        assert pair_screen(c, r['id'], '000000').status_code == 403
        assert pair_screen(c, r['id'], r['pair_code']).status_code == 200
        assert c.get('/api/playback/targets').json()[0]['paired'] is True
        sent = c.post(path + '/commands', json={'action': 'play', 'video_id': 'abcdefghijk'})
        assert sent.status_code == 200
        command = c.post(path + '/heartbeat', json={}, headers=headers).json()['command']
        assert command['video_id'] == 'abcdefghijk'
        assert command['id'] == sent.json()['command_id']
        assert c.post(path + '/heartbeat', json={'ack': command['id'], 'video_id': 'abcdefghijk',
                                                 'paused': False, 'position_s': 12}, headers=headers).json()['command'] is None
        state = next(t for t in c.get('/api/playback/targets').json() if t['id'] == r['id'])['state']
        assert state['video_id'] == 'abcdefghijk' and not state['paused']
        # Unacknowledged commands expire, so reconnecting screens do not play stale requests.
        with session() as s:
            target = s.get(PlaybackTarget, r['id'])
            target.command = {**target.command, 'created_at': time.time() - 31}
            s.add(target)
            s.commit()
        assert c.post(path + '/heartbeat', json={}, headers=headers).json()['command'] is None
        with session() as s:
            target = s.get(PlaybackTarget, r['id'])
            target.last_seen = time.time() - 21
            s.add(target)
            s.commit()
        assert c.post(path + '/commands', json={'action': 'pause'}).status_code == 409
        assert c.post(path + '/heartbeat', json={}, headers=headers).status_code == 200
        assert c.delete(path, headers=headers).status_code == 200
        assert c.post(path + '/commands', json={'action': 'pause'}).status_code == 404


def test_pairing_scope_and_sender_profile_ownership():
    with TestClient(app) as c:
        r = c.post('/api/playback/targets', json={'name': 'Screen'}).json()
        path = f'/api/playback/targets/{r["id"]}'
        receiver_headers = {'X-Receiver-Token': r['token']}
        assert c.post('/api/playback/targets', json={'name': '  '}).status_code == 422
        # A sender profile can command only after code-gated pairing.
        assert c.post(path + '/commands', json={'action': 'pause'}).status_code == 403
        p = c.post('/api/profiles', json={'name': 'Casting family'}).json()
        sender = {'X-Profile-Id': str(p['id'])}
        assert c.post(path + '/commands', json={'action': 'pause'}, headers=sender).status_code == 403
        assert pair_screen(c, r['id'], r['pair_code'], headers=sender).status_code == 200
        cat = c.post('/api/categories', json={'name': 'Cast queue'}, headers=sender).json()
        foreign = c.post('/api/profiles', json={'name': 'Second housemate'}).json()
        foreign_cat = c.post('/api/categories', json={'name': 'Sibling queue'},
                             headers={'X-Profile-Id': str(foreign['id'])}).json()
        # The paired sender may not cast from another profile's playlist.
        assert c.post(path + '/commands', json={'action': 'play', 'video_id': 'abcdefghijk',
                                                'category_id': foreign_cat['id']}, headers=sender).status_code == 404
        assert c.post(path + '/commands', json={'action': 'play', 'video_id': 'abcdefghijk',
                                                'category_id': cat['id']}, headers=sender).status_code == 200
        cmd = c.post(path + '/heartbeat', json={}, headers=receiver_headers).json()['command']
        assert cmd['profile_id'] == p['id'] and cmd['category_id'] == cat['id']
        # Latest command wins; receiver status updates must not clobber commands.
        c.post(path + '/commands', json={'action': 'pause'}, headers=sender)
        cmd2 = c.post(path + '/heartbeat', json={'ack': cmd['id']}, headers=receiver_headers).json()['command']
        assert cmd2['id'] != cmd['id'] and cmd2['action'] == 'pause'
        # A household profile without pairing may not probe command validation either.
        assert c.post(path + '/commands', json={'action': 'pause'},
                      headers={'X-Profile-Id': str(foreign['id'])}).status_code == 403
        c.delete(path, headers=receiver_headers)


def test_registration_cap_and_send_rate_limit():
    with TestClient(app) as c:
        ids, tokens = [], []
        for n in range(10):
            r = c.post('/api/playback/targets', json={'name': f'Screen {n}'}).json()
            ids.append(r)
            tokens.append(r['token'])
        last = ids[-1]
        assert c.post('/api/playback/targets', json={'name': 'Eleventh'}).status_code == 422
        path = f'/api/playback/targets/{last["id"]}'
        assert pair_screen(c, last['id'], last['pair_code']).status_code == 200
        # Commands are rate limited per screen so a runaway client cannot flood the mailbox.
        for n in range(30):
            assert c.post(path + '/commands', json={'action': 'pause'}).status_code == 200, n
        assert c.post(path + '/commands', json={'action': 'pause'}).status_code == 429
        # Clean up so later tests start below the registration cap.
        for r, token in zip(ids, tokens):
            assert c.delete(f'/api/playback/targets/{r["id"]}',
                            headers={'X-Receiver-Token': token}).status_code == 200


def test_upgraded_screen_rediscovering_its_pairing_code():
    """A pre-pairing deployment row gains a usable code instead of breaking senders."""
    with TestClient(app) as c:
        # A row created before the pairing columns existed keeps them NULL.
        with session() as s:
            t = PlaybackTarget(id='legacyrow123456', name='Old TV',
                               token_hash=hashlib.sha256(b'legacytoken').hexdigest(), last_seen=0)
            s.add(t)
            s.commit()
            s.exec(text("UPDATE playbacktarget SET pair = NULL WHERE id = 'legacyrow123456'"))
            s.commit()
        init_db()  # the same additive pass that runs at startup repairs legacy rows
        with session() as s:
            pair = s.get(PlaybackTarget, 'legacyrow123456').pair or {}
            assert pair.get('code'), 'the migration assigned a pairing code to the legacy row'
        hb = c.post('/api/playback/targets/legacyrow123456/heartbeat', json={},
                    headers={'X-Receiver-Token': 'legacytoken'})
        assert hb.status_code == 200 and hb.json()['pair_code']
        assert pair_screen(c, 'legacyrow123456', '000000').status_code == 403
        assert pair_screen(c, 'legacyrow123456', hb.json()['pair_code']).status_code == 200
        # The receiver signalled via heartbeat, so the profile may send now the screen is live...
        assert c.post('/api/playback/targets/legacyrow123456/commands',
                      json={'action': 'pause'}).status_code == 200
        # ...and the offline window is honoured for the legacy screen as well.
        with session() as s:
            t = s.get(PlaybackTarget, 'legacyrow123456')
            t.last_seen = 0
            s.add(t)
            s.commit()
        assert c.post('/api/playback/targets/legacyrow123456/commands',
                      json={'action': 'resume'}).status_code == 409


def test_wrong_pairing_codes_lock_the_screen_for_a_while():
    with TestClient(app) as c:
        r = c.post('/api/playback/targets', json={'name': 'Guarded'}).json()
        wrong = '000000' if r['pair_code'] != '000000' else '111111'
        for n in range(5):
            assert pair_screen(c, r['id'], wrong).status_code == 403, n
        # Even the right code is refused until the window passes, so guessing all codes is impractical.
        assert pair_screen(c, r['id'], r['pair_code']).status_code == 429
        with session() as s:
            t = s.get(PlaybackTarget, r['id'])
            t.pair = {**t.pair, 'fails': [time.time() - 3600] * 5}
            s.add(t); s.commit()
        assert pair_screen(c, r['id'], r['pair_code']).status_code == 200
        assert c.delete(f'/api/playback/targets/{r["id"]}', headers={'X-Receiver-Token': r['token']}).status_code == 200


def test_child_profiles_cannot_see_pair_or_command_screens():
    """Child restrictions are enforced at the backend, not by hiding UI picks alone."""
    with TestClient(app) as c:
        # Enabling a child needs a PIN on every adult/administrator profile that exists.
        assert c.patch('/api/profiles/1', json={'pin': '2468'}, headers={'X-Profile-Id': '1'}).status_code == 200
        for p in c.get('/api/profiles').json():
            if p['id'] != 1 and p['role'] != 'child':
                assert c.patch(f'/api/profiles/{p["id"]}', json={'pin': '9999'}).status_code == 200
        c.post('/api/profiles/1/unlock', json={'pin': '2468'}, headers={'X-Profile-Id': '1'})
        child = c.post('/api/profiles', json={'name': 'Kid', 'role': 'child'}, headers={'X-Profile-Id': '1'}).json()
        headers = {'X-Profile-Id': str(child['id'])}
        r = c.post('/api/playback/targets', json={'name': 'Shared TV'}).json()
        assert c.get('/api/playback/targets', headers=headers).json() == []
        assert pair_screen(c, r['id'], r['pair_code'], headers=headers).status_code == 403
        assert c.post(f'/api/playback/targets/{r["id"]}/commands',
                      json={'action': 'pause'}, headers=headers).status_code == 403

