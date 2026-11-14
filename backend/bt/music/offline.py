"""Which music videos the household wants stored offline, for the downloader process.

Music is kept like Shorts: always cached locally, whatever the auto-download setting, so a party
never waits on YouTube. The wanted set is computed from the database alone (no network), in
priority order, so the downloader can queue, order and protect it from eviction.
"""
from __future__ import annotations

import threading
import time

from sqlalchemy import func
from sqlmodel import Session, col, select

from ..models import Download, Setting, Video, now
from .models import MusicFavorite, MusicMix, MusicMixTrack, MusicPlay, MusicTrack

MAX_WANTED = 800          # ~50 GB at 1080p: favourites first, so the cap trims the least loved
RECENT_PLAYS_S = 30 * 86400
MIXES = 25                # newest mixes per profile that are kept offline
FOR_YOU_KEY = 'music_for_you:'
DJ_KEY = 'music_dj:'      # the songs the live DJ lined up lately, per profile
DJ_KEEP = 64
_dj_lock = threading.Lock()
HAND_DELETED = 'deleted by hand'  # Download.error marker set by DELETE /api/downloads


def wanted_ids(s: Session, limit: int = MAX_WANTED) -> list[str]:
    """Every profile's music to keep offline, most important first:
    favourite tracks, what the live DJ lined up, For-you shelves, mixes (newest first, in play order), songs played to the end."""
    out: dict[str, None] = {}

    def add(ids):
        for v in ids:
            if len(out) >= limit:
                return
            out.setdefault(v, None)

    add(s.exec(select(MusicFavorite.ref).where(MusicFavorite.kind == 'track')
               .order_by(MusicFavorite.created_at.desc())).all())
    for row in s.exec(select(Setting).where(col(Setting.key).startswith(DJ_KEY))).all():
        add((row.value or {}).get('ids') or [])
    # Background presets stay prepared even after newer hand-made mixes displace them.
    add(s.exec(select(MusicMixTrack.video_id).join(MusicMix, MusicMix.id == MusicMixTrack.mix_id)
               .where(MusicMix.kind == 'mood').order_by(MusicMixTrack.position, MusicMix.id)).all())
    for row in s.exec(select(Setting).where(col(Setting.key).startswith(FOR_YOU_KEY))).all():
        add((row.value or {}).get('ids') or [])
    kept = _kept_mixes()
    add(s.exec(select(MusicMixTrack.video_id).join(kept, kept.c.id == MusicMixTrack.mix_id)
               .order_by(kept.c.created_at.desc(), MusicMixTrack.position)).all())
    add(s.exec(select(MusicPlay.video_id).where(MusicPlay.skipped == False,  # noqa: E712
                                                MusicPlay.played_at > time.time() - RECENT_PLAYS_S)
               .order_by(MusicPlay.played_at.desc())).all())
    return list(out)


def _kept_mixes():
    """The newest MIXES mixes of each profile, as one subquery (no per-mix round trips)."""
    ranked = select(MusicMix.id, MusicMix.created_at, func.row_number().over(
        partition_by=MusicMix.profile_id, order_by=MusicMix.created_at.desc()).label('rank')).subquery()
    return select(ranked.c.id, ranked.c.created_at).where(ranked.c.rank <= MIXES).subquery('kept_mixes')


def is_wanted(s: Session, video_id: str) -> bool:
    """Cheap single-video check for the downloader's frequent abort test. Close to wanted_ids
    (it ignores the overall cap), which is the safe side: it only ever keeps a copy."""
    if s.exec(select(MusicFavorite.ref).where(MusicFavorite.kind == 'track', MusicFavorite.ref == video_id)).first():
        return True
    if s.exec(select(MusicMixTrack.video_id).join(MusicMix, MusicMix.id == MusicMixTrack.mix_id)
              .where(MusicMix.kind == 'mood', MusicMixTrack.video_id == video_id)).first():
        return True
    kept = _kept_mixes()
    if s.exec(select(MusicMixTrack.video_id).join(kept, kept.c.id == MusicMixTrack.mix_id)
              .where(MusicMixTrack.video_id == video_id)).first():
        return True
    if s.exec(select(MusicPlay.id).where(MusicPlay.video_id == video_id, MusicPlay.skipped == False,  # noqa: E712
                                         MusicPlay.played_at > time.time() - RECENT_PLAYS_S)).first():
        return True
    lists = select(Setting).where(col(Setting.key).startswith(FOR_YOU_KEY) | col(Setting.key).startswith(DJ_KEY))
    return any(video_id in ((row.value or {}).get('ids') or []) for row in s.exec(lists).all())


def queue(s: Session) -> int:
    """Queue wanted music that has no local copy. Unlike playlist videos, an evicted copy is queued
    again while the song is still wanted (a favourite, in a mix, suggested): all music lives offline.
    A copy deleted by hand in Storage stays deleted. Returns how many were queued."""
    ids = wanted_ids(s)
    if not ids:
        return 0
    rows = {d.video_id: d for d in s.exec(select(Download).where(col(Download.video_id).in_(ids))).all()}
    again = 0
    for d in rows.values():
        if d.status == 'evicted' and d.error != HAND_DELETED:
            d.status, d.attempts, d.error, d.progress, d.created_at = 'queued', 0, '', 0.0, now()
            s.add(d)
            again += 1
    new = [v for v in ids if v not in rows]
    if not new:
        if again:
            s.commit()
        return again
    tracks = {t.video_id: t for t in s.exec(select(MusicTrack).where(col(MusicTrack.video_id).in_(new))).all()}
    known = set(s.exec(select(Video.id).where(col(Video.id).in_(new))).all())
    for v in new:
        t = tracks.get(v)
        if v not in known and t:  # the Storage tab and size estimates read titles and lengths from Video
            s.add(Video(id=v, title=f'{t.artist} – {t.title}' if t.artist else t.title, channel_id=t.artist_id,
                        channel_name=t.artist, duration_s=t.duration_s, thumbnail=t.thumbnail,
                        view_count=t.views))
        elif v not in known:
            s.add(Video(id=v))
        s.add(Download(video_id=v, status='queued'))
    s.commit()
    return len(new) + again


def save_for_you(s: Session, pid: int, ids: list[str]) -> None:
    from ..db import save_setting
    save_setting(f'{FOR_YOU_KEY}{pid}', {'ids': ids[:64], 'at': time.time()}, s)


def save_dj(s: Session, pid: int, ids: list[str]) -> None:
    """Remember what the DJ lined up (newest first), so the downloader stores it like a mix."""
    from ..db import save_setting
    with _dj_lock:  # a top-up and a guest request can land together
        s.expire_all()
        row = s.get(Setting, f'{DJ_KEY}{pid}')
        old = (row.value or {}).get('ids') or [] if row else []
        save_setting(f'{DJ_KEY}{pid}', {'ids': list(dict.fromkeys([*ids, *old]))[:DJ_KEEP], 'at': time.time()}, s)


def forget(s: Session, pid: int, video_id: str) -> None:
    """A disliked song leaves the profile's DJ and For-you lists, so it is no longer kept offline for them."""
    from ..db import save_setting
    with _dj_lock:
        for key in (f'{DJ_KEY}{pid}', f'{FOR_YOU_KEY}{pid}'):
            row = s.get(Setting, key)
            ids = (row.value or {}).get('ids') or [] if row else []
            if video_id in ids:
                save_setting(key, {**row.value, 'ids': [v for v in ids if v != video_id]}, s)


def offline_status(s: Session, ids: list[str]) -> dict[str, str]:
    """video id -> download status (done | queued | downloading | error | evicted) for those that have one."""
    if not ids:
        return {}
    return dict(s.exec(select(Download.video_id, Download.status).where(col(Download.video_id).in_(ids))).all())


def saved_ids(s: Session, ids: list[str]) -> set[str]:
    """Readable, nonempty local files; a stale done row must never promise offline playback."""
    from ..downloader.service import abs_path
    if not ids:
        return set()
    saved = set()
    for row in s.exec(select(Download).where(Download.video_id.in_(ids), Download.status == 'done')).all():
        try:
            path = abs_path(row.path)
            if path and path.is_file():
                with path.open('rb') as source:
                    if source.read(1):
                        saved.add(row.video_id)
        except OSError:
            continue
    return saved
