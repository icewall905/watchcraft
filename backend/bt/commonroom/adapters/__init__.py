"""Platform adapters for Common Room.

Every adapter module exposes a module-level ``ADAPTER = Adapter(...)``. All network calls go
through ``get_json`` / ``get_text`` below (per-host pacing, Retry-After, SSRF-safe fetch). Never
open sockets directly, never fetch inside a DB transaction.

Normalized post dict (what ``fetch`` and ``search`` return in ``Page.posts``)::

    {
      'platform': 'reddit',             # adapter key
      'community': 'r/selfhosted',      # display name of where it was posted ('' if none)
      'external_id': 't3_abc123',       # stable id on the platform
      'url': 'https://www.reddit.com/r/selfhosted/comments/abc123/x/',  # canonical permalink (https)
      'link_url': 'https://example.com/article',  # outbound link, '' for self/text posts
      'title': 'Post title',            # plain text, <= 300 chars; for title-less platforms use the first line
      'body': 'Plain text body',        # plain text (HTML stripped), <= 20000 chars
      'author': 'name',
      'media': [{'kind': 'image'|'video', 'url': 'https://...', 'poster': '', 'w': 0, 'h': 0}],  # https only
      'score': 123,                     # upvotes/favourites/likes, 0 if unknown
      'comments': 45,                   # comment/reply count, 0 if unknown
      'published_at': 1731700000.0,     # epoch seconds
      'nsfw': False, 'spoiler': False,
      'meta': {...},                    # anything ``comments()`` needs later (ids, instance, api urls)
    }

Normalized comment dict (``comments()`` returns a list of top-level comments)::

    {'id': '...', 'author': 'name', 'body': 'plain text', 'score': 0,
     'published_at': 0.0, 'children': [ ...same shape... ]}

Sort ladder: ``Adapter.sorts`` lists the sort names the adapter supports, freshest first, from
``LADDER``. ``fetch(locator, sort, cursor)`` returns one page; ``Page.cursor`` is the token for
the next older page of the same sort ('' = no more pages).
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from ...hub import social_fetch

LADDER = ('new', 'hot', 'top_day', 'top_week', 'top_month', 'top_year', 'top_all')
JSON_TYPES = ('application/json', 'application/activity+json', 'text/json')
TEXT_TYPES = ('text/html', 'application/xhtml+xml', 'application/rss+xml', 'application/atom+xml',
              'application/xml', 'text/xml', 'text/plain')
MAX_BODY = 20000


@dataclass
class Page:
    posts: list[dict] = field(default_factory=list)
    cursor: str = ''


@dataclass
class Adapter:
    key: str
    label: str
    # Validate/normalize a user- or AI-entered locator. Raise ValueError with a readable message.
    parse: Callable[[str], str]
    # One page of a community/feed, newest-first for 'new'.
    fetch: Callable[[str, str, str], Page]
    sorts: tuple[str, ...] = ('new',)
    # Keyword search across the platform: (query, cursor) -> Page. None when unsupported.
    search: Callable[[str, str], Page] | None = None
    # Comment tree for a stored post: (post dict with url/external_id/community/meta) -> list.
    comments: Callable[[dict], list[dict]] | None = None
    # Locator example shown in the UI.
    example: str = ''
    # Whether discovery may suggest locators for this platform (needs a cheap existence check).
    discoverable: bool = True


def get_json(url: str, headers: dict | None = None, limit: int = 4_000_000):
    data = social_fetch.fetch(url, kinds=JSON_TYPES, limit=limit, headers=headers)
    return json.loads(data)


def get_text(url: str, kinds=TEXT_TYPES, headers: dict | None = None, limit: int = 4_000_000) -> str:
    return social_fetch.fetch(url, kinds=kinds, limit=limit, headers=headers).decode('utf-8', 'replace')


_TAG = re.compile(r'<(br|/p|/div|/li|/h[1-6]|/blockquote)\s*/?>', re.I)
_ANY = re.compile(r'<[^>]+>')


def plain(text: str | None, limit: int = MAX_BODY) -> str:
    """HTML or markdown-ish text to plain text: tags dropped, entities decoded, whitespace tidied."""
    if not text:
        return ''
    text = _TAG.sub('\n', str(text))
    text = html.unescape(_ANY.sub('', text))
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()[:limit]


def https(url: str | None) -> str:
    url = html.unescape((url or '').strip())
    return url if url.startswith('https://') and len(url) < 2000 else ''


def title_of(text: str, limit: int = 300) -> str:
    line = (text or '').strip().split('\n', 1)[0]
    return line[:limit - 1] + '…' if len(line) > limit else line


def _load() -> dict[str, Adapter]:
    import importlib
    out: dict[str, Adapter] = {}
    for name in ('reddit', 'lemmy', 'mbin', 'mastodon', 'bluesky', 'hackernews', 'lobsters', 'devto',
                 'tildes', 'stackexchange', 'discourse'):
        try:
            mod = importlib.import_module(f'{__name__}.{name}')
        except ModuleNotFoundError as e:
            if e.name == f'{__name__}.{name}':
                continue  # adapter not built yet
            raise
        out[mod.ADAPTER.key] = mod.ADAPTER
    return out


ADAPTERS: dict[str, Adapter] = _load()


def get(platform: str) -> Adapter:
    try:
        return ADAPTERS[platform]
    except KeyError:
        raise ValueError(f'Unknown platform {platform!r}.') from None
