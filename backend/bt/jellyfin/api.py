"""Jellyfin API: server setup, per-profile sign-in, browsing, playback and a signed media proxy.

Browse handlers are sync (FastAPI runs them in the threadpool); the media proxy is async and streams.
Images and streams are addressed by signed paths (profile, item, play session), so <img>, <video>
and the HLS player work without custom headers and the Jellyfin token never leaves the server.
"""
from __future__ import annotations

import re
import secrets
import urllib.parse as up

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel, Field
from sqlmodel import select

from ..access import grown_up_access, profile_id, require_admin
from ..config import SECRET
from ..db import session
from ..models import Profile
from . import cast
from . import client as jf
from .models import JellyfinLink

router = APIRouter(prefix='/jellyfin', tags=['jellyfin'])
_sig = URLSafeSerializer(SECRET, salt='jellyfin-media')

ID = r'^[0-9a-fA-F-]{32,36}$'
LIST_FIELDS = 'Overview,Genres,PrimaryImageAspectRatio,ChildCount,ProductionYear,OfficialRating,PremiereDate'
DETAIL_FIELDS = LIST_FIELDS + ',People,MediaStreams,MediaSources,Taglines'
ITEM_TYPES = {'Movie', 'Series', 'Season', 'Episode', 'BoxSet', 'Video', 'MusicVideo', 'Folder', 'CollectionFolder'}
SORTS = {'SortName', 'DateCreated', 'PremiereDate', 'CommunityRating', 'Random', 'DatePlayed', 'Runtime'}
_KEY_PARAMS = {'api_key', 'apikey'}


# ---------------------------------------------------------------- access

def _allowed(pid: int) -> bool:
    with session() as s:
        p = s.get(Profile, pid)
        if not p:
            return False
        return grown_up_access(s, p) or (p.role == 'child' and bool(jf.config().get('allow_children')))


def require_jellyfin(request: Request, pid: int = Depends(profile_id)) -> int:
    # A screen playing a title sent to it acts as the sending profile (see cast.py).
    pid = cast.grant_pid(request.headers.get(cast.HEADER)) or pid
    if not _allowed(pid):
        raise HTTPException(403, 'Jellyfin is unavailable for this profile.')
    return pid


def _link(pid: int) -> JellyfinLink:
    with session() as s:
        link = s.get(JellyfinLink, pid)
    if not link or not link.token:
        raise HTTPException(409, 'Link this profile to your Jellyfin account in Settings → Jellyfin.')
    return link


def _call(link: JellyfinLink, method: str, path: str, **kw):
    return jf.call(method, path, device_id=link.device_id, token=link.token, **kw)


def _get(link: JellyfinLink, path: str, **params):
    return _call(link, 'GET', path, params={'userId': link.user_id, **params})


# ---------------------------------------------------------------- signed URLs

def _image_url(pid: int):
    def build(item_id: str, kind: str, tag: str) -> str:
        return f'/api/jellyfin/img/{_sig.dumps([pid, item_id, kind, tag])}'
    return build


def _media_token(pid: int, item_id: str, source_id: str, play_session: str) -> str:
    return _sig.dumps({'p': pid, 'i': item_id, 'm': source_id, 's': play_session})


def _read_media_token(token: str) -> dict:
    try:
        data = _sig.loads(token)
    except BadSignature as e:
        raise HTTPException(403, 'bad media link') from e
    if not isinstance(data, dict) or not {'p', 'i', 'm', 's'} <= data.keys():
        raise HTTPException(403, 'bad media link')
    return data


def _strip_keys(url: str) -> str:
    """Drop any API key Jellyfin echoes into URLs; the proxy adds the token as a header."""
    p = up.urlsplit(url)
    q = [(k, v) for k, v in up.parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _KEY_PARAMS]
    return up.urlunsplit(('', '', p.path, up.urlencode(q), ''))


def _drop_param(url: str, name: str) -> str:
    p = up.urlsplit(url)
    q = [(k, v) for k, v in up.parse_qsl(p.query, keep_blank_values=True) if k.lower() != name]
    return up.urlunsplit((p.scheme, p.netloc, p.path, up.urlencode(q), ''))


def _media_url(token: str, jellyfin_url: str) -> str:
    """Absolute or root-relative Jellyfin URL → proxied path."""
    path = _strip_keys(jellyfin_url)
    base_path = up.urlsplit(jf.config().get('url', '')).path.rstrip('/')
    if base_path and path.startswith(base_path + '/'):
        path = path[len(base_path):]
    return f'/api/jellyfin/media/{token}{path if path.startswith("/") else "/" + path}'


# ---------------------------------------------------------------- setup

class ConfigIn(BaseModel):
    url: str = Field('', max_length=300)
    allow_children: bool = False


@router.get('/config', dependencies=[Depends(require_admin)])
def get_config():
    cfg = jf.config()
    return {'url': cfg['url'], 'allow_children': bool(cfg.get('allow_children'))}


@router.put('/config', dependencies=[Depends(require_admin)])
def put_config(body: ConfigIn):
    if not body.url.strip():
        jf.save_config({'url': '', 'allow_children': body.allow_children})
        return {'url': '', 'allow_children': body.allow_children}
    url = jf.normalize_url(body.url)
    info = jf.call('GET', '/System/Info/Public', device_id='watchcraft-setup', url=url)
    if not isinstance(info, dict) or not info.get('Id'):
        raise HTTPException(502, 'That address did not answer like a Jellyfin server.')
    major, minor = (int(x) if x.isdigit() else 0 for x in (str(info.get('Version', '0.0')).split('.') + ['0'])[:2])
    if (major, minor) < (10, 9):
        raise HTTPException(422, f'Jellyfin {info.get("Version")} is too old. The Hub needs Jellyfin 10.9 or newer.')
    jf.save_config({'url': url, 'allow_children': body.allow_children, 'server_id': info['Id'],
                    'server_name': info.get('ServerName', ''), 'version': info.get('Version', '')})
    return {'url': url, 'allow_children': body.allow_children, 'server_name': info.get('ServerName', ''),
            'version': info.get('Version', '')}


@router.get('/status')
def status(pid: int = Depends(profile_id)):
    cfg = jf.config()
    with session() as s:
        link = s.get(JellyfinLink, pid)
    return {'configured': bool(cfg['url']), 'allowed': _allowed(pid), 'linked': bool(link and link.token),
            'user_name': link.user_name if link else '', 'is_admin': bool(link and link.is_admin),
            'server_name': cfg.get('server_name', ''), 'version': cfg.get('version', '')}


class LinkIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field('', max_length=300)


@router.post('/link')
def link_account(body: LinkIn, pid: int = Depends(require_jellyfin)):
    device_id = f'watchcraft-{pid}-{secrets.token_hex(6)}'
    r = jf.call('POST', '/Users/AuthenticateByName', device_id=device_id,
                json={'Username': body.username, 'Pw': body.password})
    if not isinstance(r, dict) or not r.get('AccessToken'):
        raise HTTPException(502, 'Jellyfin did not return a sign-in token.')
    user = r.get('User') or {}
    with session() as s:
        old = s.get(JellyfinLink, pid)
        if old:
            s.delete(old)
            s.flush()
        s.add(JellyfinLink(profile_id=pid, user_id=user.get('Id', ''), user_name=user.get('Name', body.username),
                           token=r['AccessToken'], device_id=device_id, server_id=r.get('ServerId', ''),
                           is_admin=bool((user.get('Policy') or {}).get('IsAdministrator'))))
        s.commit()
    return status(pid)


@router.delete('/link')
def unlink(pid: int = Depends(profile_id)):
    with session() as s:
        link = s.get(JellyfinLink, pid)
        if not link:
            return status(pid)
        try:
            _call(link, 'POST', '/Sessions/Logout')
        except HTTPException:
            pass  # the server may be gone; forget the token anyway
        s.delete(link)
        s.commit()
    return status(pid)


@router.get('/encoding', dependencies=[Depends(require_admin)])
def encoding():
    """The server's transcoding setup, read through any linked Jellyfin administrator."""
    with session() as s:
        admin = s.exec(select(JellyfinLink).where(JellyfinLink.is_admin == True)).first()  # noqa: E712
    if not admin:
        return {'known': False, 'reason': 'Link a profile to a Jellyfin administrator account to see transcoding settings.'}
    cfg = _call(admin, 'GET', '/System/Configuration/encoding') or {}
    hw = cfg.get('HardwareAccelerationType') or 'none'
    return {
        'known': True,
        'hardware': hw if hw != 'none' else None,
        'decoding_codecs': cfg.get('HardwareDecodingCodecs') or [],
        'hevc_encoding': bool(cfg.get('AllowHevcEncoding')),
        'av1_encoding': bool(cfg.get('AllowAv1Encoding')),
        'tonemapping': bool(cfg.get('EnableTonemapping')),
        'low_power': bool(cfg.get('EnableIntelLowPowerH264HwEncoder') or cfg.get('EnableIntelLowPowerHevcHwEncoder')),
    }


# ---------------------------------------------------------------- browse

def _items(pid: int, raws) -> list[dict]:
    build = _image_url(pid)
    return [jf.item(r, build) for r in (raws or []) if isinstance(r, dict)]


@router.get('/views')
def views(pid: int = Depends(require_jellyfin)):
    link = _link(pid)
    return _items(pid, (_get(link, '/UserViews') or {}).get('Items'))


@router.get('/home')
def home(pid: int = Depends(require_jellyfin)):
    link = _link(pid)
    resume = (_get(link, '/UserItems/Resume', limit=20, mediaTypes='Video', fields=LIST_FIELDS,
                   enableImageTypes='Primary,Backdrop,Thumb') or {}).get('Items')
    nextup = (_get(link, '/Shows/NextUp', limit=20, fields=LIST_FIELDS, enableResumable=False) or {}).get('Items')
    vs = (_get(link, '/UserViews') or {}).get('Items') or []
    latest = []
    for v in vs:
        if v.get('CollectionType') not in ('movies', 'tvshows', 'homevideos', 'musicvideos', 'boxsets', None):
            continue
        rows = _get(link, '/Items/Latest', parentId=v['Id'], limit=16, fields=LIST_FIELDS) or []
        if rows:
            latest.append({'view': jf.item(v, _image_url(pid)), 'items': _items(pid, rows)})
    return {'resume': _items(pid, resume), 'next_up': _items(pid, nextup), 'views': _items(pid, vs), 'latest': latest}


@router.get('/items')
def items(parent_id: str | None = None, types: str = '', sort: str = 'SortName', descending: bool = False,
          start: int = 0, limit: int = 60, q: str = '', genre: str = '', unplayed: bool = False,
          favorite: bool = False, max_age: int | None = None, pid: int = Depends(require_jellyfin)):
    if parent_id and not re.match(ID, parent_id):
        raise HTTPException(422, 'bad parent id')
    kinds = ','.join(t for t in types.split(',') if t in ITEM_TYPES) or None
    filters = [f for f, on in (('IsUnplayed', unplayed), ('IsFavorite', favorite)) if on]
    link = _link(pid)
    ratings = None
    if max_age is not None:
        # Jellyfin's own maxOfficialRating depends on the server's rating tables; matching the
        # library's actual rating strings against our age table is predictable and excludes unrated.
        ok = [r for r in _filters(link, parent_id, kinds).get('OfficialRatings') or []
              if (a := jf.rating_age(r)) is not None and a <= max_age]
        if not ok:
            return {'items': [], 'total': 0}
        ratings = '|'.join(ok)
    r = _get(link, '/Items', parentId=parent_id, includeItemTypes=kinds,
             recursive=True if (kinds or q) else None, sortBy=sort if sort in SORTS else 'SortName',
             sortOrder='Descending' if descending else 'Ascending', startIndex=max(0, start),
             limit=min(max(1, limit), 200), searchTerm=q.strip()[:100] or None, genres=genre[:80] or None,
             officialRatings=ratings, filters=','.join(filters) or None, fields=LIST_FIELDS,
             enableTotalRecordCount=True) or {}
    return {'items': _items(pid, r.get('Items')), 'total': r.get('TotalRecordCount', 0)}


def _filters(link: JellyfinLink, parent_id: str | None, kinds: str | None) -> dict:
    return _get(link, '/Items/Filters', parentId=parent_id, includeItemTypes=kinds) or {}


AGE_STEPS = (3, 7, 10, 13, 16)


@router.get('/filters')
def filters(parent_id: str | None = None, types: str = '', pid: int = Depends(require_jellyfin)):
    """Genres and age steps that actually occur in this library, for the filter bar."""
    if parent_id and not re.match(ID, parent_id):
        raise HTTPException(422, 'bad parent id')
    kinds = ','.join(t for t in types.split(',') if t in ITEM_TYPES) or None
    f = _filters(_link(pid), parent_id, kinds)
    ages = sorted({a for r in f.get('OfficialRatings') or [] if (a := jf.rating_age(r)) is not None})
    # Offer an age step only if it shows something and differs from the step below it.
    steps, seen = [], -1
    for s in AGE_STEPS:
        n = sum(1 for a in ages if a <= s)
        if n and n != seen:
            steps.append(s)
            seen = n
    return {'genres': sorted(g for g in f.get('Genres') or [] if isinstance(g, str)), 'ages': steps}


@router.get('/search')
def search(q: str, pid: int = Depends(require_jellyfin)):
    if not q.strip():
        return {'items': []}
    link = _link(pid)
    r = _get(link, '/Items', searchTerm=q.strip()[:100], recursive=True, limit=40,
             includeItemTypes='Movie,Series,Episode,BoxSet,Video', fields=LIST_FIELDS) or {}
    return {'items': _items(pid, r.get('Items'))}


@router.get('/items/{item_id}')
def detail(item_id: str, pid: int = Depends(require_jellyfin)):
    if not re.match(ID, item_id):
        raise HTTPException(422, 'bad item id')
    link = _link(pid)
    raw = _get(link, f'/Items/{item_id}', fields=DETAIL_FIELDS)
    out = jf.item(raw, _image_url(pid))
    if raw.get('Type') == 'Series':
        out['seasons'] = _items(pid, (_get(link, f'/Shows/{item_id}/Seasons', fields=LIST_FIELDS) or {}).get('Items'))
        nu = (_get(link, '/Shows/NextUp', seriesId=item_id, limit=1, fields=LIST_FIELDS) or {}).get('Items')
        out['next_up'] = (_items(pid, nu) or [None])[0]
    elif raw.get('Type') == 'Season' and raw.get('SeriesId'):
        out['episodes'] = _items(pid, (_get(link, f'/Shows/{raw["SeriesId"]}/Episodes', seasonId=item_id,
                                            fields=LIST_FIELDS) or {}).get('Items'))
    return out


@router.get('/shows/{series_id}/episodes')
def episodes(series_id: str, season_id: str | None = None, pid: int = Depends(require_jellyfin)):
    if not re.match(ID, series_id) or (season_id and not re.match(ID, season_id)):
        raise HTTPException(422, 'bad id')
    link = _link(pid)
    r = _get(link, f'/Shows/{series_id}/Episodes', seasonId=season_id, fields=LIST_FIELDS) or {}
    return {'items': _items(pid, r.get('Items'))}


class FlagIn(BaseModel):
    on: bool


@router.post('/items/{item_id}/played')
def set_played(item_id: str, body: FlagIn, pid: int = Depends(require_jellyfin)):
    if not re.match(ID, item_id):
        raise HTTPException(422, 'bad item id')
    link = _link(pid)
    _call(link, 'POST' if body.on else 'DELETE', f'/UserPlayedItems/{item_id}', params={'userId': link.user_id})
    return {'ok': True}


@router.post('/items/{item_id}/favorite')
def set_favorite(item_id: str, body: FlagIn, pid: int = Depends(require_jellyfin)):
    if not re.match(ID, item_id):
        raise HTTPException(422, 'bad item id')
    link = _link(pid)
    _call(link, 'POST' if body.on else 'DELETE', f'/UserFavoriteItems/{item_id}', params={'userId': link.user_id})
    return {'ok': True}


# ---------------------------------------------------------------- playback

class Caps(BaseModel):
    hevc: bool = False
    av1: bool = False
    vp9: bool = False
    ac3: bool = False
    surround: bool = False
    hdr: bool = False
    max_height: int = Field(0, ge=0, le=4320)


class PlayIn(BaseModel):
    caps: Caps = Field(default_factory=Caps)
    max_bitrate: int = Field(120_000_000, ge=250_000, le=400_000_000)
    start_s: float | None = Field(None, ge=0, le=864000, allow_inf_nan=False)
    audio_index: int | None = Field(None, ge=0, le=1000)
    subtitle_index: int | None = Field(None, ge=-1, le=1000)
    media_source_id: str | None = Field(None, pattern=ID)
    force_transcode: bool = False


def _next_episode(link: JellyfinLink, pid: int, raw: dict):
    if raw.get('Type') != 'Episode' or not raw.get('SeriesId'):
        return None
    r = _get(link, f'/Shows/{raw["SeriesId"]}/Episodes', startItemId=raw['Id'], limit=2, fields=LIST_FIELDS) or {}
    rows = [x for x in (r.get('Items') or []) if x.get('Id') != raw['Id']]
    return (_items(pid, rows[:1]) or [None])[0]


@router.post('/items/{item_id}/play')
def play(item_id: str, body: PlayIn, pid: int = Depends(require_jellyfin)):
    if not re.match(ID, item_id):
        raise HTTPException(422, 'bad item id')
    link = _link(pid)
    raw = _get(link, f'/Items/{item_id}', fields=DETAIL_FIELDS)
    start_s = body.start_s if body.start_s is not None else jf._secs((raw.get('UserData') or {}).get('PlaybackPositionTicks'))
    sources = raw.get('MediaSources') or [{}]
    default_audio = next((s.get('DefaultAudioStreamIndex') for s in sources if s.get('Id') == body.media_source_id),
                         sources[0].get('DefaultAudioStreamIndex'))
    # Browsers cannot switch audio inside a direct-played file: another track means a remux.
    direct_ok = not body.force_transcode and (body.audio_index is None or body.audio_index == default_audio)
    caps = {**body.caps.model_dump(), 'max_bitrate': body.max_bitrate}
    info = _call(link, 'POST', f'/Items/{item_id}/PlaybackInfo', params={'userId': link.user_id}, json={
        'UserId': link.user_id, 'MaxStreamingBitrate': body.max_bitrate, 'StartTimeTicks': int(start_s * jf.TICKS),
        'AudioStreamIndex': body.audio_index, 'SubtitleStreamIndex': body.subtitle_index,
        'MediaSourceId': body.media_source_id, 'DeviceProfile': jf.device_profile(caps),
        'EnableDirectPlay': direct_ok, 'EnableDirectStream': True, 'EnableTranscoding': True,
        'AllowVideoStreamCopy': not body.force_transcode, 'AllowAudioStreamCopy': True, 'AutoOpenLiveStream': True,
    }) or {}
    if info.get('ErrorCode'):
        raise HTTPException(409, f'Jellyfin cannot play this here ({info["ErrorCode"]}).')
    srcs = info.get('MediaSources') or []
    src = next((s for s in srcs if s.get('Id') == body.media_source_id), srcs[0] if srcs else None)
    if not src:
        raise HTTPException(409, 'Jellyfin found no playable version of this item.')
    play_session = info.get('PlaySessionId') or secrets.token_hex(16)
    token = _media_token(pid, item_id, src['Id'], play_session)
    if direct_ok and src.get('SupportsDirectPlay'):
        method, kind = 'DirectPlay', 'file'
        url = _media_url(token, f'/Videos/{item_id}/stream?' + up.urlencode(
            {'static': 'true', 'mediaSourceId': src['Id'], 'playSessionId': play_session, 'deviceId': link.device_id}))
    elif src.get('TranscodingUrl'):
        method = 'DirectStream' if src.get('SupportsDirectStream') else 'Transcode'
        # The HLS playlist always spans the whole item; the player seeks to start_s itself.
        kind, url = 'hls', _media_url(token, _drop_param(src['TranscodingUrl'], 'starttimeticks'))
    else:
        raise HTTPException(409, 'Jellyfin offered no stream this device can play.')
    streams = src.get('MediaStreams') or []
    subs = []
    for s in jf.subtitle_tracks(streams):
        raw_s = next(x for x in streams if x.get('Index') == s['index'])
        if s['text']:  # browsers only render WebVTT; Jellyfin converts SRT/ASS on request
            s['url'] = _media_url(token, f'/Videos/{item_id}/{src["Id"]}/Subtitles/{s["index"]}/0/Stream.vtt')
        s['burned'] = raw_s.get('DeliveryMethod') == 'Encode'
        subs.append(s)
    reasons = up.parse_qs(up.urlsplit(src.get('TranscodingUrl') or '').query).get('TranscodeReasons', [''])[0]
    _call(link, 'POST', '/Sessions/Playing', json={
        'ItemId': item_id, 'MediaSourceId': src['Id'], 'PlaySessionId': play_session, 'CanSeek': True,
        'PositionTicks': int(start_s * jf.TICKS), 'PlayMethod': method, 'IsPaused': False,
        'AudioStreamIndex': body.audio_index if body.audio_index is not None else src.get('DefaultAudioStreamIndex'),
        'SubtitleStreamIndex': body.subtitle_index if body.subtitle_index is not None else src.get('DefaultSubtitleStreamIndex'),
    })
    return {
        'session': token, 'method': method, 'kind': kind, 'url': url,
        'start_s': start_s,
        'duration_s': jf._secs(src.get('RunTimeTicks') or raw.get('RunTimeTicks')),
        'audio': jf.audio_tracks(streams), 'subtitles': subs,
        'audio_index': body.audio_index if body.audio_index is not None else src.get('DefaultAudioStreamIndex'),
        'subtitle_index': body.subtitle_index if body.subtitle_index is not None else src.get('DefaultSubtitleStreamIndex', -1),
        'transcode_reasons': [r.strip() for r in reasons.split(',') if r.strip()],
        'item': jf.item(raw, _image_url(pid)), 'next': _next_episode(link, pid, raw),
    }


class ProgressIn(BaseModel):
    session: str = Field(min_length=10, max_length=600)
    position_s: float = Field(0, ge=0, le=864000, allow_inf_nan=False)
    paused: bool = False
    event: str = Field('progress', pattern='^(progress|stopped)$')


@router.post('/progress')
def progress(body: ProgressIn, pid: int = Depends(require_jellyfin)):
    m = _read_media_token(body.session)
    if m['p'] != pid:
        raise HTTPException(403, 'not your playback session')
    link = _link(pid)
    payload = {'ItemId': m['i'], 'MediaSourceId': m['m'], 'PlaySessionId': m['s'],
               'PositionTicks': int(body.position_s * jf.TICKS), 'IsPaused': body.paused, 'CanSeek': True}
    if body.event == 'stopped':
        _call(link, 'POST', '/Sessions/Playing/Stopped', json=payload)
        try:  # free the server's transcoder (and hardware encoder) right away
            _call(link, 'DELETE', '/Videos/ActiveEncodings', params={'deviceId': link.device_id, 'playSessionId': m['s']})
        except HTTPException:
            pass
    else:
        _call(link, 'POST', '/Sessions/Playing/Progress', json={**payload, 'EventName': 'timeupdate'})
    return {'ok': True}


@router.get('/playing')
def playing(session: str, pid: int = Depends(require_jellyfin)):
    """What the server is doing for this playback: direct, remux or transcode, and on which encoder."""
    m = _read_media_token(session)
    if m['p'] != pid:
        raise HTTPException(403, 'not your playback session')
    link = _link(pid)
    rows = _call(link, 'GET', '/Sessions', params={'deviceId': link.device_id}) or []
    row = next((r for r in rows if (r.get('PlayState') or {}).get('PlaySessionId') == m['s']), rows[0] if len(rows) == 1 else None)
    if not row:
        return {'method': None, 'transcode': None}
    t = row.get('TranscodingInfo')
    # Jellyfin's PlayState can lag behind a new stream on the same device; the live transcode is truth.
    method = ('DirectStream' if t.get('IsVideoDirect') and t.get('IsAudioDirect') else 'Transcode') if t \
        else (row.get('PlayState') or {}).get('PlayMethod')
    return {
        'method': method,
        'transcode': t and {
            'video_codec': t.get('VideoCodec'), 'audio_codec': t.get('AudioCodec'), 'container': t.get('Container'),
            'video_direct': bool(t.get('IsVideoDirect')), 'audio_direct': bool(t.get('IsAudioDirect')),
            'hardware': (t.get('HardwareAccelerationType') or None) if t.get('HardwareAccelerationType') != 'none' else None,
            'bitrate': t.get('Bitrate'), 'framerate': t.get('Framerate'), 'width': t.get('Width'),
            'height': t.get('Height'), 'progress': t.get('CompletionPercentage'),
            'reasons': t.get('TranscodeReasons') or [],
        },
    }


# ---------------------------------------------------------------- media proxy

_PASS = ('content-type', 'content-length', 'content-range', 'accept-ranges', 'last-modified', 'etag')
_RANGE_RE = re.compile(r'^bytes=\d*-\d*$')
_URI_ATTR = re.compile(r'URI="([^"]+)"')


def _rewrite_playlist(text: str, token: str) -> str:
    def fix(uri: str) -> str:
        if '://' in uri:
            base = jf.config().get('url', '')
            if not base or not uri.startswith(base + '/'):
                return uri  # leave foreign URLs alone; the player will fail rather than leak
            uri = uri[len(base):]
        if uri.startswith('/'):
            return _media_url(token, uri)
        return _strip_keys(uri)
    out = []
    for line in text.splitlines():
        if line and not line.startswith('#'):
            out.append(fix(line.strip()))
        else:
            out.append(_URI_ATTR.sub(lambda mt: f'URI="{fix(mt.group(1))}"', line))
    return '\n'.join(out) + '\n'


async def _upstream(link: JellyfinLink, path: str, query: str, range_header: str | None) -> httpx.Response:
    headers = {'Authorization': jf.auth_header(link.device_id, link.token), 'Accept-Encoding': 'identity'}
    if range_header and _RANGE_RE.match(range_header.strip()):
        headers['Range'] = range_header.strip()
    url = jf.base_url() + path + ('?' + query if query else '')
    c = jf.aclient()
    try:
        return await c.send(c.build_request('GET', url, headers=headers), stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f'Cannot reach Jellyfin ({type(e).__name__}).') from e


def _stream(resp: httpx.Response, cache: str) -> StreamingResponse:
    headers = {k: resp.headers[k] for k in _PASS if k in resp.headers}
    headers['cache-control'] = cache

    async def body():
        try:
            async for chunk in resp.aiter_raw(64 * 1024):
                yield chunk
        finally:
            await resp.aclose()
    return StreamingResponse(body(), status_code=resp.status_code, headers=headers)


def _link_or_404(pid: int) -> JellyfinLink:
    with session() as s:
        link = s.get(JellyfinLink, pid)
    if not link or not link.token:
        raise HTTPException(404, 'not linked')
    return link


@router.get('/media/{token}/{path:path}')
async def media(token: str, path: str, request: Request):
    m = _read_media_token(token)
    parts = path.split('/')
    # Jellyfin writes item ids with or without dashes depending on the endpoint.
    if len(parts) < 3 or parts[0].lower() != 'videos' or parts[1].replace('-', '').lower() != m['i'].replace('-', '').lower():
        raise HTTPException(403, 'outside this playback session')
    link = _link_or_404(m['p'])
    query = up.urlencode([(k, v) for k, v in up.parse_qsl(request.url.query, keep_blank_values=True)
                          if k.lower() not in _KEY_PARAMS])
    resp = await _upstream(link, '/' + path, query, request.headers.get('range'))
    if resp.status_code >= 400:
        await resp.aclose()
        return Response(status_code=502 if resp.status_code >= 500 else resp.status_code,
                        content=f'Jellyfin HTTP {resp.status_code}', media_type='text/plain')
    ctype = resp.headers.get('content-type', '')
    if path.lower().endswith('.m3u8') or 'mpegurl' in ctype.lower():
        try:
            text = (await resp.aread()).decode('utf-8', 'replace')
        finally:
            await resp.aclose()
        return Response(_rewrite_playlist(text, token), media_type='application/vnd.apple.mpegurl',
                        headers={'cache-control': 'no-store'})
    return _stream(resp, 'no-store')


@router.get('/img/{token}')
async def image(token: str, w: int = 400):
    try:
        pid, item_id, kind, tag = _sig.loads(token)
    except (BadSignature, ValueError, TypeError) as e:
        raise HTTPException(403, 'bad image link') from e
    if kind not in ('Primary', 'Backdrop', 'Thumb', 'Logo') or not re.match(ID, item_id):
        raise HTTPException(403, 'bad image link')
    link = _link_or_404(pid)
    q = up.urlencode({'tag': tag, 'fillWidth': min(max(64, w), 1920), 'quality': 90})
    resp = await _upstream(link, f'/Items/{item_id}/Images/{kind}', q, None)
    if resp.status_code >= 400:
        await resp.aclose()
        raise HTTPException(404, 'no image')
    # The tag is in the signed URL, so a changed image gets a new URL: cache hard.
    return _stream(resp, 'private, max-age=604800, immutable')
