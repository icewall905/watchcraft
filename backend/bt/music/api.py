"""Music Videos API. Sync handlers: FastAPI runs them in the threadpool, so YouTube lookups never
block the event loop. Hub-gated like the other discovery sections (open YouTube search)."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import ytdl
from ..access import require_hub
from ..db import session
from ..hub import ai
from .. import subtitle_jobs
from . import dj, lyrics, service, yt

router = APIRouter(prefix='/music', tags=['music'], dependencies=[Depends(require_hub)])


class TrackIn(BaseModel):
    video_id: str = Field(pattern=r'^[A-Za-z0-9_-]{11}$')
    title: str = Field('', max_length=300)
    artist: str = Field('', max_length=200)
    artist_id: str = Field('', max_length=40)
    views: int = Field(0, ge=0, le=10**13)
    duration_s: int = Field(0, ge=0, le=86400)
    thumbnail: str = Field('', max_length=500)  # ignored: thumbnails are built from the video id


class FavoriteIn(BaseModel):
    kind: str = Field(pattern='^(track|artist)$')
    on: bool = True
    track: TrackIn | None = None
    artist: str = Field('', max_length=200)
    artist_id: str = Field('', max_length=40)


class MixIn(BaseModel):
    kind: str = Field(pattern='^(seed|curator|manual)$')
    seed: TrackIn | None = None
    description: str = Field('', max_length=500)
    curator: str = Field('', max_length=20)
    name: str = Field('', max_length=80)
    tracks: list[TrackIn] = Field(default_factory=list, max_length=300)


class MixPatch(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CopyIn(BaseModel):
    name: str = Field('', max_length=80)


class OffsetIn(BaseModel):
    offset_ms: int = Field(ge=-lyrics.MAX_OFFSET_MS, le=lyrics.MAX_OFFSET_MS)


class PlayIn(BaseModel):
    video_id: str = Field(pattern=r'^[A-Za-z0-9_-]{11}$')
    skipped: bool = False


class DjIn(BaseModel):
    played: list[str] = Field(default_factory=list, max_length=200)
    queue: list[str] = Field(default_factory=list, max_length=200)
    vibe: str = Field('', max_length=120)
    request: str = Field('', max_length=200)
    n: int = Field(3, ge=1, le=8)


@contextmanager
def _yt_errors():
    try:
        yield
    except ytdl.YtBlocked as e:
        raise HTTPException(503, f'YouTube blocked the request: {str(e)[:200]}') from e
    except ytdl.YtError as e:
        raise HTTPException(502, f'YouTube error: {str(e)[:200]}') from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _mix(s, pid: int, mid: int):
    m = service.owned_mix(s, pid, mid)
    if not m:
        raise HTTPException(404, 'mix not found')
    return m


@router.get('/home')
def home(pid: int = Depends(require_hub)):
    with session() as s:
        return {'favorites': service.favorites(s, pid), 'mixes': service.list_mixes(s, pid),
                'recent': service.recent(s, pid, 16), 'recent_mixes': service.recent_mixes(s, pid), 'ai': ai.enabled(), 'offline': service.offline_summary(s),
                'curators': [{'key': k, **v} for k, v in dj.CURATORS.items()]}


@router.get('/search')
def search(q: str, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        return {'tracks': service.search(s, pid, q[:200])}


@router.get('/artist')
def artist(name: str, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        favs = service.fav_sets(s, pid)[1]
        return {'name': name, 'fav': service.norm(name) in favs, 'tracks': service.artist_tracks(s, pid, name[:200])}


@router.get('/radio/{video_id}')
def radio(video_id: str, pid: int = Depends(require_hub)):
    if not yt.VID_RE.match(video_id):
        raise HTTPException(404, 'bad video id')
    with _yt_errors(), session() as s:
        return {'tracks': service.decorate(s, pid, yt.radio(video_id))}


@router.get('/for-you')
def for_you(refresh: bool = False, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        return service.for_you(s, pid, refresh)


@router.get('/favorites')
def favorites(pid: int = Depends(require_hub)):
    with session() as s:
        return service.favorites(s, pid)


@router.post('/favorites')
def set_favorite(body: FavoriteIn, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        service.set_favorite(s, pid, body.kind, body.on, body.track.model_dump() if body.track else None,
                             body.artist, body.artist_id)
        return service.favorites(s, pid)


@router.post('/plays')
def play(body: PlayIn, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        service.record_play(s, pid, body.video_id, body.skipped)
    return {'ok': True}


@router.get('/mixes')
def mixes(pid: int = Depends(require_hub)):
    with session() as s:
        return {'mixes': service.list_mixes(s, pid)}


@router.post('/mixes')
def create_mix(body: MixIn, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        m = service.create_mix(s, pid, kind=body.kind, seed=body.seed.model_dump() if body.seed else None,
                               description=body.description, curator=body.curator, name=body.name,
                               tracks=[t.model_dump() for t in body.tracks])
        return service.mix_dict(s, m, pid, with_tracks=True)


@router.get('/mixes/{mid}')
def get_mix(mid: int, pid: int = Depends(require_hub)):
    with session() as s:
        return service.mix_dict(s, _mix(s, pid, mid), pid, with_tracks=True)


@router.patch('/mixes/{mid}')
def rename_mix(mid: int, body: MixPatch, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        m = _mix(s, pid, mid)
        service.rename_mix(s, m, body.name)
        return service.mix_dict(s, m, pid)


@router.post('/mixes/{mid}/played')
def mix_played(mid: int, pid: int = Depends(require_hub)):
    with session() as s:
        service.mark_played(s, _mix(s, pid, mid))
    return {'ok': True}


@router.post('/mixes/{mid}/copy')
def copy_mix(mid: int, body: CopyIn | None = None, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        copy = service.copy_mix(s, pid, _mix(s, pid, mid), body.name if body else '')
        return service.mix_dict(s, copy, pid, with_tracks=True)


@router.post('/mixes/{mid}/refresh')
def refresh_mix(mid: int, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        m = _mix(s, pid, mid)
        service.refresh_mix(s, m)
        return service.mix_dict(s, m, pid)


@router.post('/mixes/{mid}/tracks')
def add_track(mid: int, body: TrackIn, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        m = _mix(s, pid, mid)
        service.add_to_mix(s, m, body.model_dump())
        return service.mix_dict(s, m, pid)


@router.delete('/mixes/{mid}/tracks/{video_id}')
def remove_track(mid: int, video_id: str, pid: int = Depends(require_hub)):
    with session() as s:
        m = _mix(s, pid, mid)
        service.remove_from_mix(s, m, video_id)
        return service.mix_dict(s, m, pid, with_tracks=True)


@router.delete('/mixes/{mid}')
def delete_mix(mid: int, pid: int = Depends(require_hub)):
    with session() as s:
        service.delete_mix(s, _mix(s, pid, mid))
    return {'ok': True}


@router.post('/dj')
def dj_next(body: DjIn, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        return service.dj_next(s, pid, body.played, body.queue, body.vibe.strip(), body.request.strip(), body.n)


# ---------- lyrics / karaoke

def _vid(video_id: str) -> str:
    if not yt.VID_RE.match(video_id):
        raise HTTPException(404, 'bad video id')
    return video_id


def _transcript(video_id: str) -> dict:
    """The app's speech-to-text track for this video, as timed lines (empty until generated)."""
    st = subtitle_jobs.status(video_id)
    out = {'status': st['status'], 'progress': st['progress'], 'enabled': st['enabled'], 'lines': []}
    if st.get('track'):
        from ..db import session as db_session
        from ..models import GeneratedSubtitles
        with db_session() as s:
            row = s.get(GeneratedSubtitles, video_id)
            out['lines'] = [{'t': c['t'], 'end': c['end'], 'text': c['text']} for c in lyrics.parse_vtt(row.vtt if row else '')]
    return out


@router.get('/lyrics/{video_id}')
def get_lyrics(video_id: str, refresh: bool = False, pid: int = Depends(require_hub)):
    with session() as s:
        out = lyrics.as_dict(lyrics.get(s, _vid(video_id), refresh))
    if out['status'] != 'found' or not out['synced']:
        out['transcript'] = _transcript(video_id)  # karaoke text for songs without timed lyrics
    return out


@router.post('/lyrics/{video_id}/offset')
def lyrics_offset(video_id: str, body: OffsetIn, pid: int = Depends(require_hub)):
    with _yt_errors(), session() as s:
        return lyrics.as_dict(lyrics.set_offset(s, _vid(video_id), body.offset_ms))


@router.post('/lyrics/{video_id}/listen')
def lyrics_listen(video_id: str, pid: int = Depends(require_hub)):
    """Auto-sync: transcribe the song (speech-to-text) and line the lyrics up with what is sung.
    Call again to poll; once the transcript is ready the offset is saved, or the transcript itself
    becomes the karaoke text when there are no lyrics."""
    _vid(video_id)
    st = subtitle_jobs.status(video_id)
    if not st['enabled']:
        raise HTTPException(409, 'Speech-to-text is turned off in Settings, so songs cannot be transcribed.')
    if st['status'] not in ('ready', 'queued', 'running'):
        subtitle_jobs.enqueue(video_id, pid)
    tr = _transcript(video_id)
    out = {'transcript': tr, 'aligned': False, 'lyrics': None}
    if tr['lines']:
        with session() as s:
            row = lyrics.get(s, video_id)
            if row.status == 'found' and row.synced:
                delta = lyrics.align_offset(row.lines, tr['lines'])
                if delta is not None:
                    row = lyrics.set_offset(s, video_id, delta, 'auto')
                    out['aligned'] = True
            out['lyrics'] = lyrics.as_dict(row)
    return out
