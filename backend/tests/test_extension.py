import base64
from io import BytesIO

from PIL import Image
from sqlmodel import select

from test_curation import client  # noqa: F401
from test_moodboards import make, model_stub  # noqa: F401
from bt.db import session
from bt.hub import moodboards as mb
from bt.hub.models import ExtensionKey, MoodPin


def png():
    out = BytesIO()
    Image.new('RGB', (40, 30), 'red').save(out, 'PNG')
    return base64.b64encode(out.getvalue()).decode()


def test_extension_key_lists_boards_and_adds_images(client, monkeypatch):
    b = make(client, name='Star dresses')
    created = client.post('/api/ext/keys', json={'name': 'Firefox laptop'}).json()
    key = created['key']
    assert key.startswith('wcx_') and created['info']['hint'] == key[-4:]
    with session() as s:
        row = s.exec(select(ExtensionKey)).one()
        assert key not in row.key_hash and len(row.key_hash) == 64  # only the hash is stored
    auth = {'Authorization': f'Bearer {key}'}
    boards = client.get('/api/ext/v1/boards', headers=auth).json()
    assert [x['name'] for x in boards['boards']] == ['Star dresses']
    # Upload from the page (image needed the page's own context) keeps the original image link.
    r = client.post(f"/api/ext/v1/boards/{b['id']}/images", headers=auth, json={
        'data_base64': png(), 'title': 'Navy dress with stars', 'source_url': 'https://shop.example/img/dress.jpg',
        'page_url': 'https://shop.example/p/dress'})
    assert r.status_code == 200, r.text
    assert r.json()['board'] == 'Star dresses'
    with session() as s:
        pin = s.exec(select(MoodPin)).one()
        assert pin.source_url == 'https://shop.example/img/dress.jpg' and pin.attribution == 'shop.example' and pin.saved
    # Same image again: no duplicate.
    client.post(f"/api/ext/v1/boards/{b['id']}/images", headers=auth, json={'data_base64': png(), 'source_url': 'https://shop.example/img/dress.jpg'})
    with session() as s:
        assert len(s.exec(select(MoodPin)).all()) == 1
    # By URL: server captures it.
    monkeypatch.setattr(mb, 'capture_image', lambda url: (b'x', 'image/png', 10, 10))
    assert client.post(f"/api/ext/v1/boards/{b['id']}/images", headers=auth, json={'url': 'https://cdn.example/a.png'}).status_code == 200
    # Revoked or wrong keys are refused; key management needs a real session.
    assert client.get('/api/ext/v1/boards', headers={'Authorization': 'Bearer wcx_nope'}).status_code == 401
    assert client.get('/api/ext/v1/boards').status_code == 401
    assert client.delete(f"/api/ext/keys/{created['info']['id']}").status_code == 200
    assert client.get('/api/ext/v1/boards', headers=auth).status_code == 401


def test_extension_cannot_touch_other_profiles_boards(client):
    b = make(client)
    key = client.post('/api/ext/keys', json={}).json()['key']
    with session() as s:
        board = s.get(mb.MoodBoard, b['id'])
        board.profile_id = 999
        s.add(board)
        s.commit()
    r = client.post(f"/api/ext/v1/boards/{b['id']}/images", headers={'Authorization': f'Bearer {key}'}, json={'data_base64': png()})
    assert r.status_code == 404
