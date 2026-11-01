"""Semantic search: optional embeddings, per-model vectors, profile visibility."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-semantic-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, save_setting, session
from bt.hub import semantic
from bt.hub.models import ContentItem, CuratorDiscovery, ItemState
from bt.main import app

H = {'X-Profile-Id': '1'}


@pytest.fixture(autouse=True)
def _schema():
    init_db()


@pytest.fixture
def client():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield TestClient(app, raise_server_exceptions=True)
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def _model(name='test-embed'):
    save_setting('llm', {**db.get_settings()['llm'], 'embed_model': name})


def _item(url, title, pid=1):
    with session() as s:
        it = ContentItem(url=url, type='news', title=title, summary=title)
        s.add(it); s.flush()
        s.add(CuratorDiscovery(curator_id=1, item_id=it.id, profile_id=pid, query='q', score=50))
        s.commit()
        return it.id


def test_semantic_search_is_off_until_an_embeddings_model_is_set(client):
    ok, reason = semantic.available()
    assert not ok and 'embeddings model' in reason
    with pytest.raises(ValueError):
        semantic.embed(['anything'])
    got = client.get('/api/hub/search/semantic', params={'q': 'anything'}, headers=H).json()
    assert got['available'] is False and got['items'] == [] and got['reason']


def test_embed_pending_and_search_rank_by_similarity(client, monkeypatch):
    _model()
    # A tiny deterministic space: "rust" points one way, "gossip" the other.
    def fake_embed(texts):
        return [[1.0, 0.0] if 'rust' in t.lower() else [0.0, 1.0] for t in texts]

    monkeypatch.setattr(semantic, 'embed', fake_embed)
    rust = _item('https://example.org/rust', 'Rust async runtimes')
    gossip = _item('https://example.org/gossip', 'Celebrity gossip roundup')
    assert semantic.embed_pending() == 2
    assert semantic.embed_pending() == 0  # already indexed
    ranked = semantic.search('rust', 1)
    assert [i for i, _ in ranked] == [rust, gossip]
    assert ranked[0][1] > ranked[1][1]
    got = client.get('/api/hub/search/semantic', params={'q': 'rust'}, headers=H).json()
    assert got['available'] is True and got['items'][0]['id'] == rust
    assert got['items'][0]['similarity'] is not None


def test_semantic_search_only_returns_items_the_profile_can_see(client, monkeypatch):
    _model()
    monkeypatch.setattr(semantic, 'embed', lambda texts: [[1.0, 0.0] for _ in texts])
    mine = _item('https://example.org/mine', 'rust mine', pid=1)
    theirs = _item('https://example.org/theirs', 'rust theirs', pid=2)
    semantic.embed_pending()
    assert [i for i, _ in semantic.search('rust', 1)] == [mine]
    assert [i for i, _ in semantic.search('rust', 2)] == [theirs]


def test_switching_models_re_embeds_instead_of_mixing_spaces(client, monkeypatch):
    _model('model-a')
    monkeypatch.setattr(semantic, 'embed', lambda texts: [[1.0, 0.0] for _ in texts])
    _item('https://example.org/a', 'rust a')
    assert semantic.embed_pending() == 1
    _model('model-b')
    assert semantic.embed_pending() == 1  # the new model has no vectors yet
    assert semantic.status()['indexed'] == 1


def test_an_unreachable_endpoint_reports_a_reason(client, monkeypatch):
    _model()
    import httpx

    def boom(*_a, **_k):
        raise httpx.ConnectError('down')

    monkeypatch.setattr(semantic.httpx, 'post', boom)
    with pytest.raises(ValueError, match='unreachable'):
        semantic.embed(['x'])
    _item('https://example.org/pending', 'needs a vector')
    assert 'unreachable' in semantic.sweep(throttled=False)['error']
