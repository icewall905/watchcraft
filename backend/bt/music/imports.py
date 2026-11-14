"""Bounded playlist imports. Music Assistant's destination is operator configured only."""
from __future__ import annotations

import os
import re
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx

from .. import ytdl
from . import service, yt

MAX_TRACKS = 300
MAX_MA_BYTES = 8 * 1024 * 1024


class UnsupportedVersion(ValueError):
    pass


def configured() -> bool:
    return bool(os.environ.get('MUSIC_ASSISTANT_URL', '').strip() and
                os.environ.get('MUSIC_ASSISTANT_TOKEN', '').strip())


def youtube_id(reference: str) -> str:
    u = urlsplit(reference.strip())
    if (u.scheme not in ('https', 'http') or u.hostname not in
            ('youtube.com', 'www.youtube.com', 'music.youtube.com', 'm.youtube.com', 'youtu.be')
            or u.username or u.password or u.port not in (None, 80, 443)):
        raise ValueError('Paste a YouTube or YouTube Music playlist link.')
    ids = parse_qs(u.query).get('list', [])
    if len(ids) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{10,100}', ids[0]):
        raise ValueError('The YouTube link must contain a playlist (list=…).')
    return ids[0]


def _youtube(reference: str) -> tuple[str, list[dict], bool]:
    playlist_id = youtube_id(reference)
    info = ytdl._extract(f'https://www.youtube.com/playlist?list={playlist_id}', 'music', 'user',
                         f'music playlist {playlist_id}', extract_flat='in_playlist', playlistend=MAX_TRACKS + 1)
    entries = list(info.get('entries') or [])
    tracks = []
    for e in entries[:MAX_TRACKS]:
        if not isinstance(e, dict) or not yt.VID_RE.fullmatch(str(e.get('id') or '')):
            tracks.append({})
            continue
        tracks.append({'video_id': e['id'], 'title': str(e.get('title') or '')[:300],
                       'artist': str(e.get('artist') or e.get('uploader') or e.get('channel') or '')[:200],
                       'duration_s': max(0, min(int(e.get('duration') or 0), 86400)),
                       'views': max(0, min(int(e.get('view_count') or 0), 10**13))})
    return str(info.get('title') or 'Imported YouTube playlist'), tracks, len(entries) > MAX_TRACKS


def _ma_tracks(reference: str) -> tuple[str, list[dict], bool]:
    match = re.fullmatch(r'([A-Za-z0-9_-]+)://playlist/([^\s?#]{1,250})', reference.strip())
    if not match:
        raise ValueError('Use a Music Assistant playlist URI, such as library://playlist/13.')
    if not configured():
        raise ValueError('Music Assistant is not connected. Set MUSIC_ASSISTANT_URL and MUSIC_ASSISTANT_TOKEN on the WatchCraft server.')
    base = os.environ['MUSIC_ASSISTANT_URL'].strip().rstrip('/')
    u = urlsplit(base)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('The configured Music Assistant server address is invalid.')
    try:
        # No redirects: never forward the server's access token to a different destination.
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            info = client.get(base + '/info')
            info.raise_for_status()
            server_info = info.json()
            version = re.match(r'^(\d+)\.(\d+)(?:\.|$)', str(server_info.get('server_version') or '')) if isinstance(server_info, dict) else None
            if not version or tuple(map(int, version.groups())) < (2, 11):
                raise UnsupportedVersion('Music Assistant 2.11 or newer is required for bounded playlist imports. Upgrade Music Assistant and try again.')
            args = {'item_id': match[2], 'provider_instance_id_or_domain': match[1],
                    'limit': MAX_TRACKS + 1, 'offset': 0}
            with client.stream('POST', base + '/api', headers={
                    'Authorization': 'Bearer ' + os.environ['MUSIC_ASSISTANT_TOKEN'].strip()},
                    json={'message_id': 'watchcraft-import', 'command': 'music/playlists/playlist_tracks',
                          'args': args}) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_MA_BYTES:
                        raise ValueError('Music Assistant playlist response is too large.')
                    chunks.append(chunk)
                content = b''.join(chunks)
                error = content.decode('utf-8', errors='replace').lower()
                unsupported = (response.status_code in (400, 500) and
                               ('limit' in error or 'offset' in error) and
                               any(word in error for word in ('unexpected', 'unknown', 'invalid argument')))
                if unsupported:
                    raise UnsupportedVersion('Music Assistant 2.11 or newer is required for bounded playlist imports. Upgrade Music Assistant and try again.')
                response.raise_for_status()
                payload = json.loads(content)
    except UnsupportedVersion:
        raise
    except (httpx.HTTPError, ValueError) as e:
        raise ValueError('Could not read Music Assistant. Check the server address, access token and playlist permissions.') from e
    # MA HTTP API returns the command result directly; recent versions use Listing.items.
    entries = payload.get('items') if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError('Music Assistant returned an unsupported playlist response.')
    limited = len(entries) > MAX_TRACKS or (isinstance(payload, dict) and int(payload.get('total') or 0) > MAX_TRACKS)
    return 'Imported Music Assistant playlist', entries[:MAX_TRACKS], limited


def _resolve_ma(entry: dict) -> dict | None:
    if not isinstance(entry, dict) or entry.get('media_type', 'track') != 'track':
        return None
    title = str(entry.get('name') or '')[:300]
    artists = entry.get('artists') or []
    artist = ', '.join(str(a.get('name') or '') for a in artists if isinstance(a, dict))[:200]
    mappings = entry.get('provider_mappings') or []
    for p in [entry, *mappings]:
        if not isinstance(p, dict):
            continue
        provider = str(p.get('provider_domain') or p.get('provider') or '')
        vid = str(p.get('item_id') or '')
        if provider in ('ytmusic', 'youtube') and yt.VID_RE.fullmatch(vid):
            return {'video_id': vid, 'title': title, 'artist': artist}
    if not title or not artist:
        return None
    # Require both artist and song title: an artist-only fallback can import a different song.
    a = service.norm(artist)
    if not a or not service.norm(title):
        return None
    def contains_phrase(short: str, long: str) -> bool:
        return f' {short} ' in f' {long} '
    def song_title(text: str) -> str:
        text = re.sub(r'\([^)]*\b(?:official|video|remaster|hd|4k)\b[^)]*\)|\[[^]]*\b(?:official|video|remaster|hd|4k)\b[^]]*\]', '', text, flags=re.I)
        result = service.norm(text)
        result = re.sub(r'\s+(?:official music video|official video|music video|official hd video|official 4k video)$', '', result)
        if result.startswith(a + ' '):
            result = result[len(a) + 1:]
        return result
    wanted_title = song_title(title)
    if not wanted_title or wanted_title in ('official video', 'official music video', 'music video'):
        return None
    try:
        for hit in yt.search(f'{artist} {title}')[:6]:
            ha, ht = service.norm(hit.get('artist') or ''), service.norm(hit.get('title') or '')
            # Strip presentation labels but require the actual song name to match exactly.
            if ha and ht and (contains_phrase(a, ha) or contains_phrase(ha, a)) and wanted_title == song_title(hit.get('title') or ''):
                return hit
    except ytdl.YtError:
        return None
    return None


def playlist(source: str, reference: str) -> dict:
    if source == 'youtube':
        name, entries, limited = _youtube(reference)
    else:
        name, raw, limited = _ma_tracks(reference)
        with ThreadPoolExecutor(4) as pool:
            entries = list(pool.map(_resolve_ma, raw))
    seen, tracks, skipped, duplicates = set(), [], 0, 0
    for t in entries:
        if not t or not yt.VID_RE.fullmatch(t.get('video_id') or ''):
            skipped += 1
        elif t['video_id'] in seen:
            duplicates += 1
        else:
            seen.add(t['video_id'])
            tracks.append(t)
    if not tracks:
        raise ValueError('No playable YouTube tracks were found in this playlist.')
    return {'name': name, 'tracks': tracks, 'skipped': skipped, 'duplicates': duplicates, 'limited': limited}
