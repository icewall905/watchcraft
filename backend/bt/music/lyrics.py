"""Song lyrics with timing, for the karaoke view.

Source: LRCLIB (lrclib.net), an open lyrics database with line-timed (LRC) and sometimes word-timed lyrics.
Among its matches we prefer timed lyrics whose length is closest to the video's, so a music-video edit
picks the version timed for it. Lyrics are cached per video; a miss is retried after a week.

Timing can still be off when a video has an intro or a skit. Two remedies: a shared per-video offset that
anyone can nudge, and auto-sync, which lines the lyrics up with the speech-to-text transcript the app can
generate for any video (`subtitle_jobs`). That transcript is also the karaoke text for songs LRCLIB lacks.
"""
from __future__ import annotations

import logging
import re
import statistics
import time

import html

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from .models import MusicLyrics, MusicTrack

log = logging.getLogger(__name__)

API = 'https://lrclib.net/api'
UA = 'WatchCraft music karaoke (https://github.com/icewall905/watchcraft)'
MISS_RETRY_S = 7 * 86400
MAX_OFFSET_MS = 120_000

# Video-title noise that is not part of the song name.
_NOISE = re.compile(r'\s*[\(\[][^\)\]]*(official|video|audio|visuali[sz]er|lyric|hd|hq|4k|remaster|live at|'
                    r'explicit|clean|m/v|mv)[^\)\]]*[\)\]]', re.I)
_FEAT = re.compile(r'\s*[\(\[]?\s*(feat\.?|ft\.?|featuring)\s.*$', re.I)
_TS = re.compile(r'\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]')
_WORD = re.compile(r'<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>\s*([^<]*)')


def clean_title(title: str, artist: str = '') -> str:
    t = _NOISE.sub('', title or '')
    t = _FEAT.sub('', t).strip()
    if artist and ' - ' in t:  # "Artist - Song" uploads
        a, _, rest = t.partition(' - ')
        if norm(a) and (norm(a) in norm(artist) or norm(artist) in norm(a)):
            t = rest
    return t.strip(' -–|') or (title or '').strip()


def main_artist(artist: str) -> str:
    return re.split(r'\s*(?:,|&| x | and | feat\.? | ft\.? )\s*', artist or '', maxsplit=1, flags=re.I)[0].strip()


def norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (s or '').lower()).strip()


def _ms(m: str, s: str, frac: str | None) -> int:
    f = (frac or '0').ljust(3, '0')[:3]
    return (int(m) * 60 + int(s)) * 1000 + int(f)


def parse_lrc(text: str) -> list[dict]:
    """LRC lines -> [{t, text, words?}] sorted by time. Enhanced LRC `<mm:ss.xx>` word stamps become words."""
    out = []
    for raw in (text or '').splitlines():
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        body = raw[stamps[-1].end():].strip()
        words = [{'t': _ms(*w.groups()[:3]), 'text': w.group(4).strip()} for w in _WORD.finditer(body)]
        words = [w for w in words if w['text']]
        line = _WORD.sub(lambda w: w.group(4), body).strip() if words else body
        for st in stamps:  # "[00:12.00][01:30.00] chorus" repeats a line
            item = {'t': _ms(*st.groups()), 'text': line}
            if words:
                item['words'] = words
            out.append(item)
    out.sort(key=lambda x: x['t'])
    return out


def _fetch(params: dict) -> list[dict]:
    with httpx.Client(timeout=6, headers={'User-Agent': UA}) as c:
        r = c.get(f'{API}/search', params=params)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def best_match(cands: list[dict], artist: str, title: str, duration: int) -> dict | None:
    """Timed lyrics first, then the closest length to the video; the artist and title must fit."""
    a, t = norm(main_artist(artist)), norm(title)
    best, best_key = None, None
    for c in cands:
        ca, ct = norm(c.get('artistName') or ''), norm(c.get('trackName') or '')
        if c.get('instrumental') or not (c.get('syncedLyrics') or c.get('plainLyrics')):
            continue
        if a and not (a in ca or ca in a):
            continue
        if t and not (t in ct or ct in t):
            continue
        gap = abs(float(c.get('duration') or 0) - duration) if duration else 0
        if duration and gap > 45:  # a different recording (extended mix, live) times nothing right
            continue
        key = (0 if c.get('syncedLyrics') else 1, 0 if c.get('hasWordSync') else 1, round(gap), -len(ct))
        if best_key is None or key < best_key:
            best, best_key = c, key
    return best


def lookup(artist: str, title: str, duration: int) -> dict | None:
    """Find lyrics on LRCLIB. None when there are none; raises on network failure."""
    song = clean_title(title, artist)
    lead = main_artist(artist)
    cands = _fetch({'track_name': song, 'artist_name': lead}) if lead else []
    hit = best_match(cands, lead, song, duration)
    if not hit:
        hit = best_match(_fetch({'q': f'{lead} {song}'.strip()}), lead, song, duration)
    if not hit:
        return None
    lines = parse_lrc(hit.get('syncedLyrics') or '')
    return {'source': 'lrclib', 'source_id': int(hit.get('id') or 0), 'synced': bool(lines), 'lines': lines,
            'plain': (hit.get('plainLyrics') or '').strip(), 'duration': float(hit.get('duration') or 0)}


def get(s: Session, video_id: str, refresh: bool = False) -> MusicLyrics:
    """Cached lyrics for a video, looked up on first use. Status is found, missing or error."""
    row = s.get(MusicLyrics, video_id)
    now = time.time()
    fresh = row and (row.status == 'found' or (row.status == 'missing' and now - row.fetched_at < MISS_RETRY_S)
                     or (row.status == 'error' and now - row.fetched_at < 600))
    if fresh and not refresh:
        return row
    track = s.get(MusicTrack, video_id)
    if not track:  # not a known music track: nothing to search for, nothing stored
        return row or MusicLyrics(video_id=video_id, status='missing', fetched_at=now)
    artist, title, duration = track.artist, track.title, track.duration_s
    s.rollback()  # end the read transaction: the lookup below is a slow network call
    try:
        found, status = lookup(artist, title, duration), None
    except Exception as e:  # noqa: BLE001 - network or API trouble: try again soon
        log.info('lyrics lookup failed for %s: %s', video_id, e)
        found, status = None, 'error'
    row = s.get(MusicLyrics, video_id) or MusicLyrics(video_id=video_id)
    if found:
        row.status, row.source, row.source_id = 'found', found['source'], found['source_id']
        row.synced, row.lines, row.plain, row.duration = found['synced'], found['lines'], found['plain'], found['duration']
    else:
        # A failed refresh of lyrics we already have keeps them showing; only a first failure is an error.
        row.status = 'found' if row.status == 'found' else (status or 'missing')
    row.fetched_at = time.time()
    try:
        s.add(row)
        s.commit()
    except IntegrityError:  # another request stored this video's lyrics while we were looking
        s.rollback()
        return s.get(MusicLyrics, video_id)
    s.refresh(row)
    return row


def as_dict(row: MusicLyrics) -> dict:
    return {'video_id': row.video_id, 'status': row.status, 'source': row.source, 'synced': row.synced,
            'lines': row.lines or [], 'plain': row.plain, 'offset_ms': row.offset_ms, 'duration': row.duration,
            'offset_source': row.offset_source}


def set_offset(s: Session, video_id: str, offset_ms: int, source: str = 'manual') -> MusicLyrics:
    row = s.get(MusicLyrics, video_id)
    if not row or row.status != 'found':
        raise ValueError('no lyrics to shift')
    row.offset_ms = max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, int(offset_ms)))
    row.offset_source = source
    s.add(row)
    s.commit()
    return row


# ---------- auto-sync against a speech-to-text transcript

_CUE = re.compile(r'((?:\d+:)?\d{2}:\d{2}\.\d{3})\s+-->\s+((?:\d+:)?\d{2}:\d{2}\.\d{3})')


def _vtt_ms(stamp: str) -> int:
    parts = stamp.split(':')
    h = int(parts[0]) if len(parts) == 3 else 0
    m, s = int(parts[-2]), float(parts[-1])
    return int(((h * 60 + m) * 60 + s) * 1000)


def parse_vtt(vtt: str) -> list[dict]:
    """WebVTT cues -> [{t, end, text}]."""
    out = []
    for block in re.split(r'\n\s*\n', (vtt or '').replace('\r', '')):
        lines = block.strip().split('\n')
        at = next((i for i, ln in enumerate(lines) if '-->' in ln), None)
        if at is None:
            continue
        m = _CUE.search(lines[at])
        text = html.unescape(' '.join(re.sub(r'<[^>]+>', '', ln).strip() for ln in lines[at + 1:]).strip())
        if m and text:
            out.append({'t': _vtt_ms(m[1]), 'end': _vtt_ms(m[2]), 'text': text})
    return out


def align_offset(lines: list[dict], cues: list[dict]) -> int | None:
    """How far the lyrics must shift to match the transcript (ms), from the median of confident matches.

    Each lyric line is matched to the transcript cue that shares most of its words (Jaccard >= 0.5) and that
    starts within two minutes of it. None when fewer than three lines match: then nothing is trustworthy."""
    def words(t):
        return set(norm(t).split())
    deltas = []
    for ln in lines:
        w = words(ln['text'])
        if len(w) < 3:
            continue
        best, score = None, 0.0
        for c in cues:
            if abs(c['t'] - ln['t']) > 120_000:
                continue
            cw = words(c['text'])
            j = len(w & cw) / len(w | cw) if cw else 0
            if j > score:
                best, score = c, j
        if best and score >= 0.5:
            deltas.append(best['t'] - ln['t'])
    if len(deltas) < 3:
        return None
    return int(statistics.median(deltas))
