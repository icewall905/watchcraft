import array
import asyncio
import io
import math
import os
import subprocess
import tempfile
import wave

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-speech-'))

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import db, stt
from bt.api import speech
from bt.main import app


def audio(seconds=0.5, rate=16000, channels=1, silence=False):
    samples = array.array('h', (0 if silence else int(4000 * math.sin(2 * math.pi * 440 * n / rate))
                                for n in range(int(seconds * rate))))
    if channels == 2: samples = array.array('h', (value for sample in samples for value in (sample, sample)))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as out:
        out.setnchannels(channels); out.setsampwidth(2); out.setframerate(rate); out.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.fixture
def client(monkeypatch):
    with db.engine.begin() as conn: conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine); db.init_db()
    monkeypatch.setattr(stt, 'BASE_URL', 'http://stt.local:8011/v1')
    monkeypatch.setattr(speech, '_slots', asyncio.Semaphore(2))
    yield TestClient(app)
    SQLModel.metadata.drop_all(db.engine); db.init_db()


def test_normalization_makes_mono_16khz_wav():
    result = asyncio.run(stt.normalize_audio(audio(rate=48000, channels=2)))
    with wave.open(io.BytesIO(result), 'rb') as decoded:
        assert decoded.getframerate() == 16000 and decoded.getnchannels() == 1
        assert decoded.getsampwidth() == 2
        assert abs(decoded.getnframes() / 16000 - 0.5) < 0.01


def test_browser_webm_recording_is_accepted():
    encoded = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0', '-c:a', 'libopus',
                              '-f', 'webm', 'pipe:1'], input=audio(), stdout=subprocess.PIPE, check=True).stdout
    result = asyncio.run(stt.normalize_audio(encoded))
    with wave.open(io.BytesIO(result), 'rb') as decoded: assert decoded.getnframes() > 7000


@pytest.mark.parametrize('recording,detail', [
    (b'not an audio recording', 'could not be read'),
    (audio(seconds=0.05), 'too short'),
    (audio(silence=True), 'No speech'),
    (audio(seconds=31), 'under 30 seconds'),
], ids=['invalid', 'short', 'silent', 'long'])
def test_bad_silent_and_long_recordings_are_rejected(recording, detail):
    with pytest.raises(HTTPException) as error: asyncio.run(stt.normalize_audio(recording))
    assert error.value.status_code == 422 and detail in error.value.detail


def test_transcription_is_profile_scoped_and_receives_raw_audio(client, monkeypatch):
    received = []
    async def transcribe(data): received.append(data); return 'woodworking ideas'
    monkeypatch.setattr(stt, 'transcribe', transcribe)
    recording = audio()
    response = client.post('/api/speech/transcribe', content=recording, headers={'Content-Type': 'audio/webm;codecs=opus'})
    assert response.status_code == 200 and response.json() == {'text': 'woodworking ideas'}
    assert received == [recording]
    assert client.post('/api/speech/transcribe', content=recording,
                       headers={'Content-Type': 'audio/wav', 'X-Profile-Id': '99999'}).status_code == 404
    assert len(received) == 1


def test_pin_locked_profile_cannot_transcribe(client, monkeypatch):
    client.patch('/api/profiles/1', json={'pin': '4826'})
    client.post('/api/profiles/1/lock')
    assert client.get('/api/speech/status').status_code == 403
    assert client.post('/api/speech/transcribe', content=audio(), headers={'Content-Type': 'audio/wav'}).status_code == 403


def test_upload_limits_and_busy_service(client, monkeypatch):
    assert client.post('/api/speech/transcribe', content=b'x', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post('/api/speech/transcribe', content=b'', headers={'Content-Type': 'audio/wav'}).status_code == 422
    assert client.post('/api/speech/transcribe', content=b'x' * (stt.MAX_BYTES + 1), headers={'Content-Type': 'audio/wav'}).status_code == 413
    monkeypatch.setattr(speech, '_slots', asyncio.Semaphore(0))
    assert client.post('/api/speech/transcribe', content=audio(), headers={'Content-Type': 'audio/wav'}).status_code == 429


def test_unconfigured_service_gives_clear_error(client, monkeypatch):
    monkeypatch.setattr(stt, 'BASE_URL', '')
    assert client.get('/api/speech/status').json() == {'enabled': False}
    response = client.post('/api/speech/transcribe', content=audio(), headers={'Content-Type': 'audio/wav'})
    assert response.status_code == 503 and 'not configured' in response.json()['detail']


def mock_provider(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(stt.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(stt, 'BASE_URL', 'http://stt.local:8011/v1')
    monkeypatch.setattr(stt, 'API_KEY', '')
    async def normalize(data): return audio()
    monkeypatch.setattr(stt, 'normalize_audio', normalize)


def test_provider_receives_wav_and_correct_model(monkeypatch):
    async def handler(request):
        assert str(request.url) == 'http://stt.local:8011/v1/audio/transcriptions'
        payload = await request.aread()
        assert b'filename="search.wav"' in payload and b'audio/wav' in payload and b'RIFF' in payload
        assert stt.MODEL.encode() in payload
        return httpx.Response(200, json={'text': '  Show me\nwoodworking videos. '})
    mock_provider(monkeypatch, handler)
    assert asyncio.run(stt.transcribe(b'recording')) == 'Show me woodworking videos.'


@pytest.mark.parametrize('status,body,expected', [(503, {'detail': 'private details'}, 503),
    (200, {'unexpected': 'shape'}, 502), (200, {'text': ''}, 422)])
def test_upstream_errors_are_clear_and_do_not_expose_details(monkeypatch, status, body, expected):
    mock_provider(monkeypatch, lambda request: httpx.Response(status, json=body))
    with pytest.raises(HTTPException) as error: asyncio.run(stt.transcribe(b'recording'))
    assert error.value.status_code == expected and 'private details' not in error.value.detail


def test_provider_timeout_is_reported(monkeypatch):
    def timeout(request): raise httpx.ReadTimeout('private endpoint detail')
    mock_provider(monkeypatch, timeout)
    with pytest.raises(HTTPException) as error: asyncio.run(stt.transcribe(b'recording'))
    assert error.value.status_code == 504
