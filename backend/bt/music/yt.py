"""YouTube Music lookups through yt-dlp's InnerTube client (same cookies, proxy and PO tokens as ytdl).

Only official music videos pass: YouTube Music tags each video with a `musicVideoType`, and
`MUSIC_VIDEO_TYPE_OMV` is its "Official Music Video" class. Covers, lyric uploads, reactions and
fan edits are `UGC` and art tracks (static audio) are `ATV`; both are dropped by normal search/radio.
The explicit background-mood search also accepts music uploads and art tracks for ambient listening.
The response parsing is ours; the request plumbing is yt-dlp's.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

import yt_dlp
from yt_dlp.utils import parse_count

from .. import ytdl
from ..db import log_yt_request

VIDEOS_PARAMS = 'EgWKAQIQAWoKEAoQAxAEEAkQBQ=='  # YouTube Music search filter "Videos"
OMV = 'MUSIC_VIDEO_TYPE_OMV'
CACHE_TTL = 6 * 3600
_cache: dict[tuple, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
# At most this many YouTube Music calls at once, whatever starts them (pages, DJ, mix builds).
_slots = threading.BoundedSemaphore(3)
YTCFG_TTL = 3600
_ytcfg: tuple[float, dict] | None = None
VID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
# Labels give away official uploads that are not a music video: lyric videos, visualisers, audio.
BACKGROUND_REJECT_RE = re.compile(r'\b(lyrics?|cover|karaoke|reaction)\b', re.I)
NOT_VIDEO_RE = re.compile(r'\b(lyrics?|lyric video|visuali[sz]er|official audio|audio only|cover|karaoke|reaction)\b', re.I)


def _walk(o: Any, key: str, out: list) -> list:
    if isinstance(o, dict):
        if isinstance(o.get(key), dict):
            out.append(o[key])
        for k, v in o.items():
            if k != key:
                _walk(v, key, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, key, out)
    return out


def _find(o: Any, key: str) -> Any:
    found = _walk(o, key, [])
    return found[0] if found else None


def _runs(o: dict | None) -> list[dict]:
    return [r for r in ((o or {}).get('runs') or []) if isinstance(r, dict)]


def _text(o: dict | None) -> str:
    return ''.join(str(r.get('text') or '') for r in _runs(o)).strip()


def duration(text: str) -> int:
    if not re.fullmatch(r'\d+(:\d{1,2}){1,2}', text or ''):
        return 0
    s = 0
    for part in text.split(':'):
        s = s * 60 + int(part)
    return s


def _views(text: str) -> int:
    m = re.match(r'([\d.,]+\s*[KMB]?)\s+views?', text or '', re.I)
    return int(parse_count(m[1].replace(' ', '')) or 0) if m else 0


def _artist(byline: list[dict]) -> tuple[str, str]:
    """Artist names and the first artist's channel id from a byline: 'A & B • 3M views • 4:07'."""
    names, aid = [], ''
    for r in byline:
        t = str(r.get('text') or '')
        if t.strip() == '•':
            break
        names.append(t)
        bid = ((r.get('navigationEndpoint') or {}).get('browseEndpoint') or {}).get('browseId') or ''
        if not aid and str(bid).startswith('UC'):
            aid = bid
    return ''.join(names).strip(), aid


def _track(vid: str, title: str, byline: list[dict], length: str = '', *, background: bool = False) -> dict | None:
    if not vid or not VID_RE.match(vid) or not title or (BACKGROUND_REJECT_RE if background else NOT_VIDEO_RE).search(title):
        return None
    artist, aid = _artist(byline)
    tail = [str(r.get('text') or '') for r in byline]
    views = next((_views(t) for t in tail if 'view' in t.lower()), 0)
    dur = duration(length) or next((duration(t.strip()) for t in reversed(tail) if duration(t.strip())), 0)
    return {'video_id': vid, 'title': title, 'artist': artist, 'artist_id': aid, 'views': views,
            'duration_s': dur, 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'}


def _music_type(o: dict) -> str:
    cfg = _find(o, 'watchEndpointMusicConfig') or {}
    return str(cfg.get('musicVideoType') or '')


def parse_search(resp: dict, *, background: bool = False) -> list[dict]:
    """Music results in order; broader music classes only for explicit background mode."""
    out, seen = [], set()
    for r in _walk(resp, 'musicResponsiveListItemRenderer', []):
        allowed = {OMV, 'MUSIC_VIDEO_TYPE_UGC', 'MUSIC_VIDEO_TYPE_ATV'} if background else {OMV}
        if _music_type(r) not in allowed:
            continue
        cols = [c.get('musicResponsiveListItemFlexColumnRenderer', {}).get('text') for c in r.get('flexColumns') or []]
        if len(cols) < 2:
            continue
        vid = ((r.get('playlistItemData') or {}).get('videoId')
               or ((_find(r, 'watchEndpoint') or {}).get('videoId')) or '')
        t = _track(vid, _text(cols[0]), _runs(cols[1]), background=background)
        if t and t['video_id'] not in seen:
            seen.add(t['video_id'])
            out.append(t)
    return out


def parse_radio(resp: dict) -> list[dict]:
    """Official music videos from a YouTube Music watch-next (radio) response."""
    out, seen = [], set()
    for r in _walk(resp, 'playlistPanelVideoRenderer', []):
        if _music_type(r.get('navigationEndpoint') or {}) != OMV:
            continue
        t = _track(str(r.get('videoId') or ''), _text(r.get('title')), _runs(r.get('longBylineText')),
                   _text(r.get('lengthText')))
        if t and t['video_id'] not in seen:
            seen.add(t['video_id'])
            out.append(t)
    return out


def _call(ep: str, query: dict, detail: str, origin: str) -> dict:
    global _ytcfg
    t = time.monotonic()
    try:
        with _slots, yt_dlp.YoutubeDL(ytdl._opts()) as ydl:
            ie = ydl.get_info_extractor('YoutubeMusicSearchURL')
            ie.initialize()
            # The web client config is a page download of its own: fetch it once an hour, not per call.
            cached = _ytcfg
            if cached and time.time() - cached[0] < YTCFG_TTL:
                ytcfg = cached[1]
            else:
                ytcfg = ie._download_ytcfg('web_music', detail) or {}
                if ytcfg:
                    _ytcfg = (time.time(), ytcfg)
            headers = ie.generate_api_headers(ytcfg=ytcfg, default_client='web_music')
            resp = ie._extract_response(item_id=detail, ep=ep, query=query, default_client='web_music',
                                        ytcfg=ytcfg, headers=headers)
    except Exception as e:  # noqa: BLE001 - DownloadError / ExtractorError / network
        _ytcfg = None  # a stale config can be the cause; the next call fetches a fresh one
        err = ytdl.classify(e)
        log_yt_request('music', origin, False, f'{detail}: {type(err).__name__}: {str(e)[:300]}')
        raise err from e
    log_yt_request('music', origin, True, f'{detail} ({time.monotonic() - t:.1f}s)')
    return resp or {}


def _cached(key: tuple, fn) -> list[dict]:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return [dict(t) for t in hit[1]]
    out = fn()
    with _lock:
        if len(_cache) > 500:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
        _cache[key] = (now, out)
    return [dict(t) for t in out]


def search(query: str, origin: str = 'user') -> list[dict]:
    """Official music videos for a free-text query (about 20 per page)."""
    q = re.sub(r'\s+', ' ', query or '').strip()[:200]
    if not q:
        return []
    return _cached(('s', q.lower()), lambda: parse_search(
        _call('search', {'query': q, 'params': VIDEOS_PARAMS}, f'music search {q!r}', origin)))


def background_search(query: str, origin: str = 'user') -> list[dict]:
    """Mood-only music search: allow ambient uploads, visualizers and static art tracks.

    Normal music-video search/radio remain official-video-only. This deliberately accepts
    nonofficial music uploads for background listening; it does not certify their content.
    """
    q = re.sub(r'\s+', ' ', query or '').strip()[:200]
    if not q:
        return []
    return _cached(('background', q.lower()), lambda: parse_search(
        _call('search', {'query': q}, f'background music search {q!r}', origin), background=True))


def radio(video_id: str, origin: str = 'user') -> list[dict]:
    """YouTube Music's radio for a track: up to ~50 related official videos, the seed first."""
    if not VID_RE.match(video_id or ''):
        return []
    return _cached(('r', video_id), lambda: parse_radio(
        _call('next', {'videoId': video_id, 'playlistId': f'RDAMVM{video_id}', 'isAudioOnly': False},
              f'music radio {video_id}', origin)))


def prepare(video_id: str) -> None:
    """Warm the stream info so the track starts at once when its turn comes."""
    try:
        ytdl.video_info(video_id, 'user')
    except Exception:  # noqa: BLE001 - a cold track still plays, only slower
        pass
