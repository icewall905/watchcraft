"""Local copies of everything a curated post shows, so the wall works without internet access.

When a post is picked, a scheduler sweep saves each of its pictures (and video posters) full size plus a small
WebP preview, and fetches its comments once. The media route serves from disk; the network is only a fallback.
Files are keyed by the picture URL, so a fuller picture found later gets its own file. Network I/O never runs
inside a DB transaction. Unreferenced files are removed by ``gc`` from the hourly prune."""
import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import func, or_
from sqlmodel import select

from .. import curation
from ..config import DATA_DIR
from ..db import session
from . import reddit_oauth, social
from .adapters import ADAPTERS
from .models import CRComments, CRPick, CRPost, CRThread

log = logging.getLogger(__name__)
MEDIA_DIR = DATA_DIR / 'commonroom-media'
MAX_PER_POST = 8
IMAGES_PER_TICK = 16
COMMENTS_PER_TICK = 8
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


# Comments are kept current for every picked post on a platform that has them: young posts often, older ones
# now and then, and only when the post's comment count says something changed (or nothing is stored yet).
REFETCH_YOUNG_S = 3600        # posts under two days old: at most hourly
REFETCH_S = 6 * 3600          # older posts up to a week: at most every six hours
YOUNG_S = 2 * 86400
REFETCH_WINDOW_S = 7 * 86400


def comment_source(data: dict):
    """The function that reads this post's comments, or None. A Reddit mirror's post is read on Reddit itself,
    which only works with Reddit API keys (Reddit blocks anonymous reads from the server)."""
    adapter = ADAPTERS.get(data.get('platform') or '')
    if adapter and adapter.comments:
        return adapter.comments
    ref = social.reddit_ref(data.get('url') or '')
    reddit = ADAPTERS.get('reddit')
    if ref and reddit and reddit.comments and reddit_oauth.configured():
        return lambda _data: reddit.comments({'external_id': ref[1]})
    return None


def discussion_url(data: dict) -> str:
    """Where people talk about this post, when that is somewhere else than the post itself."""
    ref = social.reddit_ref(data.get('url') or '')
    return f'https://www.reddit.com/r/{ref[0]}/comments/{ref[1]}/' if ref else ''


def _due(s, limit: int) -> list[CRPost]:
    """Picked posts whose comments were never fetched, then those whose stored tree is empty or behind."""
    now = time.time()
    picked = select(CRPick.post_id)
    talk = [k for k, a in ADAPTERS.items() if a.comments]
    mirrors = [CRPost.url.like(f'https://{h}/r/%') for h in social.REDDIT_MIRRORS] if reddit_oauth.configured() else []
    rows = list(s.exec(select(CRPost).where(CRPost.id.in_(picked), CRPost.id.not_in(select(CRComments.post_id)))
                       .order_by(CRPost.id.desc()).limit(limit)).all())
    if len(rows) < limit:
        last = func.max(CRComments.checked_at, CRComments.fetched_at)
        stale = s.exec(select(CRPost).join(CRComments, CRComments.post_id == CRPost.id).where(
            CRPost.id.in_(picked), or_(CRPost.platform.in_(talk) & (CRPost.comments > 0), *mirrors),
            CRPost.published_at >= now - REFETCH_WINDOW_S,
            ((CRPost.published_at >= now - YOUNG_S) & (last < now - REFETCH_YOUNG_S)) | (last < now - REFETCH_S),
            (func.coalesce(func.json_array_length(CRComments.tree), 0) == 0) | (CRPost.comments > CRComments.count))
            .order_by(CRPost.published_at.desc()).limit(limit - len(rows))).all()
        rows += list(stale)
    return rows


def sweep_comments(limit: int = COMMENTS_PER_TICK) -> int:
    """Fetch comments for picked posts so opening a post works offline and cards can show top replies.
    Posts whose first fetch came back empty, or that gained comments since, are fetched again (with backoff)."""
    with session() as s:
        rows = _due(s, limit * 3)
        # Child threads (forced on for child profiles) also get a kid-checked copy, so cards can show a top reply.
        kid = set(s.exec(select(CRPick.post_id).join(CRThread, CRThread.id == CRPick.thread_id).where(
            CRPick.post_id.in_([p.id for p in rows] or [0]), CRThread.child_friendly == True)).all())  # noqa: E712
        todo = [{'id': p.id, 'url': p.url, 'external_id': p.external_id, 'community': p.community,
                 'meta': p.meta or {}, 'platform': p.platform, 'comments': p.comments} for p in rows]
    done = 0
    for data in todo:
        read = comment_source(data)
        if done >= limit:
            break
        tree = []
        if read:
            done += 1
            try:
                tree = read(data) or []
            except Exception as e:  # noqa: BLE001 — retried later with backoff, and on open
                log.debug('Common Room comments for %s failed: %s', data['url'], e)
        kid_tree = None
        if tree and data['id'] in kid:
            from .kids import filter_comments
            kid_tree = filter_comments(tree)
        now = time.time()
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = s.get(CRComments, data['id']) or CRComments(post_id=data['id'])
            row.checked_at = now
            if tree:  # a failed or empty refetch never wipes a tree we already have
                row.tree, row.kid, row.fetched_at, row.count = tree, kid_tree, now, data['comments']
            s.add(row)
            s.commit()
    return done


# ---------- videos: a local mp4 of each picked post's video, so it plays in the Commons without the source page

VIDEO_MAX_BYTES = 60_000_000
VIDEO_MAX_S = 300
VIDEO_MAX_SIDE = 720          # the smaller side: 720p, portrait or landscape
VIDEOS_PER_TICK = 1
HLS_HOSTS = ('v.redd.it', 'video.bsky.app')   # streaming playlists ffmpeg may read (segments stay on that host)
FILE_EXT = re.compile(r'\.(?:mp4|webm|mov|m4v)$', re.I)


def video_saveable(url: str) -> bool:
    p = urlsplit(url or '')
    host = (p.hostname or '').lower()
    return p.scheme == 'https' and ((host in HLS_HOSTS and p.path.endswith('.m3u8')) or bool(FILE_EXT.search(p.path)))


def video_path(url: str) -> Path:
    return path(url, 'mp4')


def _probe(src: str, *extra: str) -> dict:
    r = subprocess.run(['ffprobe', '-v', 'error', *extra, '-show_entries',
                        'format=duration:stream=index,codec_type,width,height,bit_rate', '-of', 'json', src],
                       capture_output=True, timeout=60, check=True)
    return json.loads(r.stdout or b'{}')


def save_video(url: str) -> bool:
    """Fetch one post video into a faststart mp4 (best version up to 720p, with sound). False when it is too
    long, too large or not a video. HLS playlists only from known video hosts; files through the safe fetch."""
    if not video_saveable(url):
        return False
    host = (urlsplit(url).hostname or '').lower()
    out = video_path(url)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wc-crvideo-') as tmp:
        if host in HLS_HOSTS and urlsplit(url).path.endswith('.m3u8'):
            src, net = url, ['-protocol_whitelist', 'https,tls,tcp,crypto,hls,http']
        else:
            data = curation.fetch(url, kinds=('video/mp4', 'video/webm', 'video/quicktime', 'application/octet-stream'),
                                  limit=VIDEO_MAX_BYTES)
            src, net = f'{tmp}/in', ['-protocol_whitelist', 'file']
            Path(src).write_bytes(data)
        info = _probe(src, *net)
        duration = float((info.get('format') or {}).get('duration') or 0)
        if not 0 < duration <= VIDEO_MAX_S:
            return False
        streams = info.get('streams') or []
        videos = [st for st in streams if st.get('codec_type') == 'video' and st.get('width') and st.get('height')]
        if not videos:
            return False
        fit = [st for st in videos if min(st['width'], st['height']) <= VIDEO_MAX_SIDE] or videos
        best = max(fit, key=lambda st: (min(st['width'], st['height']), int(st.get('bit_rate') or 0)))
        audio = [st for st in streams if st.get('codec_type') == 'audio']
        audio = max(audio, key=lambda st: int(st.get('bit_rate') or 0)) if audio else None
        tmp_out = f'{tmp}/out.mp4'
        cmd = ['ffmpeg', '-v', 'error', '-y', *net, '-i', src, '-map', f'0:{best["index"]}']
        if audio:
            cmd += ['-map', f'0:{audio["index"]}']
        cmd += ['-c', 'copy', '-movflags', '+faststart', '-fs', str(VIDEO_MAX_BYTES), tmp_out]
        subprocess.run(cmd, capture_output=True, timeout=VIDEO_MAX_S, check=True)
        done = Path(tmp_out)
        if not done.is_file() or done.stat().st_size < 1000 or done.stat().st_size >= VIDEO_MAX_BYTES:
            return False
        part = out.with_suffix('.part')
        shutil.move(str(done), part)
        part.replace(out)
    return True


def sweep_videos(limit: int = VIDEOS_PER_TICK) -> int:
    saved, tried = 0, 0
    for pid, media, meta in _picked_posts():
        failed = set((meta or {}).get('video_failed') or [])
        for m in (media or [])[:MAX_PER_POST]:
            url = m.get('url') or ''
            if m.get('kind') != 'video' or url in failed or not video_saveable(url) or video_path(url).is_file():
                continue
            if tried >= limit:
                return saved
            tried += 1
            try:
                ok = save_video(url)
            except Exception as e:  # noqa: BLE001 — a video we cannot save stays a link to the original
                log.debug('Common Room video %s failed: %s', url, e)
                ok = False
            if ok:
                saved += 1
                continue
            with session() as s:
                s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                post = s.get(CRPost, pid)
                if post:
                    post.meta = {**(post.meta or {}),
                                 'video_failed': [*((post.meta or {}).get('video_failed') or []), url][-MAX_PER_POST:]}
                    s.add(post)
                s.commit()
    return saved


def sweep() -> dict:
    return {'images': sweep_images(), 'comments': sweep_comments(), 'videos': sweep_videos()}


def gc(min_age_s: int = 3600) -> int:
    """Remove stored pictures no remaining post refers to."""
    if not MEDIA_DIR.is_dir():
        return 0
    with session() as s:
        keep = {_key(u) for media in s.exec(select(CRPost.media)).all() for u in _urls(media)}
        keep |= {_key(m['url']) for media in s.exec(select(CRPost.media)).all() for m in media or []
                 if m.get('kind') == 'video' and m.get('url')}
    now, removed = time.time(), 0
    for f in MEDIA_DIR.glob('*/*'):
        if f.stem not in keep and now - f.stat().st_mtime > min_age_s:
            f.unlink(missing_ok=True)
            removed += 1
    return removed
