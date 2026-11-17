"""Parental controls: per child switches, locked down by default, enforced by the server."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-parental-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import db, parental
from bt.db import init_db, session
from bt.main import app
from bt.models import Profile

pytestmark = pytest.mark.parental_defaults


@pytest.fixture
def client():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield TestClient(app)
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def h(p):
    return {'X-Profile-Id': str(p['id'])}


@pytest.fixture
def family(client):
    admin = client.get('/api/profiles').json()[0]
    assert client.patch(f"/api/profiles/{admin['id']}", json={'pin': '4826'}).status_code == 200
    kid = client.post('/api/profiles', json={'name': 'Kid', 'role': 'child'}, headers=h(admin)).json()
    return admin, kid


def test_children_start_locked_down_grown_ups_have_everything(client, family):
    admin, kid = family
    assert kid['controls'] == {k: k == 'screen' for k in parental.KEYS}
    assert all(client.get('/api/profiles', headers=h(admin)).json()[0]['controls'].values())
    for path in ('/api/search?q=cats', '/api/shorts', '/api/hub/boards', '/api/hub/commonroom/feed', '/api/hub/today',
                 '/api/hub/items', '/api/hub/collections'):
        r = client.get(path, headers=h(kid))
        assert r.status_code == 403 and 'switched this off' in r.text, path
    assert client.post('/api/categories', json={'name': 'Mine'}, headers=h(kid)).status_code == 403
    assert client.get('/api/feed/home', headers=h(kid)).status_code != 403
    assert client.get('/api/hub/collections', headers=h(admin)).status_code == 200  # grown-ups are not gated
    assert parental.needed('GET', '/search') == ('yt_search',)
    assert parental.needed('GET', '/video/abc') is None


def test_youtube_lists_without_live_search_and_boards_without_commons(client, family):
    admin, kid = family
    r = client.put(f"/api/profiles/{kid['id']}/parental", headers=h(admin),
                   json={'controls': {'playlists_edit': True, 'boards': True}})
    assert r.status_code == 200 and r.json()['controls']['boards'] and not r.json()['controls']['commons']
    assert client.post('/api/categories', json={'name': 'Mine'}, headers=h(kid)).status_code == 200
    assert client.get('/api/search?q=cats', headers=h(kid)).status_code == 403
    assert client.get('/api/hub/boards', headers=h(kid)).status_code == 200
    assert client.get('/api/hub/boards/search?q=cat', headers=h(kid)).status_code == 403  # board_search is off
    assert client.get('/api/hub/commonroom/feed', headers=h(kid)).status_code == 403


def test_only_grown_ups_change_switches_and_input_is_checked(client, family):
    admin, kid = family
    url = f"/api/profiles/{kid['id']}/parental"
    assert client.put(url, headers=h(kid), json={'controls': {'yt_search': True}}).status_code == 403
    assert client.put(url, headers=h(admin), json={'controls': {'nope': True}}).status_code == 400
    assert client.put(url, headers=h(admin), json={'controls': {'yt_search': 'yes'}}).status_code == 400
    assert client.put(f"/api/profiles/{admin['id']}/parental", headers=h(admin), json={'controls': {}}).status_code == 400
    # A child's own profile edit cannot switch anything on.
    client.patch(f"/api/profiles/{kid['id']}", headers=h(kid), json={'parental': {'yt_search': True}})
    with session() as s:
        assert not parental.allowed(s.get(Profile, kid['id']), 'yt_search')
    assert client.get('/api/parental/features').json()[0]['key'] == 'screen'
