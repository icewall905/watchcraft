import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='bt-pin-test-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select
from bt.db import engine, init_db, session
from bt.main import app
from bt.models import Setting


@pytest.fixture
def client():
    SQLModel.metadata.drop_all(engine)
    init_db()
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(engine)
    init_db()


def protected(c):
    return c.post('/api/profiles', json={'name': 'Parents', 'pin': '4826'}).json()


def test_profiles_are_open_by_default_and_pins_are_independent_of_app_login(client):
    assert client.get('/api/auth/status').json()['required'] is False
    default = client.get('/api/profiles').json()[0]
    assert default['pin_required'] is False
    assert default['pin_locked'] is False
    p = protected(client)
    h = {'X-Profile-Id': str(p['id'])}
    assert p['pin_required'] and p['pin_locked']
    assert client.get('/api/categories', headers=h).status_code == 403
    assert client.patch(f"/api/profiles/{p['id']}", json={'pin': ''}).status_code == 403
    assert client.delete(f"/api/profiles/{p['id']}").status_code == 403
    assert client.post(f"/api/profiles/{p['id']}/unlock", json={'pin': '1111'}).status_code == 403
    assert client.post(f"/api/profiles/{p['id']}/unlock", json={'pin': '4826'}).status_code == 200
    assert client.get('/api/categories', headers=h).status_code == 200
    assert client.get('/api/profiles').json()[-1]['pin_locked'] is False
    assert client.post(f"/api/profiles/{p['id']}/lock").status_code == 200
    assert client.get('/api/categories', headers=h).status_code == 403


def test_pin_hashes_are_private_and_changing_pin_revokes_other_sessions(client):
    p = protected(client)
    pid = p['id']
    with session() as s:
        record = s.get(Setting, f'profile_pin_{pid}')
        stored_hash = record.value['hash']
        assert len(record.value['salt']) == 32
        assert stored_hash != '4826'
    assert stored_hash not in client.get('/api/settings').text
    assert stored_hash not in client.get('/api/profiles').text
    assert '4826' not in client.get('/api/profiles').text
    client.post(f'/api/profiles/{pid}/unlock', json={'pin': '4826'})
    with TestClient(app) as other:
        other.post(f'/api/profiles/{pid}/unlock', json={'pin': '4826'})
        assert client.patch(f'/api/profiles/{pid}', json={'pin': '9321'}, headers={'X-Profile-Id': str(pid)}).status_code == 200
        h = {'X-Profile-Id': str(pid)}
        assert client.get('/api/categories', headers=h).status_code == 200
        assert other.get('/api/categories', headers=h).status_code == 403
        assert other.post(f'/api/profiles/{pid}/unlock', json={'pin': '4826'}).status_code == 403
        assert other.post(f'/api/profiles/{pid}/unlock', json={'pin': '9321'}).status_code == 200
        assert other.patch(f'/api/profiles/{pid}', json={'pin': ''}).json()['pin_required'] is False
        other.post(f'/api/profiles/{pid}/lock')
        assert other.get('/api/categories', headers=h).status_code == 200


def test_invalid_pins_and_repeated_guesses(client):
    for pin in ['12', 'abcdefgh', '123456789', '１２３４']:
        assert client.post('/api/profiles', json={'name': 'Invalid', 'pin': pin}).status_code == 400
    p = protected(client)
    for _ in range(5):
        assert client.post(f"/api/profiles/{p['id']}/unlock", json={'pin': '1111'}).status_code == 403
    assert client.post(f"/api/profiles/{p['id']}/unlock", json={'pin': '4826'}).status_code == 429


def test_last_profile_can_have_a_pin_and_logout_clears_unlocks(client):
    pid = client.get('/api/profiles').json()[0]['id']
    assert client.patch(f'/api/profiles/{pid}', json={'pin': '4826'}).status_code == 200
    assert client.get('/api/categories').status_code == 200
    client.post('/api/auth/logout')
    assert client.get('/api/categories').status_code == 403
    assert client.post(f'/api/profiles/{pid}/unlock', json={'pin': '4826'}).status_code == 200


def test_parallel_wrong_pins_still_trip_the_lockout(client):
    from concurrent.futures import ThreadPoolExecutor
    admin = client.get('/api/profiles').json()[0]
    assert client.patch(f"/api/profiles/{admin['id']}", json={'pin': '4826'}).status_code == 200

    def guess(i):
        return TestClient(app).post(f"/api/profiles/{admin['id']}/unlock", json={'pin': '%04d' % i}).status_code

    with ThreadPoolExecutor(20) as ex:
        res = list(ex.map(guess, range(1, 21)))
    assert res.count(403) == 5 and res.count(429) == 15, res
