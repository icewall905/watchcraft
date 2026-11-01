"""Short, memory-only voice-search recordings sent to the household STT service."""
from __future__ import annotations

import array
import asyncio
import io
import math
import os
import sys
import wave
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException

BASE_URL = os.environ.get('WC_STT_BASE_URL', '').rstrip('/')
MODEL = os.environ.get('WC_STT_MODEL', 'Qwen/Qwen3-ASR-1.7B')
API_KEY = os.environ.get('WC_STT_API_KEY', '')
LANGUAGE = os.environ.get('WC_STT_LANGUAGE', '')
MAX_BYTES = 3_000_000
MAX_SECONDS = 30
SAMPLE_RATE = 16000
AUDIO_TYPES = {'audio/webm', 'video/webm', 'audio/ogg', 'audio/mp4', 'audio/x-m4a', 'audio/wav', 'audio/x-wav'}


def normalize_url(value: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, 'Use an HTTP or HTTPS speech server URL.')
    value = value.strip().rstrip('/')
    if not value:
        return ''
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and parts.username is None
                 and parts.password is None and not parts.query and not parts.fragment
                 and not any(c.isspace() for c in value))
        parts.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(400, 'Use an HTTP or HTTPS speech server URL without credentials, query or fragment.')
    path = parts.path.rstrip('/')
    if path.endswith('/audio/transcriptions'):
        path = path[:-len('/audio/transcriptions')]
    while path.endswith('/v1/v1'):
        path = path[:-3]
    if not path:
        path = '/v1'
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def config() -> dict:
    """Read live settings for each operation; environment remains the default."""
    from .db import session
    from .models import Setting
    defaults = dict(base_url=BASE_URL, model=MODEL, api_key=API_KEY, language=LANGUAGE,
                    subtitles_enabled=os.environ.get('WC_STT_SUBTITLES_ENABLED', 'true').lower() in ('true', '1', 'yes'))
    with session() as s:
        row = s.get(Setting, 'stt')
        if row:
            defaults.update(row.value)
    defaults['base_url'] = normalize_url(defaults['base_url'])
    return defaults


def prepare_config(value: dict, current: dict) -> dict:
    merged = {**current, **{k: v for k, v in value.items() if k in current}}
    merged['base_url'] = normalize_url(merged['base_url'])
    if merged.get('api_key') == '********':
        if merged['base_url'] != normalize_url(current.get('base_url', '')):
            raise HTTPException(400, 'Enter the API key for the new speech server.')
        merged['api_key'] = current.get('api_key', '')
    elif 'api_key' not in value and merged['base_url'] != normalize_url(current.get('base_url', '')):
        # Never silently send a saved credential to a changed destination.
        merged['api_key'] = ''
    for field, limit in (('model', 200), ('api_key', 4096), ('language', 40)):
        if not isinstance(merged.get(field), str) or len(merged[field]) > limit or any(ord(c) < 32 for c in merged[field]):
            raise HTTPException(400, 'Use valid speech model, language and API key values.')
        merged[field] = merged[field].strip()
    if not merged['model']:
        raise HTTPException(400, 'Enter the speech model name.')
    if type(merged.get('subtitles_enabled')) is not bool:
        raise HTTPException(400, 'Subtitle generation must be on or off.')
    return merged


async def test_connection(settings: dict) -> dict:
    if not settings['base_url']:
        return {'ok': False, 'error': 'Enter the speech server URL.'}
    headers = {'Authorization': f"Bearer {settings['api_key']}"} if settings['api_key'] else {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), trust_env=False, follow_redirects=False) as client:
            response = await client.get(settings['base_url'] + '/models', headers=headers)
        if not response.is_success:
            return {'ok': False, 'error': 'The speech server refused the connection. Check its settings and API key.'}
        data = response.json()
        models = data.get('data') if isinstance(data, dict) else None
        if not isinstance(models, list) or not any(isinstance(m, dict) and m.get('id') == settings['model'] for m in models):
            return {'ok': False, 'error': 'The configured speech model is not available on this server.'}
    except (httpx.HTTPError, ValueError):
        return {'ok': False, 'error': 'Cannot reach a compatible speech server. Check its address and connection.'}
    return {'ok': True, 'model': settings['model']}


async def normalize_audio(data: bytes) -> bytes:
    """Bound decoding time and output; ffmpeg cannot follow URLs embedded in an upload."""
    proc = await asyncio.create_subprocess_exec(
        'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', '1',
        '-protocol_whitelist', 'pipe', '-i', 'pipe:0', '-map', '0:a:0', '-vn',
        '-t', str(MAX_SECONDS + 1), '-ac', '1', '-ar', str(SAMPLE_RATE),
        '-c:a', 'pcm_s16le', '-f', 'wav', 'pipe:1',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(data), timeout=15)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise HTTPException(422, 'The microphone recording could not be read. Please try again.')
    try:
        with wave.open(io.BytesIO(output), 'rb') as source:
            pcm = source.readframes(SAMPLE_RATE * (MAX_SECONDS + 1))
    except (wave.Error, EOFError):
        raise HTTPException(422, 'The microphone recording could not be read. Please try again.') from None
    seconds = len(pcm) / (SAMPLE_RATE * 2)
    if seconds > MAX_SECONDS:
        raise HTTPException(422, 'Please keep voice searches under 30 seconds.')
    if seconds < 0.2:
        raise HTTPException(422, 'The recording was too short. Please say your search again.')
    samples = array.array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
    if rms < 0.0007:
        raise HTTPException(422, 'No speech was heard. Please try again or check your microphone.')
    # A seekable writer gives the provider an accurate WAV length (pipe headers do not).
    result = io.BytesIO()
    with wave.open(result, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(SAMPLE_RATE)
        target.writeframes(pcm)
    return result.getvalue()


async def transcribe(data: bytes) -> str:
    settings = config()
    if not settings['base_url']:
        raise HTTPException(503, 'Voice search is not configured on this server yet.')
    try:
        audio = await normalize_audio(data)
    except asyncio.TimeoutError:
        raise HTTPException(422, 'The recording could not be processed. Please try a shorter phrase.') from None
    except FileNotFoundError:
        raise HTTPException(503, 'Voice search audio processing is unavailable.') from None
    fields = {'model': settings['model'], 'response_format': 'json'}
    if settings['language']:
        fields['language'] = settings['language']
    headers = {'Authorization': f"Bearer {settings['api_key']}"} if settings['api_key'] else {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5), trust_env=False, follow_redirects=False) as client:
            response = await client.post(settings['base_url'] + '/audio/transcriptions', data=fields,
                                         files={'file': ('search.wav', audio, 'audio/wav')}, headers=headers)
        if not response.is_success:
            raise HTTPException(503, 'The speech-to-text service is unavailable. Please try again shortly.')
        result = response.json()
    except httpx.TimeoutException:
        raise HTTPException(504, 'Speech-to-text took too long. Please try again.') from None
    except httpx.HTTPError:
        raise HTTPException(503, 'Cannot reach the speech-to-text service. Please try again shortly.') from None
    except ValueError:
        raise HTTPException(502, 'The speech-to-text service returned an unreadable response.') from None
    text = result.get('text') if isinstance(result, dict) else None
    if not isinstance(text, str):
        raise HTTPException(502, 'The speech-to-text service returned an unreadable response.')
    text = ' '.join(text.split()).strip()[:500]
    if not text:
        raise HTTPException(422, 'No speech was recognized. Please try saying your search again.')
    return text
