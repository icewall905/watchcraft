"""Writes for Common Room: shared posts and sources. Short BEGIN IMMEDIATE transactions, no network."""
import time
from urllib.parse import urlsplit

from sqlmodel import select

from ..db import session
from .models import CRCandidate, CRPost, CRSource, CRSourcePost

FIELDS = ('community', 'external_id', 'author', 'title', 'body', 'link_url', 'score', 'comments', 'nsfw', 'spoiler')


def _thumb(media: list) -> bool:
    """Only a small feed thumbnail: a fuller picture found later replaces it."""
    first = media[0] if media else {}
    return first.get('from') == 'thumb' or 'width=140' in str(first.get('url') or '')


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or '') if url else ''
    return host[4:] if host.startswith('www.') else host


def _clean(d: dict) -> dict | None:
    url = str(d.get('url') or '')
    if not url.startswith('https://') or len(url) > 2000:
        return None
    title = ' '.join(str(d.get('title') or '').split())[:300]
    body = str(d.get('body') or '')[:20000]
    if not title and not body:
        return None
    media = [m for m in (d.get('media') or []) if isinstance(m, dict) and str(m.get('url') or m.get('poster') or '').startswith('https://')][:20]
    link = str(d.get('link_url') or '')
    link = link if link.startswith(('https://', 'http://')) and len(link) < 2000 else ''
    try:
        published = float(d.get('published_at') or 0)
    except (TypeError, ValueError):
        published = 0.0
    return {'url': url, 'platform': str(d.get('platform') or ''), 'community': str(d.get('community') or '')[:200],
            'external_id': str(d.get('external_id') or '')[:200], 'author': str(d.get('author') or '')[:200],
            'title': title or body.split('\n', 1)[0][:300], 'body': body, 'link_url': link, 'domain': _domain(link),
            'media': media, 'meta': d.get('meta') if isinstance(d.get('meta'), dict) else {},
            'score': int(d.get('score') or 0), 'comments': int(d.get('comments') or 0),
            'published_at': published or time.time(), 'nsfw': bool(d.get('nsfw')), 'spoiler': bool(d.get('spoiler'))}


def upsert_posts(posts: list[dict], source_id: int | None = None, thread_id: int | None = None) -> list[int]:
    """Store posts (refreshing score/comments of known ones). Links them to the source that delivered
    them, or queues them as candidates for the thread whose search found them. Returns post ids."""
    rows = [r for r in (_clean(p) for p in posts) if r]
    if not rows:
        return []
    ids = []
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        known = {p.url: p for p in s.exec(select(CRPost).where(CRPost.url.in_([r['url'] for r in rows]))).all()}
        for r in rows:
            post = known.get(r['url'])
            if post:
                post.score, post.comments = r['score'] or post.score, r['comments'] or post.comments
                post.fetched_at = time.time()
                if r['media'] and (not post.media or (_thumb(post.media) and not _thumb(r['media']))):
                    post.media = r['media']
            else:
                post = CRPost(**r)
                known[r['url']] = post
            s.add(post)
        s.flush()
        ids = list(dict.fromkeys(known[r['url']].id for r in rows))
        if source_id:
            have = set(s.exec(select(CRSourcePost.post_id).where(CRSourcePost.source_id == source_id,
                                                                 CRSourcePost.post_id.in_(ids))).all())
            for i in ids:
                if i not in have:
                    s.add(CRSourcePost(source_id=source_id, post_id=i))
        if thread_id:
            have = set(s.exec(select(CRCandidate.post_id).where(CRCandidate.thread_id == thread_id,
                                                                CRCandidate.post_id.in_(ids))).all())
            for i in ids:
                if i not in have:
                    s.add(CRCandidate(thread_id=thread_id, post_id=i))
        s.commit()
    return ids


def ensure_source(platform: str, locator: str, label: str = '') -> int:
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        src = s.exec(select(CRSource).where(CRSource.platform == platform, CRSource.locator == locator)).first()
        if not src:
            src = CRSource(platform=platform, locator=locator, label=label or locator)
            s.add(src)
        s.commit()
        s.refresh(src)
        return src.id
