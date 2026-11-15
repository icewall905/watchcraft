"""Pinterest without scraping: pasted pin/image links are stored offline; pinimg captures are spaced."""
from io import BytesIO

from PIL import Image
from sqlmodel import select

from test_curation import client  # noqa: F401
from bt.db import session
from bt.hub import moodboards as mb, moodboard_sources
from bt.hub.models import MoodAsset, MoodPin

IMG = 'https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg'
PIN = 'https://www.pinterest.com/pin/123456789/'


def make(client):
    response = client.post('/api/hub/boards', json={'name': 'Garden', 'interests': 'wildflowers'})
    assert response.status_code == 200, response.text
    return response.json()['board']


def png():
    out = BytesIO()
    Image.new('RGB', (4, 3)).save(out, format='PNG')
    return out.getvalue()


def test_pasted_pin_link_becomes_source_dedupes_and_is_never_fetched(client, monkeypatch):
    fetched = []
    monkeypatch.setattr(mb, 'capture_image', lambda url: fetched.append(url) or (b'pixels', 'image/jpeg', 40, 30))
    b = make(client)
    url = f"/api/hub/boards/{b['id']}/images"
    first = client.post(url, json={'url': IMG, 'source_url': PIN + '?utm=x', 'title': 'wildflowers'})
    assert first.status_code == 200, first.text
    body = first.json()
    assert not body.get('existing')
    assert body['pin']['attribution'] == 'Pinterest'
    again = client.post(url, json={'url': IMG, 'source_url': 'https://pinterest.com/pin/123456789'})
    assert again.json()['existing'] is True and again.json()['pin']['id'] == body['pin']['id']
    assert fetched == [IMG, IMG]  # Only the image is captured; the pin page is never requested.
    with session() as s:
        pin = s.exec(select(MoodPin)).one()
        assert pin.source_url == PIN and pin.image_url == IMG
        assert s.get(MoodAsset, pin.id).data == b'pixels'  # Stored offline.


def test_image_only_and_non_pinterest_source_keep_image_identity(client, monkeypatch):
    monkeypatch.setattr(mb, 'capture_image', lambda url: (b'pixels', 'image/png', 4, 3))
    b = make(client)
    url = f"/api/hub/boards/{b['id']}/images"
    plain = client.post(url, json={'url': IMG}).json()
    assert plain['pin']['attribution'] == 'i.pinimg.com' and not plain.get('existing')
    # An image first added on its own is recognised when re-added with its pin link.
    assert client.post(url, json={'url': IMG, 'source_url': PIN}).json()['existing'] is True
    other = client.post(url, json={'url': 'https://example.com/a.png', 'source_url': 'https://evil.example/pin/1/'}).json()
    with session() as s:
        assert s.get(MoodPin, other['pin']['id']).source_url == 'https://example.com/a.png'


def test_pinimg_capture_goes_through_spaced_social_fetch(monkeypatch):
    raw, calls = png(), []
    monkeypatch.setattr(mb.social_fetch, 'fetch', lambda url, **kw: calls.append(('spaced', url)) or (raw, url, {}))
    monkeypatch.setattr(mb.curation, 'fetch', lambda url, **kw: calls.append(('direct', url)) or (raw, url, {}))
    assert mb.capture_image(IMG)[1:] == ('image/png', 4, 3)
    assert mb.capture_image('https://upload.wikimedia.org/x.png')[1:] == ('image/png', 4, 3)
    assert calls == [('spaced', IMG), ('direct', 'https://upload.wikimedia.org/x.png')]


def test_curator_sources_never_include_pinterest():
    cfg = {'web': True, 'reddit': True, 'bluesky': True, 'mastodon': True, 'mastodon_instance': 'mastodon.social',
           'flickr_api_key': 'k', 'tumblr_api_key': 'k', 'pinterest': True}
    assert 'pinterest' not in [name for name, _ in moodboard_sources.enabled_sources(cfg)]
