"""Preview pictures for kept link posts that brought none of their own (Hacker News, Lobsters, most
Lemmy and Discourse links): the linked page's og:image, so the wall is not a column of plain text.

Runs from the scheduler on picked posts only, a few per tick, network outside transactions. Each post
is tried once (meta.preview_checked). Child profiles still see only kid-checked captured pictures."""
import logging
import time
from urllib.parse import urljoin

from sqlmodel import select

from .. import curation
from ..db import session
from ..hub import extract
from .models import CRPick, CRPost

log = logging.getLogger(__name__)
PER_TICK = 8
HTML_LIMIT = 1_500_000
SKIP_HOSTS = ('news.ycombinator.com', 'reddit.com', 'www.reddit.com', 'old.reddit.com')


def og_image(url: str) -> str:
    html = curation.fetch(url, kinds=curation.HTML_TYPES, limit=HTML_LIMIT)
    page = extract._Page()
    try:
        page.feed(html.decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001 — broken markup still yields the meta tags seen so far
        pass
    m = page.meta
    raw = m.get('og:image') or m.get('og:image:secure_url') or m.get('twitter:image') or m.get('twitter:image:src') or ''
    return extract.safe_image(urljoin(url, raw)) if raw else ''


def _due(limit: int) -> list[tuple[int, str]]:
    with session() as s:
        rows = s.exec(select(CRPost.id, CRPost.link_url, CRPost.media, CRPost.meta)
                      .where(CRPost.id.in_(select(CRPick.post_id)), CRPost.link_url != '')
                      .order_by(CRPost.published_at.desc()).limit(400)).all()
    out = []
    for pid, link, media, meta in rows:
        if media or (meta or {}).get('preview_checked') or not link.startswith('https://'):
            continue
        host = link.split('/', 3)[2].lower()
        if host in SKIP_HOSTS:
            continue
        out.append((pid, link))
        if len(out) >= limit:
            break
    return out


def sweep(limit: int = PER_TICK) -> int:
    found = 0
    for pid, link in _due(limit):
        try:
            image = og_image(link)
        except Exception as e:  # noqa: BLE001 — a page we cannot read just has no picture
            log.debug('Common Room preview for %s failed: %s', link, e)
            image = ''
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            post = s.get(CRPost, pid)
            if post and not post.media:
                post.meta = {**(post.meta or {}), 'preview_checked': time.time()}
                if image:
                    post.media = [{'kind': 'image', 'url': image, 'poster': '', 'w': 0, 'h': 0, 'from': 'link'}]
                    found += 1
                s.add(post)
            s.commit()
    return found
