"""Semantic search: optional embeddings, per-model vectors, profile visibility."""
import array
import json
import os
import sqlite3
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-semantic-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, save_setting, session
from bt.hub import migrate, semantic
from bt.hub.models import ContentItem, CuratorDiscovery, ItemState, ItemVector
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


def test_the_float32_index_ranks_the_same_as_the_json_one(client, monkeypatch):
    """The encoding changed, not the ranking. Assert on ids: float32 shifts the scores."""
    _model()
    space = {'rust': [1.0, 0.0, 0.0], 'gossip': [0.0, 1.0, 0.0], 'both': [0.7, 0.7, 0.0]}

    def fake_embed(texts):
        return [space.get(t.split('\n')[0].split()[0].lower(), [0.0, 0.0, 1.0]) for t in texts]

    monkeypatch.setattr(semantic, 'embed', fake_embed)
    rust = _item('https://example.org/rust', 'rust async')
    gossip = _item('https://example.org/gossip', 'gossip roundup')
    both = _item('https://example.org/both', 'both of them')
    assert semantic.embed_pending() == 3
    assert [i for i, _ in semantic.search('rust', 1)] == [rust, both, gossip]
    st = semantic.status()
    assert st['indexed'] == 3 and st['items'] == 3


def test_a_vector_of_the_wrong_length_is_skipped_not_compared(client, monkeypatch):
    _model()
    monkeypatch.setattr(semantic, 'embed', lambda texts: [[1.0, 0.0] for _ in texts])
    good = _item('https://example.org/good', 'rust good')
    wrong = _item('https://example.org/wrong', 'rust wrong')
    semantic.embed_pending()
    with session() as s:  # a 3-dim vector filed under a 2-dim model's key
        s.merge(ItemVector(item_id=wrong, model='test-embed', dim=3, vector=semantic._pack([1.0, 0.0, 0.0])))
        s.commit()
    assert [i for i, _ in semantic.search('rust', 1)] == [good]


def test_a_truncated_or_malformed_vector_is_skipped_not_raised(client, monkeypatch):
    _model()
    monkeypatch.setattr(semantic, 'embed', lambda texts: [[1.0, 0.0] for _ in texts])
    good = _item('https://example.org/good', 'rust good')
    short = _item('https://example.org/short', 'rust short')
    ragged = _item('https://example.org/ragged', 'rust ragged')
    semantic.embed_pending()
    with session() as s:
        s.merge(ItemVector(item_id=short, model='test-embed', dim=2, vector=b'\x00\x00\x80'))
        # dim unset, so the length check cannot catch it and frombytes must.
        s.merge(ItemVector(item_id=ragged, model='test-embed', dim=0, vector=b'not a vector!'))
        s.commit()
    assert [i for i, _ in semantic.search('rust', 1)] == [good]


def test_a_json_era_row_is_not_compared_with_a_float32_row(client, monkeypatch):
    """Before the conversion a vector was JSON text. It must be skipped, not scored as garbage."""
    _model()
    monkeypatch.setattr(semantic, 'embed', lambda texts: [[1.0, 0.0] for _ in texts])
    packed = _item('https://example.org/packed', 'rust packed')
    legacy = _item('https://example.org/legacy', 'rust legacy')
    semantic.embed_pending()
    # Written as raw SQL: the ORM's BLOB binding refuses text, which is why the conversion
    # in migrate._v3 works on the driver connection too.
    with db.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE itemvector SET vector = '[1.0, 0.0]' WHERE item_id = ? AND model = 'test-embed'",
                             (legacy,))
    assert [i for i, _ in semantic.search('rust', 1)] == [packed]


def test_converting_a_json_index_preserves_the_ranking(client, monkeypatch):
    """The acceptance criterion: the same ids in the same order after the repack."""
    _model()
    space = {'rust': [1.0, 0.0, 0.0], 'gossip': [0.0, 1.0, 0.0], 'both': [0.7, 0.7, 0.0]}

    def fake_embed(texts):
        return [space.get(t.split('\n')[0].split()[0].lower(), [0.0, 0.0, 1.0]) for t in texts]

    monkeypatch.setattr(semantic, 'embed', fake_embed)
    rust = _item('https://example.org/rust', 'rust async')
    gossip = _item('https://example.org/gossip', 'gossip roundup')
    both = _item('https://example.org/both', 'both of them')
    semantic.embed_pending()
    expected = [i for i, _ in semantic.search('rust', 1)]
    assert expected == [rust, both, gossip]
    # Rewind to the JSON era: the same numbers in the old encoding, at the old schema version.
    with db.engine.begin() as conn:
        for iid, vec in ((rust, space['rust']), (gossip, space['gossip']), (both, space['both'])):
            conn.exec_driver_sql('UPDATE itemvector SET vector = ? WHERE item_id = ? AND model = ?',
                                 (json.dumps(vec), iid, 'test-embed'))
        conn.exec_driver_sql("""UPDATE setting SET value = '{"version": 2}'
                                WHERE key = 'watchcraft_schema_version'""")
        migrate.migrate(conn)
    assert [i for i, _ in semantic.search('rust', 1)] == expected


def test_v2_vectors_are_repacked_as_float32(tmp_path):
    """A JSON-era vector becomes a BLOB with the same numbers; an unparseable row is dropped."""
    from sqlalchemy import create_engine
    from sqlmodel import Session, select
    db_file = tmp_path / 'v2.db'
    con = sqlite3.connect(db_file)
    con.executescript('''
      CREATE TABLE setting (key VARCHAR PRIMARY KEY, value JSON);
      INSERT INTO setting VALUES ('watchcraft_schema_version', '{"version": 2}');
      CREATE TABLE itemvector (item_id INTEGER, model VARCHAR, dim INTEGER, vector TEXT, created_at FLOAT,
        PRIMARY KEY (item_id, model));
      INSERT INTO itemvector VALUES (1, 'm', 2, '[1.0, 0.0]', 0);
      INSERT INTO itemvector VALUES (2, 'm', 2, 'not json', 0);
    ''')
    con.commit(); con.close()
    eng = create_engine(f'sqlite:///{db_file}')
    with eng.begin() as conn:
        SQLModel.metadata.create_all(conn)
        migrate.migrate(conn)
        migrate.migrate(conn)  # idempotent
    with Session(eng) as s:
        rows = {r.item_id: r for r in s.exec(select(ItemVector)).all()}
    assert set(rows) == {1}  # the unparseable row is dropped so the sweep re-embeds it
    assert isinstance(rows[1].vector, bytes) and rows[1].dim == 2
    assert list(array.array('f', rows[1].vector)) == [1.0, 0.0]


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


def test_embeddings_use_their_own_server_and_key_or_fall_back_to_the_judge(client):
    base = {**db.get_settings()['llm'], 'base_url': 'http://judge:8080/v1/', 'api_key': 'judge-key', 'embed_model': 'e'}
    save_setting('llm', {**base, 'embed_base_url': '', 'embed_api_key': ''})
    cfg = semantic.config()
    assert cfg['base_url'] == 'http://judge:8080/v1' and cfg['api_key'] == 'judge-key'
    save_setting('llm', {**base, 'embed_base_url': 'http://embed:8082/v1/', 'embed_api_key': ''})
    cfg = semantic.config()
    # A separate server never receives the judge's key.
    assert cfg['base_url'] == 'http://embed:8082/v1' and cfg['api_key'] == ''
    save_setting('llm', {**base, 'embed_base_url': 'http://embed:8082/v1', 'embed_api_key': 'embed-key'})
    assert semantic.config()['api_key'] == 'embed-key'


def test_the_embeddings_key_is_redacted_and_survives_a_save(client):
    assert client.put('/api/settings/llm', json={'embed_model': 'e', 'embed_api_key': 'embed-key',
                                                 'embed_base_url': 'http://embed:8082/v1'}, headers=H).status_code == 200
    assert client.get('/api/settings').json()['llm']['embed_api_key'] == '********'
    assert client.put('/api/settings/llm', json={'embed_api_key': '********'}, headers=H).status_code == 200
    with session() as s:
        assert db.get_settings(s)['llm']['embed_api_key'] == 'embed-key'
    assert client.put('/api/settings/llm', json={'embed_api_key': '********',
                                                 'embed_base_url': 'http://other:9000/v1'}, headers=H).status_code == 400


def test_an_embeddings_key_on_the_judge_server_is_used(client):
    """A key without a separate server means "same server, different credential"."""
    base = {**db.get_settings()['llm'], 'base_url': 'http://judge:8080/v1', 'api_key': 'judge-key',
            'embed_model': 'e', 'embed_base_url': ''}
    save_setting('llm', {**base, 'embed_api_key': 'embed-key'})
    cfg = semantic.config()
    assert cfg['base_url'] == 'http://judge:8080/v1' and cfg['api_key'] == 'embed-key'
    save_setting('llm', {**base, 'embed_api_key': ''})
    assert semantic.config()['api_key'] == 'judge-key'


def test_a_bad_server_url_is_refused_rather_than_saved(client):
    assert client.put('/api/settings/llm', json={'embed_base_url': 'embed:8082/v1'}, headers=H).status_code == 400
    assert client.put('/api/settings/llm', json={'worker_base_url': 'ftp://nope'}, headers=H).status_code == 400
    assert client.put('/api/settings/llm', json={'embed_base_url': 'http://embed:8082/v1'}, headers=H).status_code == 200


def test_a_pasted_embeddings_url_is_not_doubled(client):
    """The hint invites pasting an endpoint; ".../v1/embeddings" must not become ".../embeddings/embeddings"."""
    base = {**db.get_settings()['llm'], 'embed_model': 'e'}
    for given, expected in (('http://embed:8082/v1/embeddings', 'http://embed:8082/v1'),
                            ('http://embed:8082/v1/embeddings/', 'http://embed:8082/v1'),
                            ('http://embed:8082/v1', 'http://embed:8082/v1')):
        save_setting('llm', {**base, 'embed_base_url': given})
        assert semantic.config()['base_url'] == expected, given
    assert semantic.base_url('') == '' and semantic.base_url('http://x/v1/') == 'http://x/v1'
