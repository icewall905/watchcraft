"""Small Jellyfin REST client (10.9+ endpoints) plus the item shape the frontend uses.

The server URL is administrator-set and usually a LAN address, so this deliberately does not go
through `netfetch` (which only allows public HTTPS). Only the configured server is ever contacted.
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse as up
from typing import Any

import httpx
from fastapi import HTTPException

from ..db import get_kv, save_setting

CLIENT = 'The Hub'  # shown in Jellyfin's dashboard (Devices, Activity)
VERSION = '1.0.0'
TICKS = 10_000_000  # Jellyfin positions are 100 ns ticks
CONFIG_KEY = 'jellyfin'

# Tests swap in an httpx.MockTransport here.
transport: httpx.BaseTransport | None = None
async_transport: httpx.AsyncBaseTransport | None = None


def config() -> dict:
    return {'url': '', 'allow_children': False, **get_kv(CONFIG_KEY)}


def save_config(cfg: dict) -> None:
    save_setting(CONFIG_KEY, cfg)


def base_url() -> str:
    url = config()['url']
    if not url:
        raise HTTPException(409, 'Jellyfin is not set up. An administrator can add the server in Settings → Jellyfin.')
    return url


def normalize_url(raw: str) -> str:
    raw = raw.strip().rstrip('/')
    if raw and '://' not in raw:
        raw = 'http://' + raw
    p = up.urlsplit(raw)
    if p.scheme not in ('http', 'https') or not p.hostname or p.query or p.fragment or p.username:
        raise HTTPException(422, 'Enter the Jellyfin address, for example http://192.168.1.20:8096')
    return raw


def auth_header(device_id: str, token: str = '') -> str:
    parts = [f'Client="{CLIENT}"', f'Device="{CLIENT}"', f'DeviceId="{device_id}"', f'Version="{VERSION}"']
    if token:
        parts.append(f'Token="{token}"')
    return 'MediaBrowser ' + ', '.join(parts)


def _client(url: str, device_id: str, token: str = '') -> httpx.Client:
    return httpx.Client(base_url=url, transport=transport, timeout=httpx.Timeout(15.0),
                        headers={'Authorization': auth_header(device_id, token), 'Accept': 'application/json'})


def call(method: str, path: str, *, device_id: str, token: str = '', url: str | None = None,
         params: dict | None = None, json: Any = None) -> Any:
    """One Jellyfin request; maps failures to readable HTTP errors."""
    try:
        with _client(url or base_url(), device_id, token) as c:
            r = c.request(method, path, params={k: v for k, v in (params or {}).items() if v is not None}, json=json)
    except httpx.HTTPError as e:
        raise HTTPException(502, f'Cannot reach Jellyfin ({type(e).__name__}).') from e
    if r.status_code == 401:
        raise HTTPException(401 if path.endswith('AuthenticateByName') else 409,
                            'Jellyfin rejected the sign-in.' if path.endswith('AuthenticateByName')
                            else 'Jellyfin sign-in expired. Link this profile again in Settings → Jellyfin.')
    if r.status_code == 403:
        raise HTTPException(403, 'Jellyfin does not allow this for your Jellyfin user.')
    if r.status_code == 404:
        raise HTTPException(404, 'Not found on Jellyfin.')
    if r.status_code >= 400:
        raise HTTPException(502, f'Jellyfin error {r.status_code}.')
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text


_aclients: dict[int, httpx.AsyncClient] = {}


def aclient() -> httpx.AsyncClient:
    """Shared streaming client per event loop (media proxy)."""
    loop = id(asyncio.get_running_loop())
    c = _aclients.get(loop)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(transport=async_transport, follow_redirects=False,
                              timeout=httpx.Timeout(20.0, read=120.0),
                              limits=httpx.Limits(max_connections=64, max_keepalive_connections=16))
        _aclients[loop] = c
    return c


# ---------------------------------------------------------------- age ratings

# Minimum viewer age for named ratings (US film/TV, UK BBFC, AU, NL, misc). Ratings ending in a
# number (FSK-12, DE-6, 12A, FR-16, PEGI 7) use that number; anything unknown or unrated never
# matches an age filter, so kids only see titles with a rating we understand.
_NAMED_AGES = {
    'G': 0, 'TV-Y': 0, 'TV-G': 0, 'U': 0, 'UC': 0, 'E': 0, 'AL': 0, 'TP': 0, 'ALL': 0, 'APPROVED': 0,
    'TV-Y7': 7, 'TV-Y7-FV': 7, 'PG': 8, 'TV-PG': 8, 'M': 15, 'MA': 15, 'MA15+': 15,
    'PG-13': 13, 'TV-14': 14, 'R': 17, 'TV-MA': 17, 'NC-17': 18, 'X': 18, 'XXX': 18, 'R18+': 18,
}
_UNRATED = {'NR', 'UNRATED', 'NOT RATED', 'TBA', 'TBC', 'RP', 'NONE', 'NOTRATED'}


def rating_age(rating: str | None) -> int | None:
    """Minimum viewer age for an official rating string, or None if unknown."""
    r = (rating or '').strip().upper().replace('_', '-')
    if not r or r in _UNRATED:
        return None
    if r in _NAMED_AGES:
        return _NAMED_AGES[r]
    if '-' in r and r.split('-', 1)[1] in _NAMED_AGES and len(r.split('-', 1)[0]) == 2:
        return _NAMED_AGES[r.split('-', 1)[1]]  # country prefix: US-PG, GB-U
    m = re.search(r'(\d{1,2})\+?A?$', r)
    if m and int(m.group(1)) <= 21:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------- item shape

def _secs(ticks) -> float:
    return round((ticks or 0) / TICKS, 1)


def item(raw: dict, image_url) -> dict:
    """Compact item for the UI. `image_url(item_id, kind, tag)` builds a signed proxy URL."""
    tags = raw.get('ImageTags') or {}
    ud = raw.get('UserData') or {}
    iid = raw.get('Id', '')
    primary = image_url(iid, 'Primary', tags['Primary']) if tags.get('Primary') else None
    if not primary and raw.get('SeriesPrimaryImageTag') and raw.get('SeriesId'):
        primary = image_url(raw['SeriesId'], 'Primary', raw['SeriesPrimaryImageTag'])
    thumb = image_url(iid, 'Thumb', tags['Thumb']) if tags.get('Thumb') else None
    if not thumb and raw.get('ParentThumbItemId') and raw.get('ParentThumbImageTag'):
        thumb = image_url(raw['ParentThumbItemId'], 'Thumb', raw['ParentThumbImageTag'])
    if not thumb and raw.get('Type') == 'Episode' and tags.get('Primary'):
        thumb = primary  # episode stills are landscape already
    backdrops = raw.get('BackdropImageTags') or []
    backdrop = image_url(iid, 'Backdrop', backdrops[0]) if backdrops else None
    if not backdrop and raw.get('ParentBackdropItemId') and raw.get('ParentBackdropImageTags'):
        backdrop = image_url(raw['ParentBackdropItemId'], 'Backdrop', raw['ParentBackdropImageTags'][0])
    runtime = _secs(raw.get('RunTimeTicks'))
    position = _secs(ud.get('PlaybackPositionTicks'))
    out = {
        'id': iid,
        'name': raw.get('Name', ''),
        'type': raw.get('Type', ''),
        'collection_type': raw.get('CollectionType'),
        'year': raw.get('ProductionYear'),
        'overview': raw.get('Overview', ''),
        'runtime_s': runtime,
        'official_rating': raw.get('OfficialRating'),
        'community_rating': raw.get('CommunityRating'),
        'genres': raw.get('Genres') or [],
        'series_id': raw.get('SeriesId'),
        'series_name': raw.get('SeriesName'),
        'season_id': raw.get('SeasonId'),
        'season': raw.get('ParentIndexNumber'),
        'episode': raw.get('IndexNumber'),
        'premiere': (raw.get('PremiereDate') or '')[:10] or None,
        'is_folder': bool(raw.get('IsFolder')),
        'child_count': raw.get('ChildCount'),
        'unplayed': ud.get('UnplayedItemCount'),
        'played': bool(ud.get('Played')),
        'favorite': bool(ud.get('IsFavorite')),
        'position_s': position,
        'progress': round(position / runtime, 3) if runtime and position else 0,
        'image': primary,
        'thumb': thumb,
        'backdrop': backdrop,
        'playable': raw.get('Type') in ('Movie', 'Episode', 'Video', 'MusicVideo', 'Trailer'),
    }
    if raw.get('People'):
        out['people'] = [
            {'name': person['Name'], 'role': person.get('Role') or '', 'type': person.get('Type') or '',
             'image': image_url(person['Id'], 'Primary', person['PrimaryImageTag'])
             if person.get('Id') and person.get('PrimaryImageTag') else None}
            for person in raw['People'] if person.get('Name')
        ][:40]
    if raw.get('MediaStreams') is not None or raw.get('MediaSources'):
        streams = raw.get('MediaStreams') or ((raw.get('MediaSources') or [{}])[0].get('MediaStreams') or [])
        out['audio'] = audio_tracks(streams)
        out['subtitles'] = subtitle_tracks(streams)
        video = next((stream for stream in streams if stream.get('Type') == 'Video' and not stream.get('IsExternal')), None)
        if video:
            out['video'] = {'width': video.get('Width'), 'height': video.get('Height'),
                            'codec': video.get('Codec'), 'range': video.get('VideoRangeType') or video.get('VideoRange')}
    return out


def audio_tracks(streams: list[dict]) -> list[dict]:
    return [{'index': s['Index'], 'label': s.get('DisplayTitle') or s.get('Language') or f'Track {s["Index"]}',
             'lang': s.get('Language'), 'default': bool(s.get('IsDefault'))}
            for s in streams if s.get('Type') == 'Audio']


def subtitle_tracks(streams: list[dict]) -> list[dict]:
    return [{'index': s['Index'], 'label': s.get('DisplayTitle') or s.get('Language') or f'Subtitle {s["Index"]}',
             'lang': s.get('Language'), 'default': bool(s.get('IsDefault')), 'forced': bool(s.get('IsForced')),
             'external': bool(s.get('IsExternal')), 'text': bool(s.get('IsTextSubtitleStream'))}
            for s in streams if s.get('Type') == 'Subtitle']


# ---------------------------------------------------------------- device profile

SDR_RANGES = 'SDR|DOVIWithSDR'
HDR_RANGES = 'SDR|DOVIWithSDR|HDR10|HDR10Plus|HLG|DOVIWithHDR10|DOVIWithHDR10Plus|DOVIWithHLG'

def device_profile(caps: dict) -> dict:
    """What the browser can play. Direct play when it can; otherwise Jellyfin transcodes to HLS,
    using the server's hardware encoder when one is configured in Jellyfin."""
    video = ['h264'] + [c for c in ('hevc', 'av1', 'vp9') if caps.get(c)]
    audio = 'aac,mp3,opus,flac' + (',ac3,eac3' if caps.get('ac3') else '')
    max_h = caps.get('max_height') or 0
    conditions = [{'Condition': 'LessThanEqual', 'Property': 'Height', 'Value': str(max_h), 'IsRequired': False}] if max_h else []
    # Without a range condition Jellyfin remuxes HDR10/HLG/Dolby Vision as-is and an SDR screen shows it washed out.
    # Declaring SDR-only makes it transcode instead, tonemapping on the server's GPU (tonemap_cuda / VPP / OpenCL).
    ranges = conditions + [{'Condition': 'EqualsAny', 'Property': 'VideoRangeType', 'IsRequired': False,
                            'Value': HDR_RANGES if caps.get('hdr') else SDR_RANGES}]
    return {
        'Name': CLIENT,
        'MaxStreamingBitrate': caps['max_bitrate'],
        'MaxStaticBitrate': caps['max_bitrate'],
        'MusicStreamingTranscodingBitrate': 384000,
        'DirectPlayProfiles': [
            {'Container': 'mp4,m4v', 'Type': 'Video', 'VideoCodec': ','.join(video), 'AudioCodec': audio},
            {'Container': 'webm', 'Type': 'Video', 'VideoCodec': ','.join(c for c in ('vp8', 'vp9', 'av1') if c == 'vp8' or caps.get(c)),
             'AudioCodec': 'opus,vorbis'},
        ],
        'TranscodingProfiles': [
            # fMP4 segments: browsers' Media Source Extensions take them natively (MPEG-TS needs transmuxing,
            # which many engines lack). HEVC/AV1 only when this device decodes them; H.264 is always last.
            {'Container': 'mp4', 'Type': 'Video', 'Protocol': 'hls', 'Context': 'Streaming',
             'VideoCodec': ','.join([c for c in ('hevc', 'av1') if caps.get(c)] + ['h264']), 'AudioCodec': 'aac',
             'MaxAudioChannels': '6' if caps.get('surround') else '2', 'BreakOnNonKeyFrames': True, 'MinSegments': 1},
        ],
        'ContainerProfiles': [],
        # Browsers decode 8-bit H.264 and HEVC Main/Main 10 only; anything else (4:4:4, 10-bit H.264) is transcoded.
        'CodecProfiles': [
            {'Type': 'Video', 'Codec': 'h264', 'Conditions': conditions + [
                {'Condition': 'EqualsAny', 'Property': 'VideoRangeType', 'Value': SDR_RANGES, 'IsRequired': False},
                {'Condition': 'EqualsAny', 'Property': 'VideoProfile', 'Value': 'high|main|baseline|constrained baseline', 'IsRequired': False},
                {'Condition': 'LessThanEqual', 'Property': 'VideoBitDepth', 'Value': '8', 'IsRequired': False}]},
            {'Type': 'Video', 'Codec': 'hevc', 'Conditions': ranges + [
                {'Condition': 'EqualsAny', 'Property': 'VideoProfile', 'Value': 'main|main 10', 'IsRequired': False}]},
            {'Type': 'Video', 'Codec': 'av1', 'Conditions': ranges},
            {'Type': 'Video', 'Codec': 'vp9', 'Conditions': ranges},
        ],
        'SubtitleProfiles': [
            {'Format': 'vtt', 'Method': 'External'},
            {'Format': 'srt', 'Method': 'External'},
            {'Format': 'subrip', 'Method': 'External'},
            {'Format': 'ass', 'Method': 'External'},
            {'Format': 'ssa', 'Method': 'External'},
            {'Format': 'pgssub', 'Method': 'Encode'},
            {'Format': 'dvdsub', 'Method': 'Encode'},
            {'Format': 'dvbsub', 'Method': 'Encode'},
        ],
    }
