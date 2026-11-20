"""Local copies of everything a curated post shows, so the wall works without internet access.

When a post is picked, a scheduler sweep saves each of its pictures (and video posters) full size plus a small
WebP preview, and fetches its comments once. The media route serves from disk; the network is only a fallback.
Files are keyed by the picture URL, so a fuller picture found later gets its own file. Network I/O never runs
inside a DB transaction. Unreferenced files are removed by ``gc`` from the hourly prune."""
import hashlib
import logging
import time
from pathlib import Path

from sqlmodel import select

from .. import curation
from ..config import DATA_DIR
from ..db import session
from .adapters import ADAPTERS
from .models import CRComments, CRPick, CRPost

log = logging.getLogger(__name__)
MEDIA_DIR = DATA_DIR / 'commonroom-media'
MAX_PER_POST = 8
IMAGES_PER_TICK = 16
COMMENTS_PER_TICK = 4
MAX_BYTES = 8_000_000
TYPES = ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif')


def mime_of(data: bytes) -> str:
    from ..hub.api import _image_mime
    return _image_mime(data)


def _key(url: str) -> str:
    try:
        url = curation.canonical(url)
    except ValueError:
        pass
    return hashlib.sha256(url.encode()).hexdigest()


def path(url: str, kind: str = 'full') -> Path:
    key = _key(url)
    return MEDIA_DIR / key[:2] / f'{key}.{kind}'


def local(url: str, thumb: bool = False) -> bytes | None:
    """Stored bytes for a picture: the preview when asked and one exists, else the full picture."""
    for kind in (('thumb', 'full') if thumb else ('full',)):
        p = path(url, kind)
        if p.is_file():
            return p.read_bytes()
    return None


def save(url: str, data: bytes | None = None) -> bool:
    """Fetch (unless given) and store one picture with its preview. False when it is not a usable image."""
    if data is None:
        data = curation.fetch(url, kinds=TYPES, limit=MAX_BYTES)
    if not mime_of(data):
        return False
    from ..hub.moodboards import make_thumb
    full = path(url)
    full.parent.mkdir(parents=True, exist_ok=True)
    tmp = full.with_suffix('.tmp')
    tmp.write_bytes(data)
    tmp.replace(full)
    small = make_thumb(data)
    if small:
        path(url, 'thumb').write_bytes(small[0])
    return True


def _urls(media: list) -> list[str]:
    out = []
    for m in (media or [])[:MAX_PER_POST]:
        u = m.get('poster') if m.get('kind') == 'video' else m.get('url')
        if u and u.startswith('https://'):
            out.append(u)
    return out


def _picked_posts(limit: int = 300) -> list[tuple[int, list, dict]]:
    with session() as s:
        return list(s.exec(select(CRPost.id, CRPost.media, CRPost.meta).where(CRPost.id.in_(select(CRPick.post_id)))
                           .order_by(CRPost.id.desc()).limit(limit)).all())


def sweep_images(limit: int = IMAGES_PER_TICK) -> int:
    saved, tried = 0, 0
    for pid, media, meta in _picked_posts():
        failed = set((meta or {}).get('media_failed') or [])
        for url in _urls(media):
            if tried >= limit:
                return saved
            if url in failed or path(url).is_file():
                continue
            tried += 1
            try:
                ok = save(url)
            except Exception as e:  # noqa: BLE001 — a picture we cannot get stays a link to the original
                log.debug('Common Room picture %s failed: %s', url, e)
                ok = False
            if ok:
                saved += 1
                continue
            with session() as s:
                s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                post = s.get(CRPost, pid)
                if post:
                    meta2 = dict(post.meta or {})
                    meta2['media_failed'] = [*(meta2.get('media_failed') or []), url][-MAX_PER_POST:]
                    post.meta = meta2
                    s.add(post)
                s.commit()
    return saved


def sweep_comments(limit: int = COMMENTS_PER_TICK) -> int:
    """Fetch comments once for picked posts that have none stored, so opening a post works offline."""
    with session() as s:
        have = select(CRComments.post_id)
        rows = s.exec(select(CRPost).where(CRPost.id.in_(select(CRPick.post_id)), CRPost.id.not_in(have))
                      .order_by(CRPost.id.desc()).limit(limit * 3)).all()
        todo = [{'id': p.id, 'url': p.url, 'external_id': p.external_id, 'community': p.community,
                 'meta': p.meta or {}, 'platform': p.platform} for p in rows]
    done = 0
    for data in todo:
        adapter = ADAPTERS.get(data['platform'])
        if done >= limit:
            break
        tree = []
        if adapter and adapter.comments:
            done += 1
            try:
                tree = adapter.comments(data) or []
            except Exception as e:  # noqa: BLE001 — retried on open; an empty row stops endless retries here
                log.debug('Common Room comments for %s failed: %s', data['url'], e)
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            if not s.get(CRComments, data['id']):
                s.add(CRComments(post_id=data['id'], tree=tree, fetched_at=time.time() if tree else 0.0))
            s.commit()
    return done


def sweep() -> dict:
    return {'images': sweep_images(), 'comments': sweep_comments()}


def gc(min_age_s: int = 3600) -> int:
    """Remove stored pictures no remaining post refers to."""
    if not MEDIA_DIR.is_dir():
        return 0
    with session() as s:
        keep = {_key(u) for media in s.exec(select(CRPost.media)).all() for u in _urls(media)}
    now, removed = time.time(), 0
    for f in MEDIA_DIR.glob('*/*'):
        if f.stem not in keep and now - f.stat().st_mtime > min_age_s:
            f.unlink(missing_ok=True)
            removed += 1
    return removed
