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
        if row.source_id and row.source_id != found['source_id']:
            # Another recording's timing: an auto-sync fitted to the old one no longer applies.
            row.rate = 1.0
            if row.offset_source == 'auto':
                row.offset_ms, row.offset_source = 0, ''
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
            'lines': row.lines or [], 'plain': row.plain, 'offset_ms': row.offset_ms, 'rate': row.rate or 1.0,
            'duration': row.duration,
            'offset_source': row.offset_source}


def set_offset(s: Session, video_id: str, offset_ms: int, source: str = 'manual',
               rate: float | None = None) -> MusicLyrics:
    """Shift the lyrics; auto-sync also sets the speed ratio, a manual nudge keeps it."""
    row = s.get(MusicLyrics, video_id)
    if not row or row.status != 'found':
        raise ValueError('no lyrics to shift')
    if rate is not None:
        row.rate = min(RATE_MAX, max(RATE_MIN, float(rate)))
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


WORD_MS = 450            # typical time to sing one word
LINE_MAX_MS = 6000       # a lyric line is assumed sung within this long when estimating word times
MIN_LINES = 3            # distinct lyric lines that must agree before a shift is trusted


def _timeline(cues: list[dict]) -> list[tuple[int, str]]:
    """Transcript words with an estimated time each.

    Speech-to-text returns whole chunks (often 6-12 s, cut at pauses) without word timing, so a word's
    time is interpolated from its position inside its cue. Music confuses the recogniser: non-Latin
    hallucinations normalise away to nothing, and filler runs ("oh, oh, oh ...", the same word three or
    more times in a row) are dropped."""
    out: list[tuple[int, str]] = []
    for c in cues:
        toks = norm(c['text']).split()
        if not toks:
            continue
        span = max(1, c['end'] - c['t'])
        for i, w in enumerate(toks):
            run = (i >= 2 and toks[i - 1] == w == toks[i - 2]) or (i + 2 < len(toks) and toks[i + 1] == w == toks[i + 2]) \
                or (0 < i < len(toks) - 1 and toks[i - 1] == w == toks[i + 1])
            if not run:
                out.append((c['t'] + int(span * (i + 0.5) / len(toks)), w))
    return out


def anchors(lines: list[dict], cues: list[dict]) -> list[tuple[int, int, int]]:
    """(lyric line, lyric time ms, video time ms) for every word pair of a lyric line that also occurs, in
    order, in the transcript. A chorus yields one candidate per repeat; only one of them fits the rest."""
    words = _timeline(cues)
    where: dict[tuple[str, str], list[int]] = {}
    for j in range(len(words) - 1):
        where.setdefault((words[j][1], words[j + 1][1]), []).append(j)
    out = []
    for n, ln in enumerate(lines):
        toks = norm(ln['text']).split()
        if len(toks) < 2:
            continue
        nxt = lines[n + 1]['t'] if n + 1 < len(lines) else ln['t'] + LINE_MAX_MS
        # A line takes about WORD_MS per word to sing, and never runs into the next one.
        dur = max(500, min(LINE_MAX_MS, nxt - ln['t'], len(toks) * WORD_MS))
        for k in range(len(toks) - 1):
            sung = ln['t'] + int(dur * (k + 0.5) / len(toks))
            for j in where.get((toks[k], toks[k + 1]), []):
                if abs(words[j][0] - sung) <= MAX_OFFSET_MS:
                    out.append((n, sung, words[j][0]))
    return out


RATE_MIN, RATE_MAX = 0.85, 1.2  # a music-video edit runs at most this much slower or faster than the record
FIT_MS = 1800                    # an anchor this close to the fitted line agrees with it


def _support(votes, rate: float, offset: float) -> tuple[int, list]:
    agree = [v for v in votes if abs(v[2] - (rate * v[1] + offset)) <= FIT_MS]
    return len({v[0] for v in agree}), agree


def fit(lines: list[dict], cues: list[dict], hint_rate: float = 1.0) -> dict | None:
    """Map lyric time to video time as video = rate * lyric + offset.

    Most videos only start late or early (rate 1). Some are a different edit or speed of the record, and
    then the shift grows through the song, so one offset fits nowhere. Constant shifts and every line
    through two anchors on distant lines are tried; the one most distinct lyric lines agree with wins
    (RANSAC-style, so a repeated chorus cannot outvote the song), and a least-squares pass over those
    lines refines it. A speed other than 1 must explain clearly more lines, unless the video's length
    already says it runs slower or faster (`hint_rate` = video length / lyrics length, 3 % or more off):
    then a speed near that hint only has to match as many lines. None when fewer than MIN_LINES agree."""
    votes = anchors(lines, cues)
    if len({v[0] for v in votes}) < MIN_LINES:
        return None
    shifts = sorted(v[2] - v[1] for v in votes)
    best = (0, 1.0, 0.0)
    for sh in shifts[::max(1, len(shifts) // 400)]:
        n, _ = _support(votes, 1.0, sh)
        if n > best[0]:
            best = (n, 1.0, float(sh))
    flat = best
    sample = votes[::max(1, len(votes) // 120)]
    for i, a in enumerate(sample):
        for b in sample[i + 1:]:
            if a[0] == b[0] or abs(b[1] - a[1]) < 30_000:
                continue
            rate = (b[2] - a[2]) / (b[1] - a[1])
            if not RATE_MIN <= rate <= RATE_MAX:
                continue
            off = a[2] - rate * a[1]
            n, _ = _support(votes, rate, off)
            if n > best[0]:
                best = (n, rate, off)
    hinted = abs(hint_rate - 1) >= 0.03 and abs(best[1] - hint_rate) <= 0.04
    if best[1] != 1.0 and best[0] < flat[0] + (0 if hinted else max(2, flat[0] // 5)):
        best = flat  # a speed change must earn its keep
    n, rate, off = best
    if n < MIN_LINES:
        return None
    _, agree = _support(votes, rate, off)
    per_line: dict[int, list] = {}
    for v in agree:
        per_line.setdefault(v[0], []).append(v)
    pts = [(statistics.median(v[1] for v in vs), statistics.median(v[2] for v in vs)) for vs in per_line.values()]
    if rate == 1.0 or len(pts) < 4:
        rate, off = 1.0, statistics.median(y - x for x, y in pts)
    else:
        mx, my = statistics.fmean(x for x, _ in pts), statistics.fmean(y for _, y in pts)
        sxx = sum((x - mx) ** 2 for x, _ in pts)
        if sxx > 0:
            rate = min(RATE_MAX, max(RATE_MIN, sum((x - mx) * (y - my) for x, y in pts) / sxx))
            off = my - rate * mx
    if abs(off) > MAX_OFFSET_MS:
        return None  # clamping the intercept alone would skew every line
    return {'offset_ms': int(round(off)), 'rate': round(rate, 4), 'lines': len(pts)}


def align_offset(lines: list[dict], cues: list[dict]) -> int | None:
    """The constant part of the fit, for callers that only shift."""
    f = fit(lines, cues)
    return f['offset_ms'] if f else None
