"""Quick lyrics auto-sync: listen to a few samples of the song, not all of it.

Lining up timed lyrics needs a handful of recognised lines spread over the song, not a full transcript.
So instead of the household subtitle queue (serial, whole song, chunk after chunk), this job takes the
saved offline copy when there is one (music is stored offline) or fetches the audio once, transcribes up
to SAMPLES short chunks spread across the song, re-fits after each one and stops as soon as the fit
holds steady. Every step reports a plain-language state for the karaoke view, and a failure ends the job
with its reason instead of retrying silently.

Jobs live in this process (the API runs as one uvicorn worker; several workers would each keep their own
jobs): one runs at a time (speech-to-text is a shared, often single-GPU service), every job has a hard
DEADLINE_S after which it stops and fails, and it is forgotten an hour after it ends.
"""
from __future__ import annotations

import logging
import secrets
import shutil
import tempfile
import threading
import time
import wave
from pathlib import Path

from .. import stt, subtitle_jobs
from ..config import DATA_DIR
from ..db import session
from . import lyrics
from .models import MusicLyrics, MusicTrack

log = logging.getLogger(__name__)

SAMPLES = 10          # at most this many chunks are transcribed
SAMPLES_SPEED = 16    # ... when the video's length suggests it runs slower or faster than the lyrics
MIN_SAMPLES = 4       # before an early stop is considered
STEADY_MS = 400       # two fits this close (and same speed) mean the answer is settled
KEEP_S = 3600
DEADLINE_S = 8 * 60   # a job still running after this stops itself (slow download, stuck STT)
STALE_S = 9 * 60      # a job without news for this long counts as dead, so it can be started again
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_slot = threading.Semaphore(1)


def spawn(fn, *a) -> None:
    """Tests replace this to run inline."""
    threading.Thread(target=fn, args=a, daemon=True).start()


def state(video_id: str) -> dict | None:
    with _lock:
        now = time.time()
        for vid in [v for v, j in _jobs.items() if j.get('ended') and now - j['ended'] > KEEP_S]:
            _jobs.pop(vid, None)
        job = _jobs.get(video_id)
        # A job waiting its turn behind other songs is not dead; only a running one can go quiet.
        if job and not job.get('ended') and job.get('state') != 'waiting' and now - job.get('updated', 0) >= STALE_S:
            job.update(state='failed', message='Listening took too long and was stopped. Try again.', ended=now)
        return {k: v for k, v in job.items() if k != 'token'} if job else None


def _set(video_id: str, token: str, **values) -> bool:
    """Update this job's state; a thread whose job was replaced (token changed) writes nothing."""
    with _lock:
        job = _jobs.get(video_id)
        if not job or job.get('token') != token:
            return False
        job.update(values, updated=time.time())
        return True


def _sweep_temp() -> None:
    """Remove audio left behind by a job that died with the process (daemon threads skip cleanup)."""
    root = DATA_DIR / 'subtitle-tmp'
    if not root.is_dir():
        return
    now = time.time()
    for path in root.glob('*-lyrics-*'):
        try:
            if path.is_dir() and not path.is_symlink() and now - path.stat().st_mtime > KEEP_S:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def start(video_id: str) -> dict:
    """Start a quick sync unless one is already under way for this song. Returns its state."""
    _sweep_temp()
    with _lock:
        job = _jobs.get(video_id)
        if job and not job.get('ended') and (job.get('state') == 'waiting'
                                             or time.time() - job.get('updated', 0) < STALE_S):
            return {k: v for k, v in job.items() if k != 'token'}
        token = secrets.token_hex(8)  # a stale thread of an older job can no longer write here
        _jobs[video_id] = {'state': 'waiting', 'progress': 0, 'message': 'Waiting for the listener…',
                           'started': time.time(), 'updated': time.time(), 'token': token}
    spawn(run, video_id, token)
    return state(video_id) or {}


def positions(duration: float, chunk: float = subtitle_jobs.CHUNK_SECONDS, n: int = SAMPLES) -> list[float]:
    """Chunk start times spread over the song, ordered so any first few already span it (for the speed)."""
    usable = max(0.0, duration - chunk)
    count = max(1, min(n, int(duration // chunk)))
    grid = [usable * (i + 0.5) / count for i in range(count)]
    bits = max(1, (count - 1).bit_length())
    # Bit-reversed order (0, 4, 2, 6, 1, 5, ...): any first few samples already span the song.
    order = sorted(range(count), key=lambda i: int(format(i, f'0{bits}b')[::-1], 2))
    return [grid[i] for i in order]


def _audio(video_id: str, directory: Path, stop: threading.Event) -> Path:
    def beat():
        if stop.is_set():
            raise subtitle_jobs.SubtitleError('Listening took too long and was stopped. Try again.')
    return subtitle_jobs._source(video_id, directory, beat, stop)


def _fail(video_id: str, token: str, message: str) -> None:
    _set(video_id, token, state='failed', message=message, ended=time.time())


def run(video_id: str, token: str) -> None:
    with _slot:
        with _lock:
            if (_jobs.get(video_id) or {}).get('token') != token:
                return  # replaced while waiting its turn
        stop = threading.Event()
        deadline = threading.Timer(DEADLINE_S, stop.set)
        deadline.daemon = True
        deadline.start()
        try:
            _run(video_id, token, stop)
        except subtitle_jobs.SubtitleError as e:
            _fail(video_id, token, str(e))
        except Exception:  # noqa: BLE001 - the job must end with a reason, never hang
            log.exception('lyrics quick sync failed for %s', video_id)
            _fail(video_id, token, 'Listening failed. Try again, or nudge the lyrics by hand.')
        finally:
            deadline.cancel()


def _run(video_id: str, token: str, stop: threading.Event) -> None:
    with session() as s:
        row = s.get(MusicLyrics, video_id)
        track = s.get(MusicTrack, video_id)
        if not row or row.status != 'found' or not row.synced:
            return _fail(video_id, token, 'This song has no timed lyrics to line up.')
        lines, lyr_dur = list(row.lines or []), row.duration
        before = (row.offset_ms, row.rate, row.offset_source)  # a hand nudge while listening wins
        hint = track.duration_s / lyr_dur if track and track.duration_s and lyr_dur else 1.0
    full = _full_transcript(video_id)
    if full:  # the app transcribed the whole song before: fit on that at once, no listening needed
        return _finish(video_id, token, lines, full, hint, before)
    cfg = stt.config()
    if not subtitle_jobs.enabled(cfg):
        return _fail(video_id, token, 'Speech-to-text is turned off in Settings, so songs cannot be listened to.')
    _set(video_id, token, state='preparing', progress=3, message='Getting the song…')
    root = DATA_DIR / 'subtitle-tmp'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'{video_id}-lyrics-', dir=root) as temp:
        directory = Path(temp)
        source = _audio(video_id, directory, stop)
        duration = subtitle_jobs.normalize(source, directory / 'song.wav', lambda: None, stop)
        # A video whose length suggests another speed gets more samples: the drift shows only across the song.
        spots = positions(duration, n=SAMPLES_SPEED if abs(hint - 1) >= 0.03 else SAMPLES)
        cues: list[dict] = []
        last = None
        busy = heard = 0
        with wave.open(str(directory / 'song.wav'), 'rb') as wav:
            for i, at in enumerate(spots):
                _set(video_id, token, state='listening', progress=round(8 + 87 * i / len(spots)),
                     message=f'Listening… {i + 1} of {len(spots)}')
                got = subtitle_jobs.audio_chunk(wav, at)
                if not got:
                    continue
                _end, audio, begin, finish = got
                if stop.is_set():
                    raise subtitle_jobs.SubtitleError('Listening took too long and was stopped. Try again.')
                try:
                    text = subtitle_jobs.transcribe_chunk(audio, cfg, stop) if audio else ''
                    heard += bool(audio)
                except subtitle_jobs.TransientSubtitleError:
                    busy += 1  # the speech service is busy (it also serves subtitles): skip this sample
                    continue
                if text:
                    cues.append({'t': int(begin * 1000), 'end': int(finish * 1000), 'text': text})
                # A video whose length says it runs at another speed needs every sample to see the drift.
                if i + 1 >= MIN_SAMPLES and abs(hint - 1) < 0.03:
                    f = lyrics.fit(lines, sorted(cues, key=lambda c: c['t']), hint)
                    if f and last and abs(f['offset_ms'] - last['offset_ms']) <= STEADY_MS \
                            and abs(f['rate'] - last['rate']) < 0.003 and f['lines'] >= 6:
                        break  # two fits in a row agree: settled
                    last = f
    if busy and not heard:
        return _fail(video_id, token, 'The speech service is busy right now. Try again in a minute.')
    return _finish(video_id, token, lines, sorted(cues, key=lambda c: c['t']), hint, before)


def _full_transcript(video_id: str) -> list[dict]:
    """Cues of a finished whole-song transcript from the subtitle queue, if there is one."""
    from ..models import GeneratedSubtitles
    with session() as s:
        row = s.get(GeneratedSubtitles, video_id)
        if row and row.status == 'ready' and subtitle_jobs.usable_vtt(row.vtt):
            return lyrics.parse_vtt(row.vtt)
    return []


def _finish(video_id: str, token: str, lines: list[dict], cues: list[dict], hint: float, before: tuple) -> None:
    f = lyrics.fit(lines, cues, hint)
    if not f:
        return _fail(video_id, token, "Couldn't hear enough of the words to line them up — nudge them by hand.")
    sign = '+' if f['offset_ms'] >= 0 else ''
    with _lock:
        if (_jobs.get(video_id) or {}).get('token') != token:
            return  # replaced by a newer job meanwhile: its result counts, not ours
    with session() as s:
        row = s.get(MusicLyrics, video_id)
        if row and (row.offset_ms, row.rate, row.offset_source) != before:
            _set(video_id, token, state='done', progress=100, ended=time.time(), kept=True,
                 message=f"You set the timing yourself meanwhile, so it was kept "
                         f"(auto-sync found {sign}{f['offset_ms'] / 1000:.1f} s).")
            return
        lyrics.set_offset(s, video_id, f['offset_ms'], 'auto', f['rate'])
    speed = f" · video ×{f['rate']:.3f}" if abs(f['rate'] - 1) >= 0.005 else ''
    _set(video_id, token, state='done', progress=100, ended=time.time(), offset_ms=f['offset_ms'], rate=f['rate'],
         lines=f['lines'], message=f"Lined up on {f['lines']} lines ({sign}{f['offset_ms'] / 1000:.1f} s{speed})")
