import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from test_moodboards import make, seed, model_stub, verdicts  # noqa: F401
from bt.db import session
from bt.hub import moodboard_search as ms, moodboards as mb, semantic
from bt.hub.models import MoodPin, MoodPinVector


def vec(text):
    text = text.lower()
    return [1.0 if 'star' in text else 0.0, 1.0 if 'stripe' in text else 0.0, 0.1]


@pytest.fixture
def embeds(monkeypatch):
    calls = []
    def fake(inputs, timeout_s=None):
        calls.append(inputs)
        out = []
        for item in inputs:
            if isinstance(item, str):
                out.append(vec(item))
            else:  # Image input: the stub image "shows" what its caption says.
                parts = item['content']
                assert parts[-1]['type'] == 'image_url'
                out.append(vec(' '.join(p.get('text', '') for p in parts)))
        return out
    monkeypatch.setattr(semantic, 'available', lambda: (True, ''))
    monkeypatch.setattr(semantic, 'config', lambda: {'model': 'embeddinggemma', 'base_url': 'x', 'api_key': '', 'timeout_s': 1})
    monkeypatch.setattr(semantic, 'embed', fake)
    monkeypatch.setattr(mb, 'vision_image', lambda data, edge=1024: 'data:image/jpeg;base64,cGl4ZWxz')
    return calls


def titled(bid, titles):
    seed(bid, len(titles))
    with session() as s:
        pins = s.exec(select(MoodPin).where(MoodPin.board_id == bid).order_by(MoodPin.id)).all()
        for pin, title in zip(pins, titles):
            pin.title = title
            s.add(pin)
        s.commit()
        return [p.id for p in pins]


def test_index_and_search_finds_pin_by_picture(client, embeds):
    b = make(client)
    other = make(client, name='Clothes')
    ids = titled(b['id'], ['Flower dress with stars', 'Green stripes'])
    titled(other['id'], ['Starry night sky'])
    while ms.embed_pending():
        pass
    assert all(len(call) <= ms.BATCH for call in embeds)
    result = client.get('/api/hub/boards/search', params={'q': 'flower dress with stars'}).json()
    assert result['available'] and result['indexed'] == 3 and result['pins_total'] == 3
    assert result['pins'][0]['id'] in (ids[0], ids[0] + 2)  # Both star pictures tie for first.
    assert {p['board_name'] for p in result['pins']} == {'Garden', 'Clothes'}
    assert result['pins'][-1]['id'] == ids[1]
    scoped = client.get('/api/hub/boards/search', params={'q': 'stars', 'board_id': b['id']}).json()
    assert [p['id'] for p in scoped['pins']] == [ids[0], ids[1]]
    assert scoped['pins'][0]['image_url'].endswith(f"/pins/{ids[0]}/media")


def test_search_hides_hidden_pins_and_other_profiles(client, embeds):
    b = make(client)
    ids = titled(b['id'], ['stars', 'more stars'])
    ms.embed_pending()
    client.patch(f"/api/hub/boards/{b['id']}/pins/{ids[1]}", json={'hidden': True})
    pins = client.get('/api/hub/boards/search', params={'q': 'stars'}).json()['pins']
    assert [p['id'] for p in pins] == [ids[0]]
    assert ms.search('stars', pid=999) == []


def test_unavailable_and_delete_cleans_vectors(client, embeds, monkeypatch):
    b = make(client)
    titled(b['id'], ['stars'])
    ms.embed_pending()
    with session() as s:
        assert s.exec(select(MoodPinVector)).all()
    assert client.delete(f"/api/hub/boards/{b['id']}").status_code == 200
    with session() as s:
        assert not s.exec(select(MoodPinVector)).all()
    monkeypatch.setattr(semantic, 'available', lambda: (False, 'Set an embeddings model under Settings → AI.'))
    result = client.get('/api/hub/boards/search', params={'q': 'stars'}).json()
    assert result == {**result, 'available': False, 'pins': []} and 'embeddings model' in result['reason']


def test_broken_image_marked_and_batch_failure_retried_singly(client, embeds, monkeypatch):
    b = make(client)
    ids = titled(b['id'], ['stars', 'stripes', 'bad'])
    real = semantic.embed
    def picky(inputs, timeout_s=None):
        if any('bad' in str(i) for i in inputs):
            raise ValueError('The embeddings endpoint returned HTTP 500.')
        return real(inputs)
    monkeypatch.setattr(semantic, 'embed', picky)
    assert ms.embed_pending() == 3
    with session() as s:
        dims = {v.pin_id: v.dim for v in s.exec(select(MoodPinVector)).all()}
    assert dims == {ids[0]: 3, ids[1]: 3, ids[2]: 0}
    assert ms.embed_pending() == 0  # Broken pin is not retried forever.
    monkeypatch.setattr(semantic, 'embed', lambda *a, **k: (_ for _ in ()).throw(ValueError('The embeddings endpoint is unreachable (ConnectError).')))
    titled(make(client, name='Later')['id'], ['new stars'])
    assert 'unreachable' in ms.sweep(throttled=False)['error']
    with session() as s:
        assert len(s.exec(select(MoodPinVector)).all()) == 3  # Transient failure leaves the pin pending.
