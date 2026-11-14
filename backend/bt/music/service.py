"""Music section logic: track store, favorites, mixes, suggestions and the DJ's next picks."""
from __future__ import annotations

import logging
import random
import re
import unicodedata
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import delete, func, or_, update
from sqlmodel import Session, select

from ..db import session
from . import dj, offline, yt
from .models import MusicFavorite, MusicMix, MusicMixTrack, MusicPlay, MusicTrack

log = logging.getLogger(__name__)

MIX_SIZE = 40
MAX_MIX_TRACKS = 300
MAX_MIXES = 60
CURATOR_STALE_S = 24 * 3600
BUILD_TIMEOUT_S = 10 * 60  # a build this old died with its thread or process
FOR_YOU_TTL = 2 * 3600
COLD_START = ('party anthems', 'dance hits', 'feel good pop')
TRACK_KEYS = ('video_id', 'title', 'artist', 'artist_id', 'views', 'duration_s', 'thumbnail')
_for_you: dict[int, tuple[float, dict]] = {}
_lock = threading.Lock()


def spawn(fn, *a) -> None:
    """Run slow YouTube/model work off the request. Tests replace this to run inline."""
    threading.Thread(target=fn, args=a, daemon=True).start()


def norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').lower()).strip()


# ---------- tracks

def store(s: Session, tracks: list[dict], trusted: bool = True) -> None:
    """Record tracks. Only YouTube responses are `trusted`: client-sent details may fill in a track
    nobody has seen yet but never overwrite one, since the table is shared by every profile.
    Thumbnails always come from the video id, never from the client."""
    for t in tracks:
        row = s.get(MusicTrack, t['video_id'])
        if row and not trusted:
            continue
        if not row:
            row = MusicTrack(video_id=t['video_id'])
        t = {**t, 'thumbnail': f'https://i.ytimg.com/vi/{t["video_id"]}/hqdefault.jpg'}
        for k in TRACK_KEYS[1:]:
            v = t.get(k)
            if v not in (None, '', 0):
                setattr(row, k, v)
        s.add(row)


def track_dict(row: MusicTrack) -> dict:
    return {k: getattr(row, k) for k in TRACK_KEYS}


def tracks_by_id(s: Session, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    return {r.video_id: track_dict(r) for r in s.exec(select(MusicTrack).where(MusicTrack.video_id.in_(ids))).all()}


def fav_sets(s: Session, pid: int) -> tuple[set[str], set[str]]:
    rows = s.exec(select(MusicFavorite).where(MusicFavorite.profile_id == pid)).all()
    return {r.ref for r in rows if r.kind == 'track'}, {r.ref for r in rows if r.kind == 'artist'}


def taste(s: Session, pid: int) -> tuple[set[str], set[str]]:
    """What the profile taught us to avoid: disliked video ids, and artists it wants less of."""
    rows = s.exec(select(MusicFavorite).where(MusicFavorite.profile_id == pid,
                                              MusicFavorite.kind.in_(['dislike', 'less']))).all()
    return {r.ref for r in rows if r.kind == 'dislike'}, {r.ref for r in rows if r.kind == 'less'}


def _folded(text: str) -> str:
    """norm() with accents folded, for matching what a guest typed ("beyonce" → Beyoncé)."""
    return norm(''.join(c for c in unicodedata.normalize('NFKD', text or '') if not unicodedata.combining(c)))


def is_less(artist: str, less: set[str]) -> bool:
    """True when any of the credited artists is one the profile wants less of ("Dua Lipa, DaBaby")."""
    a = f' {norm(artist)} '
    return any(k and f' {k} ' in a for k in less)


def prune(tracks: list[dict], disliked: set[str], less: set[str], drop_less: bool = False) -> list[dict]:
    """Drop disliked songs; push (or drop) artists the profile wants less of to the back."""
    keep = [t for t in tracks if t['video_id'] not in disliked]
    if drop_less:
        return [t for t in keep if not is_less(t.get('artist', ''), less)]
    return sorted(keep, key=lambda t: is_less(t.get('artist', ''), less))


def decorate(s: Session, pid: int, tracks: list[dict]) -> list[dict]:
    """Add the profile's favourite flags. Also records the tracks, so later ids resolve."""
    store(s, tracks)
    s.commit()
    ft, fa = fav_sets(s, pid)
    disliked, less = taste(s, pid)
    saved = offline.offline_status(s, [t['video_id'] for t in tracks])
    return [{**t, 'fav': t['video_id'] in ft, 'fav_artist': norm(t.get('artist', '')) in fa,
             'disliked': t['video_id'] in disliked, 'less_artist': is_less(t.get('artist', ''), less),
             'offline': saved.get(t['video_id']) == 'done'} for t in tracks]


def search(s: Session, pid: int, q: str) -> list[dict]:
    return decorate(s, pid, yt.search(q))


def artist_tracks(s: Session, pid: int, name: str) -> list[dict]:
    want = norm(name)
    hits = [t for t in yt.search(name) if want and want in norm(t['artist'])]
    return decorate(s, pid, hits)


# ---------- favorites

OPPOSITE = {'track': 'dislike', 'dislike': 'track', 'artist': 'less', 'less': 'artist'}


def set_favorite(s: Session, pid: int, kind: str, on: bool, track: dict | None = None, artist: str = '',
                 artist_id: str = '') -> None:
    """Favourite or teach-away a song (`track` / `dislike`) or an artist (`artist` / `less`)."""
    if kind not in OPPOSITE:
        raise ValueError('unknown kind')
    if kind in ('track', 'dislike'):
        if not track or not yt.VID_RE.match(track.get('video_id') or ''):
            raise ValueError('track needs a video id')
        store(s, [track], trusted=False)
        known = s.get(MusicTrack, track['video_id'])
        ref, name, thumb = track['video_id'], known.title if known else '', known.thumbnail if known else ''
    else:
        ref, name, thumb = norm(artist), artist.strip()[:120], ''
        if not ref:
            raise ValueError('artist needs a name')
    row = s.get(MusicFavorite, (pid, kind, ref))
    if on and not row:
        s.add(MusicFavorite(profile_id=pid, kind=kind, ref=ref, name=name, artist_id=artist_id, thumbnail=thumb))
        if other := s.get(MusicFavorite, (pid, OPPOSITE[kind], ref)):  # loving undoes a dislike and back
            s.delete(other)
    elif not on and row:
        s.delete(row)
    s.commit()
    if on and kind == 'dislike':
        offline.forget(s, pid, ref)
    with _lock:
        _for_you.pop(pid, None)


def favorites(s: Session, pid: int) -> dict:
    rows = s.exec(select(MusicFavorite).where(MusicFavorite.profile_id == pid)
                  .order_by(MusicFavorite.created_at.desc())).all()
    known = tracks_by_id(s, [r.ref for r in rows if r.kind == 'track'])
    fa = {r.ref for r in rows if r.kind == 'artist'}
    tracks = [{**known[r.ref], 'fav': True, 'fav_artist': norm(known[r.ref]['artist']) in fa}
              for r in rows if r.kind == 'track' and r.ref in known]
    artists = [{'name': r.name, 'artist_id': r.artist_id, 'key': r.ref} for r in rows if r.kind == 'artist']
    return {'tracks': tracks, 'artists': artists, 'disliked': [r.ref for r in rows if r.kind == 'dislike'],
            'less': [{'name': r.name, 'artist_id': r.artist_id, 'key': r.ref} for r in rows if r.kind == 'less']}


# ---------- plays

def record_play(s: Session, pid: int, video_id: str, skipped: bool) -> None:
    if not yt.VID_RE.match(video_id or ''):
        raise ValueError('bad video id')
    s.add(MusicPlay(profile_id=pid, video_id=video_id, skipped=skipped))
    s.commit()


def recent(s: Session, pid: int, limit: int = 20) -> list[dict]:
    rows = s.exec(select(MusicPlay.video_id, func.max(MusicPlay.played_at).label('at'))
                  .where(MusicPlay.profile_id == pid, MusicPlay.skipped == False)  # noqa: E712
                  .group_by(MusicPlay.video_id).order_by(func.max(MusicPlay.played_at).desc()).limit(limit)).all()
    known = tracks_by_id(s, [r[0] for r in rows])
    return decorate(s, pid, [known[r[0]] for r in rows if r[0] in known])


# ---------- mixes

def mix_dict(s: Session, m: MusicMix, pid: int, with_tracks: bool = False) -> dict:
    links = s.exec(select(MusicMixTrack).where(MusicMixTrack.mix_id == m.id).order_by(MusicMixTrack.position)).all()
    persona = dj.CURATORS.get(m.curator) or {}
    out = {'id': m.id, 'name': m.name, 'kind': m.kind, 'emoji': m.emoji, 'description': m.description,
           'curator': m.curator, 'curator_name': persona.get('name', ''), 'color': persona.get('color', ''),
           'blurb': m.blurb, 'seed_video_id': m.seed_video_id, 'status': m.status, 'error': m.error,
           'count': len(links), 'created_at': m.created_at, 'refreshed_at': m.refreshed_at,
           'last_played_at': m.last_played_at,
           'covers': [f'https://i.ytimg.com/vi/{lk.video_id}/mqdefault.jpg' for lk in links[:4]]}
    if with_tracks:
        known = tracks_by_id(s, [lk.video_id for lk in links])
        reasons = {lk.video_id: lk.reason for lk in links}
        tracks = decorate(s, pid, [known[lk.video_id] for lk in links if lk.video_id in known])
        out['tracks'] = [{**t, 'reason': reasons.get(t['video_id'], '')} for t in tracks]
    return out


def owned_mix(s: Session, pid: int, mid: int) -> MusicMix | None:
    m = s.get(MusicMix, mid)
    return m if m and m.profile_id == pid else None


def list_mixes(s: Session, pid: int) -> list[dict]:
    rows = s.exec(select(MusicMix).where(MusicMix.profile_id == pid).order_by(MusicMix.created_at.desc())).all()
    now = time.time()
    # Curators keep their mixes fresh, like the Hub curators re-run on a schedule: one stale mix per
    # visit, so a big library never turns one page load into a burst of YouTube requests. A build
    # that has been running for too long died with its thread and gets another go.
    for m in sorted(rows, key=lambda m: m.refreshed_at or 0):
        stale = m.kind == 'curator' and m.status == 'ready' and now - (m.refreshed_at or 0) > CURATOR_STALE_S
        dead = m.kind != 'manual' and m.status == 'building' and now - (m.build_started or 0) > BUILD_TIMEOUT_S
        if (stale or dead) and claim_build(s, m):
            break
    return [mix_dict(s, m, pid) for m in rows]


def claim_build(s: Session, m: MusicMix) -> bool:
    """Start a build unless another request just did. The conditional update is the lock."""
    now = time.time()
    res = s.execute(update(MusicMix).where(MusicMix.id == m.id, MusicMix.status == m.status,
                                        MusicMix.build_started == m.build_started)
                 .values(status='building', error='', build_started=now))
    s.commit()
    if res.rowcount != 1:
        s.refresh(m)
        return False
    s.refresh(m)
    spawn(build_mix, m.id)
    return True


def create_mood(s: Session, pid: int, preset: str, description: str = '') -> MusicMix:
    if preset == 'custom':
        if not description.strip() or len(description) > 300:
            raise ValueError('Describe your background mood in 1–300 characters.')
        name, intent = 'Custom background', description.strip()
    elif preset in dj.MOODS:
        name, intent = dj.MOODS[preset]['name'], dj.MOODS[preset]['description']
    else:
        raise ValueError('Choose a background mood.')
    return create_mix(s, pid, kind='mood', description=dj.BACKGROUND_INTENT + intent,
                      curator=preset, name=name)


def create_mix(s: Session, pid: int, *, kind: str, seed: dict | None = None, description: str = '',
               curator: str = '', name: str = '', tracks: list[dict] | None = None) -> MusicMix:
    count = s.exec(select(func.count()).select_from(MusicMix).where(MusicMix.profile_id == pid)).one()
    if count >= MAX_MIXES:
        raise ValueError(f'You have {MAX_MIXES} mixes already. Delete one first.')
    if kind == 'seed':
        if not seed or not yt.VID_RE.match(seed.get('video_id') or ''):
            raise ValueError('pick a track to start the mix from')
        store(s, [seed], trusted=False)
        m = MusicMix(profile_id=pid, kind='seed', seed_video_id=seed['video_id'], emoji='📻', status='building',
                     name=(name or f'{seed.get("title") or "Track"} radio')[:80],
                     blurb=f'Built around {seed.get("artist") or "this track"}.')
    elif kind == 'mood':
        if curator not in (*dj.MOODS, 'custom') or not description.strip():
            raise ValueError('Choose a background mood.')
        m = MusicMix(profile_id=pid, kind='mood', curator=curator, description=description[:500],
                     emoji=dj.MOODS.get(curator, {}).get('emoji', '🎐'),
                     status='building', name=(name or 'Background mix')[:80])
    elif kind == 'curator':
        if not description.strip():
            raise ValueError('describe the mix you want')
        cur = curator if curator in dj.CURATORS else dj.DEFAULT_CURATOR
        m = MusicMix(profile_id=pid, kind='curator', curator=cur, description=description.strip()[:500],
                     emoji=dj.CURATORS[cur]['emoji'], status='building', name=(name or 'Curating…')[:80])
    else:
        m = MusicMix(profile_id=pid, kind='manual', emoji='💿', name=(name.strip() or 'My mix')[:80],
                     refreshed_at=time.time())
    if m.status == 'building':
        m.build_started = time.time()
    s.add(m)
    s.commit()
    s.refresh(m)
    if m.status == 'building':
        spawn(build_mix, m.id)
    elif tracks:  # "Save as mix": what was playing becomes a mix of your own
        _fill(s, m, tracks)
    return m


def _fill(s: Session, m: MusicMix, tracks: list[dict]) -> None:
    seen: set[str] = set()
    tracks = [t for t in tracks if yt.VID_RE.match(t.get('video_id') or '')
              and not (t['video_id'] in seen or seen.add(t['video_id']))][:MAX_MIX_TRACKS]
    store(s, tracks, trusted=False)
    for i, t in enumerate(tracks):
        s.add(MusicMixTrack(mix_id=m.id, video_id=t['video_id'], position=i, reason=(t.get('reason') or '')[:200]))
    s.commit()


def copy_mix(s: Session, pid: int, m: MusicMix, name: str = '') -> MusicMix:
    """Keep a curated or radio mix as it is now: a hand-made copy that refreshes never touch."""
    links = s.exec(select(MusicMixTrack).where(MusicMixTrack.mix_id == m.id).order_by(MusicMixTrack.position)).all()
    copy = create_mix(s, pid, kind='manual', name=(name or f'{m.name} (saved)')[:80])
    copy.emoji, copy.blurb, copy.description = m.emoji, m.blurb, m.description
    s.add(copy)
    for lk in links:
        s.add(MusicMixTrack(mix_id=copy.id, video_id=lk.video_id, position=lk.position, reason=lk.reason))
    s.commit()
    return copy


def mark_played(s: Session, m: MusicMix) -> None:
    m.last_played_at = time.time()
    s.add(m)
    s.commit()


def recent_mixes(s: Session, pid: int, limit: int = 8) -> list[dict]:
    """Mixes you played or made lately, newest activity first."""
    rows = s.exec(select(MusicMix).where(MusicMix.profile_id == pid)).all()
    rows = sorted(rows, key=lambda m: max(m.last_played_at or 0, m.created_at or 0), reverse=True)[:limit]
    return [mix_dict(s, m, pid) for m in rows]


def rename_mix(s: Session, m: MusicMix, name: str) -> None:
    if not name.strip():
        raise ValueError('a mix needs a name')
    m.name = name.strip()[:80]
    s.add(m)
    s.commit()


def _resolve(pick: dict) -> dict | None:
    """The official video for an `artist – title` the model named, or None."""
    try:
        hits = yt.search(f'{pick["artist"]} {pick["title"]}')
    except Exception:  # noqa: BLE001 - one unresolvable song never sinks the batch
        return None
    a, ttl = norm(pick['artist']), norm(pick['title'])
    for h in hits[:6]:
        ha, ht = norm(h['artist']), norm(h['title'])
        if (a in ha or ha in a) and (ttl[:12] in ht or ht[:12] in ttl):
            return {**h, 'reason': pick.get('why') or ''}
    for h in hits[:3]:
        if a and (a in norm(h['artist']) or norm(h['artist']) in a):
            return {**h, 'reason': pick.get('why') or ''}
    return None


def resolve_all(picks: list[dict]) -> list[dict]:
    if not picks:
        return []
    with ThreadPoolExecutor(4) as pool:
        return [t for t in pool.map(_resolve, picks) if t]


def _interleave(*lists: list[dict]) -> list[dict]:
    out, seen = [], set()
    for row in zip(*[iter_pad(lst, max(map(len, lists), default=0)) for lst in lists]):
        for t in row:
            if t and t['video_id'] not in seen:
                seen.add(t['video_id'])
                out.append(t)
    return out


def iter_pad(lst: list, n: int) -> list:
    return list(lst) + [None] * (n - len(lst))


def build_mix(mid: int) -> None:
    """Fill (or refill) a mix. Runs off the request; status tells the page when it is done."""
    with session() as s:
        m = s.get(MusicMix, mid)
        if not m:
            return
        kind, seed, description, curator = m.kind, m.seed_video_id, m.description, m.curator
        disliked, less = taste(s, m.profile_id)
    try:
        if kind == 'seed':
            tracks = prune(yt.radio(seed, origin='worker'), disliked, less)[:MIX_SIZE]
            extra = {}
        else:
            plan = (dj.curate(description, curator, background=True) if kind == 'mood'
                    else dj.curate(description, curator))
            named = resolve_all(plan['picks'])
            found = []
            for q in plan['queries'][:6]:
                try:
                    find = yt.background_search if kind == 'mood' else yt.search
                    found.append(find(q, origin='worker')[:12])
                except Exception as e:  # noqa: BLE001
                    log.info('music curator query %r failed: %s', q, e)
            tracks = prune(_interleave(named, *found), disliked, less)[:MIX_SIZE]
            extra = {'name': plan['name'], 'emoji': plan['emoji'], 'blurb': plan['blurb'], 'queries': plan['queries']}
        if not tracks:
            raise RuntimeError('No background music matched. Try a different mood or description.' if kind == 'mood'
                               else 'No official music videos matched. Try different words.')
    except Exception as e:  # noqa: BLE001
        with session() as s:
            if m := s.get(MusicMix, mid):
                m.status, m.error = 'error', str(e)[:300]
                s.add(m)
                s.commit()
        return
    with session() as s:
        m = s.get(MusicMix, mid)
        if not m:
            return
        store(s, tracks)
        s.exec(delete(MusicMixTrack).where(MusicMixTrack.mix_id == mid))
        for i, t in enumerate(tracks):
            s.add(MusicMixTrack(mix_id=mid, video_id=t['video_id'], position=i, reason=t.get('reason') or ''))
        for k, v in extra.items():
            setattr(m, k, v)
        m.status, m.error, m.refreshed_at = 'ready', '', time.time()
        s.add(m)
        s.commit()


def refresh_mix(s: Session, m: MusicMix) -> None:
    if m.kind == 'manual':
        raise ValueError('a hand-made mix has nothing to refresh')
    if m.status == 'building' and time.time() - (m.build_started or 0) < BUILD_TIMEOUT_S:
        return  # already on it
    claim_build(s, m)


def add_to_mix(s: Session, m: MusicMix, track: dict) -> None:
    if not yt.VID_RE.match(track.get('video_id') or ''):
        raise ValueError('bad track')
    store(s, [track], trusted=False)
    if not s.get(MusicMixTrack, (m.id, track['video_id'])):
        count = s.exec(select(func.count()).select_from(MusicMixTrack).where(MusicMixTrack.mix_id == m.id)).one()
        if count >= MAX_MIX_TRACKS:
            raise ValueError(f'A mix holds at most {MAX_MIX_TRACKS} songs.')
        last = s.exec(select(func.max(MusicMixTrack.position)).where(MusicMixTrack.mix_id == m.id)).one()
        s.add(MusicMixTrack(mix_id=m.id, video_id=track['video_id'], position=(last if last is not None else -1) + 1))
    s.commit()


def remove_from_mix(s: Session, m: MusicMix, video_id: str) -> None:
    s.exec(delete(MusicMixTrack).where(MusicMixTrack.mix_id == m.id, MusicMixTrack.video_id == video_id))
    s.commit()


def delete_mix(s: Session, m: MusicMix) -> None:
    s.exec(delete(MusicMixTrack).where(MusicMixTrack.mix_id == m.id))
    s.delete(m)
    s.commit()


# ---------- suggestions

def _safe(fn, *a) -> list[dict]:
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001 - one failed lookup leaves the rest of the shelf
        log.info('music lookup failed: %s', e)
        return []


def for_you(s: Session, pid: int, refresh: bool = False) -> dict:
    """Two shelves: songs you already love, and new ones you probably will."""
    now = time.time()
    with _lock:
        hit = _for_you.get(pid)
    if hit and not refresh and now - hit[0] < FOR_YOU_TTL:
        cached = hit[1]
        return {'loved': decorate(s, pid, cached['loved']), 'fresh': decorate(s, pid, cached['fresh']),
                'generated_at': hit[0]}
    favs = favorites(s, pid)
    played = recent(s, pid, 40)
    loved_artists = {a['key'] for a in favs['artists']}
    by_artist = []
    if loved_artists:
        names = [a['name'].lower() for a in favs['artists'] if a['name']][:30]
        rows = s.exec(select(MusicTrack).where(or_(*[func.lower(MusicTrack.artist).contains(n) for n in names]))
                      .limit(300)).all() if names else []
        by_artist = [track_dict(r) for r in rows if norm(r.artist) in loved_artists
                     or any(norm(n) in norm(r.artist) for n in names)]
    disliked, less = taste(s, pid)
    loved = {t['video_id']: t for t in prune([*favs['tracks'], *played, *by_artist], disliked, less, drop_less=True)}
    loved_list = random.sample(list(loved.values()), min(16, len(loved)))

    seeds = [t['video_id'] for t in random.sample(list(loved.values()), min(3, len(loved)))]
    artists = random.sample([a['name'] for a in favs['artists']], min(2, len(favs['artists'])))
    with ThreadPoolExecutor(4) as pool:
        lists = [r[1:] for r in pool.map(lambda v: _safe(yt.radio, v), seeds)]  # radio opens with its seed
        lists += list(pool.map(lambda a: _safe(yt.search, a), artists))
        if not lists or not any(lists):
            lists = list(pool.map(lambda q: _safe(yt.search, q), COLD_START))
    fresh = prune([t for t in _interleave(*lists) if t['video_id'] not in loved], disliked, less)[:16]
    for t in fresh:
        t['reason'] = 'New for you'
    with _lock:
        _for_you[pid] = (now, {'loved': loved_list, 'fresh': fresh})
    offline.save_for_you(s, pid, [t['video_id'] for t in [*fresh, *loved_list]])  # the downloader stores these
    return {'loved': decorate(s, pid, loved_list), 'fresh': decorate(s, pid, fresh), 'generated_at': now}


# ---------- the DJ

def dj_next(s: Session, pid: int, played: list[str], queue: list[str], vibe: str = '', request: str = '',
            n: int = 3) -> dict:
    """The DJ's next `n` tracks plus what it says on the mic. Model first, YouTube Music radio as backup."""
    n = max(1, min(int(n), 8))
    disliked, less = taste(s, pid)
    exclude = set(played) | {v for v in queue if yt.VID_RE.match(v)}  # a skipped dislike is not played again
    played = [v for v in played if yt.VID_RE.match(v) and v not in disliked][-30:]  # nor does the DJ flow from it
    known = tracks_by_id(s, played)
    history = [known[v] for v in played if v in known]
    favs = favorites(s, pid)
    loved = [f'{t["artist"]} – {t["title"]}' for t in favs['tracks'][:10]] + [a['name'] for a in favs['artists'][:8]]
    exclude |= disliked
    asked = f' {_folded(request)} '
    less = {a['key'] for a in favs['less'] if f' {_folded(a["name"])} ' not in asked}  # a guest request still plays
    avoid = [a['name'] for a in favs['less'][:10] if a['key'] in less]
    newest = s.exec(select(MusicFavorite.ref).where(MusicFavorite.profile_id == pid, MusicFavorite.kind == 'dislike')
                    .order_by(MusicFavorite.created_at.desc()).limit(15)).all()
    known_bad = tracks_by_id(s, list(newest))
    avoid += [f'{known_bad[v]["artist"]} – {known_bad[v]["title"]}' for v in newest if v in known_bad]

    out: list[dict] = []
    line, source = '', 'radio'
    plan = dj.pick_next(history, loved, vibe, request, n, avoid)
    if plan:
        source, line = 'ai', plan['line']
        out = prune([t for t in resolve_all(plan['picks']) if t['video_id'] not in exclude], disliked, less, True)
        uniq: dict[str, dict] = {}
        for t in out:
            uniq.setdefault(t['video_id'], t)
        out = list(uniq.values())[:n]
    if len(out) < n:
        out += _radio_picks(s, pid, history, favs, exclude | {t['video_id'] for t in out}, vibe, request, n - len(out),
                            less)
    if not out:
        return {'line': "The crates are empty right now — try a request!", 'tracks': [], 'source': source}
    if not line:
        line = dj.fallback_line(out[0], request)
    tracks = decorate(s, pid, out)  # also stores the tracks, so the lyrics lookup knows their names
    ids = [t['video_id'] for t in out]
    offline.save_dj(s, pid, ids)  # the downloader stores DJ picks like a mix: replays come from disk
    spawn(prewarm, pid, ids)
    return {'line': line, 'tracks': tracks, 'source': source}


SYNC_TRIED_KEY = 'music_prewarm_synced'
SYNC_RETRY_S = 30 * 86400
SYNC_TRIED_KEEP = 3000


def _first_sync_try(video_id: str) -> bool:
    """True once per song per SYNC_RETRY_S: the background auto-sync is never retried song after song.
    Kept in the database, so a failed fit is not tried again on every pick or after a restart."""
    from ..db import save_setting
    from ..models import Setting
    with _lock, session() as s:
        row = s.get(Setting, SYNC_TRIED_KEY)
        tried = dict((row.value or {}) if row else {})
        now = time.time()
        if now - tried.get(video_id, 0) < SYNC_RETRY_S:
            return False
        tried[video_id] = now
        if len(tried) > SYNC_TRIED_KEEP:
            tried = dict(sorted(tried.items(), key=lambda kv: kv[1])[-SYNC_TRIED_KEEP:])
        save_setting(SYNC_TRIED_KEY, tried, s)
        return True


def prewarm(pid: int, ids: list[str]) -> None:
    """Get the DJ's picks ready before their turn: the stream, the lyrics, and their timing.

    Every pick gets its lyrics looked up. The next song also gets the app's ear when speech-to-text is
    on: timed lyrics are auto-synced to the video, a song without them is transcribed for karaoke."""
    if not ids:
        return
    yt.prepare(ids[0])
    from . import lyrics, sync
    from .. import subtitle_jobs
    rows = {}
    for v in ids:
        try:
            with session() as s:
                row = lyrics.get(s, v)
                rows[v] = (row.status, row.synced, row.offset_source)
        except Exception as e:  # noqa: BLE001 - lyrics still load when the song plays
            log.info('music lyrics prewarm %s failed: %s', v, e)
    first = rows.get(ids[0])
    try:
        if not first or not subtitle_jobs.enabled():
            return
        status, synced, offset_source = first
        if status == 'found' and synced:
            # a manual nudge or an earlier auto-sync already fixed the timing; a failed try is not repeated
            if not offset_source and _first_sync_try(ids[0]):
                sync.start(ids[0])
        elif status == 'missing' and subtitle_jobs.status(ids[0])['status'] not in ('ready', 'queued', 'running', 'failed'):
            subtitle_jobs.enqueue(ids[0], pid)
    except Exception as e:  # noqa: BLE001
        log.info('music subtitle prewarm %s failed: %s', ids[0], e)


def _radio_picks(s: Session, pid: int, history: list[dict], favs: dict, exclude: set[str], vibe: str, request: str,
                 n: int, less: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    pool: list[dict] = []
    if request:
        pool = [{**t, 'reason': 'Guest request'} for t in _safe(yt.search, request)[:4]]
    elif vibe and not history:
        pool = _safe(yt.search, vibe)
    if len(pool) < n:
        seed = history[-1]['video_id'] if history else ''
        if not seed and favs['tracks']:
            seed = random.choice(favs['tracks'])['video_id']
        pool += _safe(yt.radio, seed)[1:] if seed else _safe(yt.search, vibe or random.choice(COLD_START))
    last_artist = norm(history[-1]['artist']) if history else ''
    picks: list[dict] = []
    candidates = [t for t in pool if t['video_id'] not in exclude and not is_less(t.get('artist', ''), less)]
    keep = 1 if request else 0  # a guest request plays its best match first
    head, rest = candidates[:keep], candidates[keep:keep + 20]
    random.shuffle(rest)
    rest.sort(key=lambda t: norm(t['artist']) == last_artist)  # never the same artist back to back
    for t in head + rest:
        if len(picks) >= n:
            break
        if t['video_id'] not in {p['video_id'] for p in picks}:
            picks.append(t)
    return picks


def delete_profile(s: Session, pid: int) -> None:
    mids = list(s.exec(select(MusicMix.id).where(MusicMix.profile_id == pid)).all())  # lyrics are shared: kept
    s.exec(delete(MusicMixTrack).where(MusicMixTrack.mix_id.in_(mids or [0])))
    for model in (MusicMix, MusicFavorite, MusicPlay):
        s.exec(delete(model).where(model.profile_id == pid))
    from ..models import Setting
    s.exec(delete(Setting).where(Setting.key.in_([f'{offline.FOR_YOU_KEY}{pid}', f'{offline.DJ_KEY}{pid}'])))


def offline_summary(s: Session) -> dict:
    """How much of the household's music is stored offline."""
    ids = offline.wanted_ids(s)
    st = offline.offline_status(s, ids)
    done = sum(1 for v in ids if st.get(v) == 'done')
    pending = sum(1 for v in ids if st.get(v) in ('queued', 'downloading', 'error'))
    return {'wanted': len(ids), 'saved': done, 'pending': pending}
