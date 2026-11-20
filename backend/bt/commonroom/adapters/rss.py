"""RSS, Atom and JSON Feed sources: blogs, newsletters (Substack, Ghost, Buttondown), Medium tags, forum feeds.

Locator: the feed's https URL. One page per poll (feeds carry their newest ~50 entries); there is no
older-page walk, so the source simply stays fresh. Each entry is the article itself: ``url`` is the
article and there is no separate outbound link."""
from __future__ import annotations

from urllib.parse import urlsplit

from ... import curation
from ...hub import providers, social_fetch
from . import JSON_TYPES, TEXT_TYPES, Adapter, Page, https, plain, title_of

FEED_TYPES = TEXT_TYPES + JSON_TYPES + ('application/feed+json',)


def parse(locator: str) -> str:
    raw = (locator or '').strip()
    if not raw.lower().startswith('https://') or len(raw) > 1000:
        raise ValueError('Use the https:// address of an RSS or Atom feed.')
    parts = urlsplit(raw)
    if parts.username or parts.password or not parts.hostname:
        raise ValueError('Use a plain https:// feed address.')
    return curation.canonical(raw)


def fetch(locator: str, sort: str = 'new', cursor: str = '') -> Page:
    if cursor:
        return Page()
    url = parse(locator)
    data = social_fetch.fetch(url, kinds=FEED_TYPES, limit=4_000_000)
    host = urlsplit(url).hostname or ''
    posts = []
    for row in providers.parse_feed(data, url):
        link = https(row.get('url'))
        title = plain(row.get('title'), 400)
        if not link or not title or row.get('type') == 'video':
            continue
        image = https(row.get('image'))
        posts.append({
            'platform': 'rss', 'community': plain(row.get('publisher'), 120) or host,
            'external_id': str(row.get('external_id') or link)[:500], 'url': link, 'link_url': '',
            'title': title_of(title), 'body': plain(row.get('summary')), 'author': plain(row.get('author'), 120),
            'media': [{'kind': 'image', 'url': image, 'poster': '', 'w': 0, 'h': 0}] if image else [],
            'score': 0, 'comments': 0, 'published_at': float(row.get('published_at') or 0),
            'nsfw': False, 'spoiler': False, 'meta': {'feed': url},
        })
    return Page(posts, '')


ADAPTER = Adapter(key='rss', label='Blog / feed', parse=parse, fetch=fetch, sorts=('new',),
                  example='https://example.substack.com/feed')
