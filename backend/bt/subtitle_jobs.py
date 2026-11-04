"""Durable, one-at-a-time alternative captions from the administrator's STT service.

Qwen ASR supplies text without alignment. Short audio chunks end at nearby pauses;
readable two-line cues use approximate timing and never replace YouTube's tracks.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import html
import io
import json
import logging
import math
import os
import signal
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
from contextlib import suppress

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select
from yt_dlp import YoutubeDL

from . import captions, stt, ytdl
from .config import DATA_DIR
from .db import session
from .downloader.service import abs_path, VID_RE
from .models import Candidate, Category, Download, GeneratedSubtitles, Profile, Video, WatchHistory

log = logging.getLogger(__name__)
MAX_SECONDS = 4 * 3600
MAX_AUDIO_BYTES = 512_000_000
MAX_PENDING = 5
LEASE_SECONDS = 240
CHUNK_SECONDS = 12
RATE = 16000


class SubtitleError(Exception):
    pass


class TransientSubtitleError(SubtitleError):
    """Temporary remote STT failure eligible for a bounded durable retry."""


def enabled(cfg=None):
    cfg = cfg or stt.config()
    return bool(cfg.get('base_url') and cfg.get('subtitles_enabled'))


def _fingerprint(cfg):
    return hashlib.sha256(json.dumps([cfg.get(k, '') for k in ('base_url', 'model', 'language')]).encode()).hexdigest()


def status(video_id):
    with session() as s:
        row = s.get(GeneratedSubtitles, video_id)
        return status_out(video_id, row)


def usable_vtt(value):
    """Match the Reel player's strict timing/header checks before exposing a track."""
    if not value or not re.match(r'^WEBVTT(?:\s|$)', value.removeprefix('\ufeff')):
        return False
    readable = False
    timing = re.compile(r'^\s*((?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+'
                        r'((?:\d+:)?\d{2}:\d{2}\.\d{3})(?:\s|$)')
    for block in re.split(r'\n\s*\n', value.replace('\r', '')):
        lines = block.split('\n')
        at = next((n for n, line in enumerate(lines) if '-->' in line), None)
        if at is None:
            continue
        match = timing.match(lines[at])
        if not match:
            return False
        def seconds(stamp):
            result = 0
            for part in stamp.split(':'):
                result = result * 60 + float(part)
            return result
        try:
            start, end = map(seconds, match.groups())
        except (ValueError, OverflowError):
            return False
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            return False
        text_value = html.unescape(re.sub(r'<[^>]*>', '', '\n'.join(lines[at + 1:])))
        readable = readable or bool(text_value.strip())
    return readable


def status_out(video_id, row, configured=None):
    valid = bool(row and row.status == 'ready' and usable_vtt(row.vtt))
    invalid = bool(row and row.status == 'ready' and not valid)
    state = 'failed' if invalid else row.status if row else 'idle'
    error = 'The saved subtitle track is invalid. Regenerate subtitles.' if invalid else row.error if row else ''
    result = {'enabled': enabled() if configured is None else configured, 'status': state,
              'progress': round(row.progress, 1) if row else 0, 'error': error,
              'timing_approximate': True, 'track': None,
              'retry_at': row.retry_at if row else 0}
    if valid:
        result['track'] = {'key': 'generated', 'lang': row.language or 'und', 'name': 'Generated (STT)',
                           'auto': False, 'url': f'/api/video/{video_id}/subtitles/generated.vtt'}
    return result


def enqueue(video_id, pid, local_only=False, regenerate=False):
    if not VID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Invalid video id')
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(GeneratedSubtitles, video_id)
        if row and (row.status in ('queued', 'running') or
                    (row.status == 'ready' and usable_vtt(row.vtt) and not regenerate)):
            if row.status == 'queued':
                row.updated_at = 0  # requested clip precedes background backfill
                row.retry_count, row.retry_at = 0, 0
            row.local_only = row.local_only or local_only
            s.add(row); s.commit()
            return
        if not enabled():
            raise HTTPException(503, 'Configure generated subtitles under Settings → Player.')
        count = len(s.exec(select(GeneratedSubtitles.video_id).where(
            GeneratedSubtitles.status.in_(('queued', 'running')))).all())
        if count >= MAX_PENDING:
            raise HTTPException(429, 'The subtitle queue is full. Please try again when a video finishes.')
        row = row or GeneratedSubtitles(video_id=video_id)
        if regenerate and row.status in ('ready', 'failed'):
            row.cues, row.vtt = [], ''
        if not row.cues:
            row.completed_s, row.progress = 0, 0
        row.status, row.error, row.requested_by = 'queued', '', pid
        row.local_only = row.local_only or local_only
        row.updated_at, row.token, row.lease_until = 0, '', 0
        row.retry_count, row.retry_at = 0, 0
        # Retry keeps completed chunks from this same model/language/server.
        s.add(row); s.commit()


def backfill():
    """Fill spare STT slots from saved feed clips, without retries or source fetches."""
    if not enabled():
        return 0
    from .access import has_children
    from . import profile_pins
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        pending = len(s.exec(select(GeneratedSubtitles.video_id).where(
            GeneratedSubtitles.status.in_(('queued', 'running')))).all())
        spare = MAX_PENDING - 1 - pending  # keep room for the currently viewed clip
        if spare <= 0:
            return 0
        children = has_children(s)
        allowed = {p.id for p in s.exec(select(Profile)).all()
                   if not children or p.role == 'child' or profile_pins.record(s, p.id)}
        if not allowed:
            return 0
        rows = s.exec(select(Candidate.video_id, Category.profile_id, Download.path)
            .join(Category, Category.id == Candidate.category_id)
            .join(Video, Video.id == Candidate.video_id)
            .join(Download, Download.video_id == Candidate.video_id)
            .outerjoin(GeneratedSubtitles, GeneratedSubtitles.video_id == Candidate.video_id)
            .where(Category.profile_id.in_(allowed), Category.kind == 'shorts', Category.enabled == True,
                   Candidate.status == 'accepted', Candidate.dismissed == False, Video.is_short == True,
                   Download.status == 'done', Video.duration_s > 0, Video.duration_s <= 180,
                   GeneratedSubtitles.video_id == None)
            .order_by(Category.priority.desc(), Candidate.score.desc(), Candidate.id)).all()
        finished = set(s.exec(select(WatchHistory.profile_id, WatchHistory.video_id).where(
            WatchHistory.profile_id.in_(allowed), WatchHistory.finished == True)).all())
        added = set()
        for vid, pid, relative in rows:
            if vid in added or (pid, vid) in finished or not VID_RE.fullmatch(vid):
                continue
            path = abs_path(relative) if relative else None
            try:
                if not path or not path.is_file() or not path.stat().st_size:
                    continue
                with path.open('rb') as source:
                    if not source.read(1):
                        continue
            except OSError:
                continue
            s.add(GeneratedSubtitles(video_id=vid, requested_by=pid, local_only=True,
                                     updated_at=time.time()))
            added.add(vid)
            if len(added) >= spare:
                break
        s.commit()
        return len(added)


def claim():
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        now = time.time()
        # A live job in another process keeps the household STT queue serial.
        if s.exec(select(GeneratedSubtitles.video_id).where(
                GeneratedSubtitles.status == 'running', GeneratedSubtitles.lease_until > now)).first():
            return None
        row = s.exec(select(GeneratedSubtitles).where(
            ((GeneratedSubtitles.status == 'queued') & (GeneratedSubtitles.retry_at <= now)) |
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


def _source(video_id, directory, beat, stop, local_only=False):
    with session() as s:
        download = s.get(Download, video_id)
        path = abs_path(download.path) if download and download.status == 'done' else None
        row = s.get(GeneratedSubtitles, video_id)
        local_only = local_only or bool(row and row.local_only)
    if path and path.is_file():
        return path
    # A viewing request may have upgraded an already running job after run()
    # took its snapshot. Re-read immediately before any remote source fallback.
    with session() as s:
        latest = s.get(GeneratedSubtitles, video_id)
        local_only = local_only or bool(latest and latest.local_only)
    if local_only:
        raise SubtitleError('The saved video is no longer available. Save it offline before retrying.')
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


def audio_chunk(wav, offset, chunk_seconds=CHUNK_SECONDS):
    """Use a pause near the chunk end, and don't ask ASR to hallucinate silence."""
    wav.setpos(round(offset * RATE))
    pcm = wav.readframes(chunk_seconds * RATE)
    samples = array.array('h', pcm)
    if sys.byteorder != 'little': samples.byteswap()
    if not samples:
        return None
    step = RATE // 10
    energy = [math.sqrt(sum(v*v for v in samples[n:n+step]) / len(samples[n:n+step])) / 32768
              for n in range(0, len(samples), step)]
    end = len(samples) / RATE
    # Three quiet 100 ms windows provide a natural break, without arbitrary word overlap.
    pauses = [n / 10 for n in range(min(60, chunk_seconds * 5), len(energy)-2) if max(energy[n:n+3]) < .002]
    if pauses and end >= chunk_seconds:
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


def transcribe_chunk(audio, cfg, stop=None):
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
                        error_type = TransientSubtitleError if response.status_code >= 500 or response.status_code == 429 else SubtitleError
                        raise error_type(f'The STT service returned HTTP {response.status_code}. Check Settings → Player.')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 200_000: raise SubtitleError('The STT response was too large.')
                    return json.loads(raw)
    async def interruptible():
        pending = asyncio.create_task(request())
        try:
            while not pending.done():
                if stop and stop.is_set():
                    raise SubtitleError('Subtitle generation stopped; retry to continue.')
                await asyncio.wait({pending}, timeout=.25)
            return await pending
        finally:
            if not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
    try:
        data = asyncio.run(interruptible())
        value = data.get('text') if isinstance(data, dict) else None
        if not isinstance(value, str): raise ValueError()
        return ' '.join(value.split())[:20000]
    except SubtitleError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise TransientSubtitleError('The STT service took too long. Retry to continue from the last completed chunk.') from None
    except httpx.HTTPError:
        raise TransientSubtitleError('The STT service could not be reached. Check Settings → Player.') from None
    except ValueError:
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
            source = _source(video_id, directory, beat, stop, row.local_only)
            duration = normalize(source, directory / 'normalized.wav', beat, stop)
            if offset > duration: cues, offset = [], 0
            with wave.open(str(directory / 'normalized.wav'), 'rb') as wav:
                while offset < duration - .001:
                    beat()
                    got = audio_chunk(wav, offset, 6 if row.local_only else CHUNK_SECONDS)
                    if not got: break
                    end, audio, start, finish = got
                    value = transcribe_chunk(audio, cfg, stop) if audio else ''
                    if value: cues.append({'start': start, 'end': finish, 'text': value})
                    offset = end
                    _update(video_id, token, cues=cues, completed_s=offset,
                            progress=min(99, 5 + 94 * offset / duration), duration_s=duration)
        if not cues: raise SubtitleError('No speech was recognized in this video.')
        _update(video_id, token, status='ready', progress=100, vtt=make_vtt(cues), error='',
                retry_count=0, retry_at=0)
    except Exception as exc:
        message = str(exc) if isinstance(exc, SubtitleError) else 'Subtitle generation failed. Please retry.'
        try:
            if stop.is_set():
                _update(video_id, token, status='queued', error='', retry_at=0)
            elif isinstance(exc, TransientSubtitleError):
                with session() as s:
                    latest = s.get(GeneratedSubtitles, video_id)
                    count = latest.retry_count if latest else 3
                if count < 3:
                    _update(video_id, token, status='queued', error=message,
                            retry_count=count + 1, retry_at=time.time() + (60, 300, 900)[count])
                else:
                    _update(video_id, token, status='failed', error=message, retry_at=0)
            else:
                _update(video_id, token, status='failed', error=message, retry_at=0)
        except SubtitleError:
            pass
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


async def worker(stop=None):
    stop = stop or threading.Event()
    next_backfill = 0
    try:
        while not stop.is_set():
            try:
                await asyncio.to_thread(cleanup_temp)
                if time.monotonic() >= next_backfill:
                    await asyncio.to_thread(backfill)
                    next_backfill = time.monotonic() + 15
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


HEARTBEAT_NAME = 'subtitle-worker-heartbeat'


def healthy(path=None):
    """Check this container's daemon PID and a recent event-loop heartbeat."""
    path = path or DATA_DIR / HEARTBEAT_NAME
    try:
        pid = int(path.read_text().strip())
        age = time.time() - path.stat().st_mtime
        if pid <= 0 or not 0 <= age <= 90:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


async def heartbeat(stop, path):
    while not stop.is_set():
        path.write_text(str(os.getpid()))
        # Keep this independent of potentially long-running audio/STT threads.
        for _ in range(60):
            if stop.is_set():
                return
            await asyncio.sleep(.25)


async def daemon():
    from .db import init_db
    init_db()
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT)
    for signum in signals:
        loop.add_signal_handler(signum, stop.set)
    path = DATA_DIR / HEARTBEAT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    pulse = asyncio.create_task(heartbeat(stop, path))
    try:
        await worker(stop)
    finally:
        stop.set()
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse
        for signum in signals:
            loop.remove_signal_handler(signum)
        # A shutdown never removes another daemon's heartbeat.
        with suppress(OSError):
            if path.read_text().strip() == str(os.getpid()):
                path.unlink()


def main():
    if sys.argv[1:] == ['--healthcheck']:
        raise SystemExit(0 if healthy() else 1)
    if sys.argv[1:]:
        raise SystemExit('Usage: python -m bt.subtitle_jobs [--healthcheck]')
    logging.basicConfig(level=logging.INFO)
    asyncio.run(daemon())


if __name__ == '__main__':
    main()
