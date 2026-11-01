"""Durable, one-at-a-time alternative captions from the administrator's STT service.

Qwen ASR supplies text without alignment. Short audio chunks end at nearby pauses;
readable two-line cues use approximate timing and never replace YouTube's tracks.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import io
import json
import logging
import math
import secrets
import shutil
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select
from yt_dlp import YoutubeDL

from . import captions, stt, ytdl
from .config import DATA_DIR
from .db import session
from .downloader.service import abs_path, VID_RE
from .models import Download, GeneratedSubtitles

log = logging.getLogger(__name__)
MAX_SECONDS = 4 * 3600
MAX_AUDIO_BYTES = 512_000_000
MAX_PENDING = 5
LEASE_SECONDS = 240
CHUNK_SECONDS = 12
RATE = 16000


class SubtitleError(Exception):
    pass


def enabled(cfg=None):
    cfg = cfg or stt.config()
    return bool(cfg.get('base_url') and cfg.get('subtitles_enabled'))


def _fingerprint(cfg):
    return hashlib.sha256(json.dumps([cfg.get(k, '') for k in ('base_url', 'model', 'language')]).encode()).hexdigest()


def status(video_id):
    with session() as s:
        row = s.get(GeneratedSubtitles, video_id)
        result = {'enabled': enabled(), 'status': row.status if row else 'idle',
                  'progress': round(row.progress, 1) if row else 0, 'error': row.error if row else '',
                  'timing_approximate': True, 'track': None}
        if row and row.status == 'ready' and row.vtt:
            result['track'] = {'key': 'generated', 'lang': row.language or 'und', 'name': 'Generated (STT)',
                               'auto': False, 'url': f'/api/video/{video_id}/subtitles/generated.vtt'}
        return result


def enqueue(video_id, pid):
    if not VID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Invalid video id')
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(GeneratedSubtitles, video_id)
        if row and row.status in ('queued', 'running', 'ready'):
            return
        if not enabled():
            raise HTTPException(503, 'Configure generated subtitles under Settings → Player.')
        count = len(s.exec(select(GeneratedSubtitles.video_id).where(
            GeneratedSubtitles.status.in_(('queued', 'running')))).all())
        if count >= MAX_PENDING:
            raise HTTPException(429, 'The subtitle queue is full. Please try again when a video finishes.')
        row = row or GeneratedSubtitles(video_id=video_id)
        if not row.cues:
            row.completed_s, row.progress = 0, 0
        row.status, row.error, row.requested_by = 'queued', '', pid
        row.updated_at, row.token, row.lease_until = time.time(), '', 0
        # Retry keeps completed chunks from this same model/language/server.
        s.add(row); s.commit()


def claim():
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        now = time.time()
        # A live job in another process keeps the household STT queue serial.
        if s.exec(select(GeneratedSubtitles.video_id).where(
                GeneratedSubtitles.status == 'running', GeneratedSubtitles.lease_until > now)).first():
            return None
        row = s.exec(select(GeneratedSubtitles).where(
            (GeneratedSubtitles.status == 'queued') |
            ((GeneratedSubtitles.status == 'running') & (GeneratedSubtitles.lease_until <= now)))
            .order_by(GeneratedSubtitles.updated_at).limit(1)).first()
        if not row:
            return None
        row.status, row.token = 'running', secrets.token_hex(16)
        row.lease_until, row.updated_at = now + LEASE_SECONDS, now
        s.add(row); s.commit()
        return row.video_id, row.token


def _update(video_id, token, **values):
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(GeneratedSubtitles, video_id)
        if not row or row.token != token or row.status != 'running' or row.lease_until <= time.time():
            raise SubtitleError('Subtitle generation was superseded.')
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = time.time()
        row.lease_until = row.updated_at + LEASE_SECONDS if row.status == 'running' else 0
        s.add(row); s.commit()


def _source(video_id, directory, beat, stop):
    with session() as s:
        download = s.get(Download, video_id)
        path = abs_path(download.path) if download and download.status == 'done' else None
    if path and path.is_file():
        return path
    try:
        info = ytdl.video_info(video_id, origin='user')
    except Exception:
        raise SubtitleError('YouTube audio could not be fetched. Try saving the video offline first.') from None
    if ytdl._is_live(info):
        raise SubtitleError('Generate subtitles after the live stream has finished.')
    duration = info.get('duration')
    if not isinstance(duration, (int, float)) or not 0 < duration <= MAX_SECONDS:
        raise SubtitleError('Subtitle generation supports videos up to four hours.')
    class Quiet:
        def debug(self, *a): pass
        def warning(self, *a): pass
        def error(self, *a): pass
    def progress(event):
        if stop.is_set():
            raise SubtitleError('Subtitle generation stopped; retry to continue.')
        if event.get('downloaded_bytes', 0) > MAX_AUDIO_BYTES:
            raise SubtitleError('The audio exceeds the 512 MB generation limit.')
        beat()
    try:
        opts = ytdl._opts(skip_download=False, format='bestaudio/best', noplaylist=True,
                         outtmpl=str(directory / 'source.%(ext)s'), max_filesize=MAX_AUDIO_BYTES,
                         retries=1, fragment_retries=1, logger=Quiet(), progress_hooks=[progress])
        with YoutubeDL(opts) as dl:
            dl.download([f'https://www.youtube.com/watch?v={video_id}'])
    except Exception:
        raise SubtitleError('YouTube audio could not be fetched. Try saving the video offline first.') from None
    files = [p for p in directory.glob('source.*') if p.is_file() and p.suffix not in ('.part', '.ytdl')]
    if len(files) != 1 or files[0].stat().st_size > MAX_AUDIO_BYTES:
        raise SubtitleError('The audio could not be downloaded within the generation limit.')
    return files[0]


def normalize(source, target, beat, stop):
    proc = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1',
        '-protocol_whitelist', 'file,pipe', '-i', str(source), '-t', str(MAX_SECONDS + 1),
        '-map', '0:a:0', '-vn', '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le', str(target)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if stop.wait(1) or time.monotonic() - started > 300:
                raise SubtitleError('Audio preparation stopped or took too long. Please retry.')
            beat()
        if proc.returncode:
            raise SubtitleError('The video audio could not be read.')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    with wave.open(str(target), 'rb') as wav:
        duration = wav.getnframes() / RATE
    if not 0.2 <= duration <= MAX_SECONDS:
        raise SubtitleError('Subtitle generation supports videos between 0.2 seconds and four hours.')
    return duration


def audio_chunk(wav, offset):
    """Use a pause near the chunk end, and don't ask ASR to hallucinate silence."""
    wav.setpos(round(offset * RATE))
    pcm = wav.readframes(CHUNK_SECONDS * RATE)
    samples = array.array('h', pcm)
    if sys.byteorder != 'little': samples.byteswap()
    if not samples:
        return None
    step = RATE // 10
    energy = [math.sqrt(sum(v*v for v in samples[n:n+step]) / len(samples[n:n+step])) / 32768
              for n in range(0, len(samples), step)]
    end = len(samples) / RATE
    # Three quiet 100 ms windows provide a natural break, without arbitrary word overlap.
    pauses = [n / 10 for n in range(60, len(energy)-2) if max(energy[n:n+3]) < .002]
    if pauses and end >= CHUNK_SECONDS:
        end = pauses[-1] + .15
        samples = samples[:round(end * RATE)]
        energy = energy[:math.ceil(end * 10)]
    active = [n for n,e in enumerate(energy) if e >= .0007]
    end = offset + len(samples) / RATE
    if not active:
        return end, b'', offset, end
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(RATE)
        if sys.byteorder != 'little': samples.byteswap()
        target.writeframes(samples.tobytes())
    return end, output.getvalue(), offset + active[0] / 10, min(end, offset + (active[-1] + 1) / 10)


def transcribe_chunk(audio, cfg):
    fields = {'model': cfg['model'], 'response_format': 'json'}
    if cfg.get('language'): fields['language'] = cfg['language']
    headers = {'Authorization': 'Bearer '+cfg['api_key']} if cfg.get('api_key') else {}
    async def request():
        # Total deadline, not just a per-read timeout: never outlive the durable lease.
        async with asyncio.timeout(120):
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5), trust_env=False, follow_redirects=False) as client:
                async with client.stream('POST', cfg['base_url']+'/audio/transcriptions', data=fields,
                                         files={'file': ('subtitle.wav', audio, 'audio/wav')}, headers=headers) as response:
                    if not response.is_success:
                        raise SubtitleError(f'The STT service returned HTTP {response.status_code}. Check Settings → Player.')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 200_000: raise SubtitleError('The STT response was too large.')
                    return json.loads(raw)
    try:
        data = asyncio.run(request())
        value = data.get('text') if isinstance(data, dict) else None
        if not isinstance(value, str): raise ValueError()
        return ' '.join(value.split())[:20000]
    except SubtitleError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise SubtitleError('The STT service took too long. Retry to continue from the last completed chunk.') from None
    except (httpx.HTTPError, ValueError):
        raise SubtitleError('The STT service could not return a transcript. Check Settings → Player.') from None


def make_vtt(cues):
    lines = ['WEBVTT', '']
    for cue in cues:
        lines.extend([f"{captions._stamp(round(cue['start']*1000))} --> {captions._stamp(round(cue['end']*1000))}",
                      cue['text'], ''])
    return captions.two_line_vtt('\n'.join(lines), 'vtt', rolling=False)


def run(video_id, token, stop=None):
    stop = stop or threading.Event()
    last_beat = [0.0]
    def beat():
        if stop.is_set(): raise SubtitleError('Subtitle generation stopped; retry to continue.')
        if time.monotonic() - last_beat[0] > 20:
            _update(video_id, token)
            last_beat[0] = time.monotonic()
    try:
        cfg = stt.config()
        if not enabled(cfg): raise SubtitleError('Configure generated subtitles under Settings → Player.')
        with session() as s:
            row = s.get(GeneratedSubtitles, video_id)
            if not row or row.token != token: return
            cues, offset = list(row.cues), row.completed_s
            if row.fingerprint != _fingerprint(cfg): cues, offset = [], 0
        _update(video_id, token, fingerprint=_fingerprint(cfg), language=cfg.get('language',''),
                cues=cues, completed_s=offset, progress=0 if not offset else row.progress, error='')
        root = DATA_DIR / 'subtitle-tmp'; root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f'{video_id}-{token}-', dir=root) as temp:
            directory = Path(temp)
            source = _source(video_id, directory, beat, stop)
            duration = normalize(source, directory / 'normalized.wav', beat, stop)
            if offset > duration: cues, offset = [], 0
            with wave.open(str(directory / 'normalized.wav'), 'rb') as wav:
                while offset < duration - .001:
                    beat()
                    got = audio_chunk(wav, offset)
                    if not got: break
                    end, audio, start, finish = got
                    value = transcribe_chunk(audio, cfg) if audio else ''
                    if value: cues.append({'start': start, 'end': finish, 'text': value})
                    offset = end
                    _update(video_id, token, cues=cues, completed_s=offset,
                            progress=min(99, 5 + 94 * offset / duration), duration_s=duration)
        if not cues: raise SubtitleError('No speech was recognized in this video.')
        _update(video_id, token, status='ready', progress=100, vtt=make_vtt(cues), error='')
    except Exception as exc:
        message = str(exc) if isinstance(exc, SubtitleError) else 'Subtitle generation failed. Please retry.'
        try: _update(video_id, token, status='failed', error=message)
        except SubtitleError: pass
        if not isinstance(exc, SubtitleError): log.exception('Subtitle generation failed for %s', video_id)


def cleanup_temp():
    """Discard crash leftovers only when their owning lease is no longer live."""
    root = DATA_DIR / 'subtitle-tmp'
    if not root.exists(): return
    now = time.time()
    with session() as s:
        live = [(r.video_id, r.token) for r in s.exec(select(GeneratedSubtitles).where(
            GeneratedSubtitles.status == 'running', GeneratedSubtitles.lease_until > now)).all()]
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or not re.match(r'^[A-Za-z0-9_-]{11}-[0-9a-f]{32}-', path.name):
            continue
        if now - path.stat().st_mtime <= LEASE_SECONDS: continue
        if any(path.name.startswith(f'{vid}-{token}-') for vid,token in live): continue
        shutil.rmtree(path)


async def worker():
    stop = threading.Event()
    try:
        while True:
            try:
                await asyncio.to_thread(cleanup_temp)
                job = await asyncio.to_thread(claim)
                if job: await asyncio.to_thread(run, *job, stop)
                else: await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Subtitle queue tick failed; retrying')
                await asyncio.sleep(2)
    finally:
        stop.set()
