"""Live speech settings, credential handling and administrator checks."""
import httpx
import pytest
from sqlmodel import Session

from bt import db, stt
from bt.models import Profile
from test_speech import client  # isolated schema and server fixture


def test_settings_are_live_and_keys_are_redacted(client):
    value = dict(base_url='http://10.0.10.73:8011/v1/audio/transcriptions/',
                 model='Qwen/Qwen3-ASR-1.7B', api_key='private-test-key', language='da', subtitles_enabled=True)
    response = client.put('/api/settings/stt', json=value)
    assert response.status_code == 200
    assert response.json()['base_url'] == 'http://10.0.10.73:8011/v1'
    assert response.json()['api_key'] == '********'
    assert 'private-test-key' not in client.get('/api/settings').text
    assert stt.config()['api_key'] == 'private-test-key'
    assert stt.config()['language'] == 'da'
    assert client.get('/api/speech/status').json() == {'enabled': True}
    assert client.put('/api/settings/stt', json={'api_key': '********', 'language': 'en'}).status_code == 200
    assert stt.config()['api_key'] == 'private-test-key'
    assert client.put('/api/settings/stt', json={'base_url': 'http://other:8011', 'api_key': '********'}).status_code == 400
    assert client.put('/api/settings/stt', json={'base_url': 'http://other:8011'}).status_code == 200
    assert stt.config()['api_key'] == ''
    assert client.put('/api/settings/stt', json={'base_url': ''}).status_code == 200
    assert client.get('/api/speech/status').json() == {'enabled': False}


@pytest.mark.parametrize('url', ['file:///tmp/test', 'ftp://host', 'http://user:pass@host/v1',
                                'http://host/v1?key=secret', 'http://host/#fragment', 'http://host:bad',
                                'http://bad host', 123])
def test_invalid_endpoints_are_rejected(client, url):
    response = client.put('/api/settings/stt', json={'base_url': url})
    assert response.status_code == 400
    assert client.post('/api/speech/test', json={'base_url': url}).status_code == 400


def test_settings_and_connection_test_require_admin(client):
    with Session(db.engine) as s:
        p = s.get(Profile, 1); p.role = 'adult'; s.add(p); s.commit()
    assert client.put('/api/settings/stt', json={'base_url': 'http://host'}).status_code == 403
    assert client.post('/api/speech/test', json={'base_url': 'http://host'}).status_code == 403


def test_draft_connection_uses_preserved_key_and_never_saves(client, monkeypatch):
    client.put('/api/settings/stt', json={'base_url': 'http://stt.local/v1', 'api_key': 'private-test-key'})
    real = httpx.AsyncClient
    def handler(request):
        assert str(request.url) == 'http://stt.local/v1/models'
        assert request.headers['authorization'] == 'Bearer private-test-key'
        return httpx.Response(200, json={'data': [{'id': 'draft-model'}]})
    monkeypatch.setattr(stt.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    result = client.post('/api/speech/test', json={'model': 'draft-model', 'api_key': '********'})
    assert result.json() == {'ok': True, 'model': 'draft-model'}
    assert stt.config()['model'] != 'draft-model'
    assert client.post('/api/speech/test', json={'base_url': 'http://other', 'api_key': '********'}).status_code == 400


@pytest.mark.parametrize('failure', ['timeout', 'malformed', 'missing', 'denied'])
def test_connection_errors_do_not_leak_provider_details(client, monkeypatch, failure):
    real = httpx.AsyncClient
    def handler(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('private key and URL')
        if failure == 'malformed':
            return httpx.Response(200, text='private key and URL')
        if failure == 'denied':
            return httpx.Response(401, json={'error': 'private key and URL'})
        return httpx.Response(200, json={'data': []})
    monkeypatch.setattr(stt.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    response = client.post('/api/speech/test', json={'base_url': 'http://private-host/v1', 'api_key': 'private-key'})
    assert response.status_code == 200 and response.json()['ok'] is False
    assert 'private' not in response.text


def test_voice_search_uses_saved_model_language_and_key(client, monkeypatch):
    import asyncio
    client.put('/api/settings/stt', json={'base_url': 'http://saved.local/v1', 'model': 'saved-model',
                                        'language': 'da', 'api_key': 'saved-key'})
    real = httpx.AsyncClient
    async def normalize(data):
        return b'RIFF-normalized-audio'
    async def handler(request):
        assert str(request.url) == 'http://saved.local/v1/audio/transcriptions'
        assert request.headers['authorization'] == 'Bearer saved-key'
        payload = await request.aread()
        assert b'saved-model' in payload and b'\r\n\r\nda\r\n' in payload
        return httpx.Response(200, json={'text': 'find videos'})
    monkeypatch.setattr(stt, 'normalize_audio', normalize)
    monkeypatch.setattr(stt.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(stt.transcribe(b'audio')) == 'find videos'
