"""Judge and worker model roles: one big model for thinking, an optional cheap one for grunt work."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-llm-roles-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import db, llm
from bt.db import init_db, save_setting, session
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


def _llm(**values):
    save_setting('llm', {**db.get_settings()['llm'], **values})


class FakeResponse:
    status_code = 200
    text = ''

    def json(self):
        return {'choices': [{'message': {'content': '{"ok": true}'}, 'finish_reason': 'stop'}]}


def _capture(monkeypatch):
    seen = []

    def fake_post(cfg, body, abort=None):
        seen.append({'model': body['model'], 'base_url': cfg['base_url'], 'api_key': cfg.get('api_key')})
        return FakeResponse()

    monkeypatch.setattr(llm, '_post', fake_post)
    return seen


def test_an_unconfigured_worker_uses_the_judge_everywhere():
    _llm(model='big-model', base_url='http://judge:8080/v1', api_key='judge-key',
         worker_model='', worker_base_url='', worker_api_key='')
    judge, worker = llm._cfg('judge'), llm._cfg('worker')
    assert judge['model'] == 'big-model'
    assert worker['model'] == 'big-model'
    assert worker['base_url'] == 'http://judge:8080/v1'
    assert worker['api_key'] == 'judge-key'


def test_a_configured_worker_gets_its_own_model_endpoint_and_key():
    _llm(model='big-model', base_url='http://judge:8080/v1', api_key='judge-key',
         worker_model='small-model', worker_base_url='http://worker:8081/v1', worker_api_key='worker-key')
    assert llm._cfg('judge')['model'] == 'big-model'
    worker = llm._cfg('worker')
    assert worker['model'] == 'small-model'
    assert worker['base_url'] == 'http://worker:8081/v1'
    assert worker['api_key'] == 'worker-key'


def test_a_worker_model_alone_keeps_the_judge_endpoint_and_key():
    _llm(model='big-model', base_url='http://judge:8080/v1', api_key='judge-key',
         worker_model='small-model', worker_base_url='', worker_api_key='')
    worker = llm._cfg('worker')
    assert worker['model'] == 'small-model'
    assert worker['base_url'] == 'http://judge:8080/v1' and worker['api_key'] == 'judge-key'


def test_the_role_decides_which_model_is_requested(monkeypatch):
    _llm(model='big-model', base_url='http://judge:8080/v1', api_key='judge-key',
         worker_model='small-model', worker_base_url='http://worker:8081/v1', worker_api_key='worker-key')
    seen = _capture(monkeypatch)
    llm.chat_json([{'role': 'user', 'content': 'judge this'}], schema_name='judge_task')
    llm.chat_json([{'role': 'user', 'content': 'screen this'}], schema_name='screen', role='worker')
    assert seen[0] == {'model': 'big-model', 'base_url': 'http://judge:8080/v1', 'api_key': 'judge-key'}
    assert seen[1] == {'model': 'small-model', 'base_url': 'http://worker:8081/v1', 'api_key': 'worker-key'}


def test_screening_and_summaries_use_the_worker(monkeypatch):
    """The two grunt-work paths must not spend the big model."""
    from bt.hub import ai
    _llm(model='big-model', worker_model='small-model')
    seen = _capture(monkeypatch)
    ai.summarize_items([{'title': 'A', 'body': 'Some captured text.'}])
    assert seen and seen[-1]['model'] == 'small-model'
    # Ranking is a judgement, so it keeps the big model.
    seen.clear()
    from bt.hub.models import Curator
    ai.rank(Curator(profile_id=1, name='c'), [{'title': 'A', 'summary': 'B', 'url': 'https://example.org/a'}])
    assert seen and seen[-1]['model'] == 'big-model'


def test_the_worker_key_is_redacted_and_survives_a_save(client):
    assert client.put('/api/settings/llm', json={'worker_model': 'small-model', 'worker_api_key': 'worker-key',
                                                 'worker_base_url': 'http://worker:8081/v1'}, headers=H).status_code == 200
    got = client.get('/api/settings').json()['llm']
    assert got['worker_api_key'] == '********' and got['worker_model'] == 'small-model'
    # Saving the redacted value keeps the stored key.
    assert client.put('/api/settings/llm', json={'worker_api_key': '********'}, headers=H).status_code == 200
    with session() as s:
        assert db.get_settings(s)['llm']['worker_api_key'] == 'worker-key'
    # Pointing the worker at a new server without a key is refused rather than silently reusing one.
    assert client.put('/api/settings/llm', json={'worker_api_key': '********',
                                                 'worker_base_url': 'http://other:9000/v1'}, headers=H).status_code == 400


def _mock_http(monkeypatch, handler):
    import httpx
    from bt.api import agent
    real = httpx.AsyncClient
    monkeypatch.setattr(agent.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_the_embeddings_probe_reports_the_vector_size(client, monkeypatch):
    """Dimensions are read from the server, so there is nothing to configure."""
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get('authorization')))
        return __import__('httpx').Response(200, json={'data': [{'embedding': [0.1] * 8}]})

    _mock_http(monkeypatch, handler)
    _llm(embed_model='e', embed_base_url='http://embed:8082/v1', embed_api_key='embed-key')
    got = client.post('/api/agent/llm-test', json={'role': 'embed'}, headers=H).json()
    assert got['ok'] is True and got['dimensions'] == 8
    assert seen[0][0] == 'http://embed:8082/v1/embeddings'
    assert seen[0][1] == 'Bearer embed-key'


def test_the_embeddings_probe_needs_a_model(client):
    _llm(embed_model='')
    got = client.post('/api/agent/llm-test', json={'role': 'embed'}, headers=H).json()
    assert got['ok'] is False and 'embeddings model' in got['error']


def test_the_worker_probe_uses_the_worker_endpoint_and_key(client, monkeypatch):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get('authorization'), request.content.decode()))
        return __import__('httpx').Response(200, json={'choices': [{'message': {'content': 'pong'}}]})

    _mock_http(monkeypatch, handler)
    _llm(model='big', base_url='http://judge:8080/v1', api_key='judge-key',
         worker_model='small', worker_base_url='http://worker:8081/v1', worker_api_key='worker-key')
    got = client.post('/api/agent/llm-test', json={'role': 'worker'}, headers=H).json()
    assert got['ok'] is True and got['reply'] == 'pong'
    chat = [s for s in seen if s[0].endswith('/chat/completions')]
    assert chat and chat[0][0] == 'http://worker:8081/v1/chat/completions'
    assert chat[0][1] == 'Bearer worker-key'
    assert '"small"' in chat[0][2]


def test_a_saved_worker_key_is_not_sent_to_a_different_server(client):
    _llm(worker_base_url='http://worker:8081/v1', worker_api_key='worker-key')
    got = client.post('/api/agent/llm-test', json={'role': 'worker', 'base_url': 'http://other:9000/v1',
                                                   'api_key': '********'}, headers=H)
    assert got.status_code == 400
