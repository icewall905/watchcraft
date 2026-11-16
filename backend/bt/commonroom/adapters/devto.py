"""DEV Community (dev.to) via its public API."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

from . import Adapter, Page, get_json, https, plain, title_of

BASE = 'https://dev.to/api'
_TAG = re.compile(r'^[a-z0-9]{1,40}$')
TOP = {'top_week': 7, 'top_month': 30, 'top_year': 365, 'top_all': 3650}
PER_PAGE = 50


def parse(locator: str) -> str:
    loc = (locator or '').strip().lower().lstrip('#')
    if loc.startswith('https://dev.to/t/'):
        loc = loc.split('/t/', 1)[1].split('/')[0]
    if not _TAG.match(loc):
        raise ValueError('DEV locator must be a tag name like "python" (letters and digits only).')
    return loc


def _ts(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _post(a: dict) -> dict | None:
    aid = a.get('id')
    url = https(a.get('url') or a.get('canonical_url'))
    title = plain(a.get('title'), 300)
    if not aid or not url or not title:
        return None
    tags = a.get('tag_list')
    tags = tags if isinstance(tags, list) else [t.strip() for t in str(tags or '').split(',') if t.strip()]
    image = https(a.get('cover_image') or a.get('social_image'))
    user = a.get('user') or {}
    return {
        'platform': 'devto', 'community': '#' + (tags[0] if tags else 'dev'), 'external_id': str(aid),
        'url': url, 'link_url': '', 'title': title_of(title), 'body': plain(a.get('description')),
        'author': user.get('username') or user.get('name') or '',
        'media': [{'kind': 'image', 'url': image, 'poster': '', 'w': 0, 'h': 0}] if image else [],
        'score': int(a.get('public_reactions_count') or a.get('positive_reactions_count') or 0),
        'comments': int(a.get('comments_count') or 0),
        'published_at': _ts(a.get('published_timestamp') or a.get('published_at')),
        'nsfw': False, 'spoiler': False, 'meta': {'article_id': aid, 'tags': tags},
    }


def fetch(locator: str, sort: str, cursor: str) -> Page:
    tag = parse(locator)
    try:
        page = max(1, int(cursor or 1))
    except ValueError:
        raise ValueError('Invalid DEV cursor.') from None
    t = quote(tag)
    if sort == 'new':
        url = f'{BASE}/articles/latest?tag={t}&per_page={PER_PAGE}&page={page}'
    else:
        url = f'{BASE}/articles?tag={t}&per_page={PER_PAGE}&page={page}'
        if sort in TOP:
            url += f'&top={TOP[sort]}'
    data = get_json(url)
    items = data if isinstance(data, list) else []
    posts = [p for p in (_post(a) for a in items if isinstance(a, dict)) if p]
    return Page(posts, str(page + 1) if len(items) >= PER_PAGE else '')


def comments(post: dict) -> list[dict]:
    aid = str(post.get('external_id') or (post.get('meta') or {}).get('article_id') or '')
    if not aid.isdigit():
        raise ValueError('Not a DEV post.')

    def walk(nodes) -> list[dict]:
        out = []
        for c in nodes or []:
            if not isinstance(c, dict) or c.get('deleted') or c.get('hidden_by_commentable_user'):
                continue
            out.append({'id': str(c.get('id_code') or ''), 'author': (c.get('user') or {}).get('username') or '',
                        'body': plain(c.get('body_html')), 'score': 0, 'published_at': _ts(c.get('created_at')),
                        'children': walk(c.get('children'))})
        return out
    data = get_json(f'{BASE}/comments?a_id={aid}')
    return walk(data if isinstance(data, list) else [])


ADAPTER = Adapter(key='devto', label='DEV Community', parse=parse, fetch=fetch,
                  sorts=('new', 'hot', 'top_week', 'top_month', 'top_year', 'top_all'),
                  comments=comments, example='python', discoverable=False)
