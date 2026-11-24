"""Preview pictures for kept posts that brought none of their own: the linked page's og:image (Hacker News,
Lobsters, most Lemmy and Discourse links), or the post's own page for scraped sites, RSS entries and forum topics,
so the wall is not a column of plain text. Reddit mirrors also give the post's author, subreddit and votes.

Runs from the scheduler on picked posts only, a few per tick, network outside transactions. Each post
is tried once (meta.preview_checked). Child profiles still see only kid-checked captured pictures."""
import logging
import re
import time
from urllib.parse import urljoin, urlsplit

from sqlmodel import select

from .. import curation
from ..db import session
from ..hub import extract
from . import social
from .adapters.reddit import direct_image
from .models import CRPick, CRPost
from .store import _thumb

log = logging.getLogger(__name__)
PER_TICK = 8
HTML_LIMIT = 1_500_000
SKIP_HOSTS = ('news.ycombinator.com', 'reddit.com', 'www.reddit.com', 'old.reddit.com')


OWN_PAGE = ('web', 'rss', 'discourse')   # posts whose own page is the article (no separate link)
SITE_ART = re.compile(r'(logo|favicon|apple-touch|default[-_]?(image|og|share)|og-image\.png|placeholder|avatar)', re.I)
_VOTES = re.compile(r'by u/([A-Za-z0-9_-]{3,20}) in r/([A-Za-z0-9_]{2,21})\. ([\d,]+) upvotes')


def full_size(url: str) -> str:
    """Reddit's preview.redd.it thumbnail (width=320) → the original picture on i.redd.it."""
    p = urlsplit(url)
    if p.hostname == 'preview.redd.it' and re.fullmatch(r'/[A-Za-z0-9]+\.(?:jpe?g|png|gif|webp)', p.path):
        return f'https://i.redd.it{p.path}'
    return url


def page_meta(url: str) -> dict:
    html = curation.fetch(url, kinds=curation.HTML_TYPES, limit=HTML_LIMIT)
    page = extract._Page()
    try:
        page.feed(html.decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001 — broken markup still yields the meta tags seen so far
        pass
    return page.meta


def image_of(url: str, m: dict) -> str:
    """The page's share picture, full size; site-wide logos and placeholders do not count."""
    raw = m.get('og:image') or m.get('og:image:secure_url') or m.get('twitter:image') or m.get('twitter:image:src') or ''
    image = extract.safe_image(full_size(urljoin(url, raw))) if raw else ''
    return '' if not image or SITE_ART.search(urlsplit(image).path) else image


def og_image(url: str) -> str:
    return image_of(url, page_meta(url))


def reddit_info(m: dict) -> dict:
    """Author, subreddit and votes from a Reddit mirror's description ("by u/x in r/y. 16,406 upvotes")."""
    found = _VOTES.search(m.get('og:description') or m.get('description') or '')
    return {'author': found.group(1), 'sub': found.group(2), 'score': int(found.group(3).replace(',', ''))} \
        if found else {}


def _due(limit: int) -> list[tuple[int, str]]:
    with session() as s:
        rows = s.exec(select(CRPost.id, CRPost.link_url, CRPost.url, CRPost.platform, CRPost.media, CRPost.meta)
                      .where(CRPost.id.in_(select(CRPick.post_id)),
                             (CRPost.link_url != '') | CRPost.platform.in_(OWN_PAGE))
                      .order_by(CRPost.published_at.desc()).limit(400)).all()
    out = []
    for pid, link, url, platform, media, meta in rows:
        link = link or (url if platform in OWN_PAGE else '')
        if (media and not _thumb(media)) or (meta or {}).get('preview_checked') or not link.startswith('https://'):
            continue
        host = link.split('/', 3)[2].lower()
        if host in SKIP_HOSTS and not direct_image(link):
            continue
        out.append((pid, link))
        if len(out) >= limit:
            break
    return out


def sweep(limit: int = PER_TICK) -> int:
    found = 0
    for pid, link in _due(limit):
        info = {}
        try:
            if direct_image(link):
                image = link
            else:
                m = page_meta(link)
                image = image_of(link, m)
                info = reddit_info(m) if social.reddit_ref(link) else {}
        except Exception as e:  # noqa: BLE001 — a page we cannot read just has no picture
            log.debug('Common Room preview for %s failed: %s', link, e)
            image = ''
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            post = s.get(CRPost, pid)
            if post and info:
                post.author = post.author or info['author']
                post.score = max(post.score or 0, info['score'])
                post.meta = {**(post.meta or {}), 'subreddit': info['sub']}
                s.add(post)
            if post and (not post.media or _thumb(post.media)):
                post.meta = {**(post.meta or {}), 'preview_checked': time.time()}
                if image:
                    post.media = [{'kind': 'image', 'url': image, 'poster': '', 'w': 0, 'h': 0, 'from': 'link'}]
                    found += 1
                s.add(post)
            s.commit()
    return found
