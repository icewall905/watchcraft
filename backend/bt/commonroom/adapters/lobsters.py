"""Lobsters via its public .json endpoints."""
from __future__ import annotations

import re
from datetime import datetime

from . import Adapter, Page, get_json, https, plain, title_of

BASE = 'https://lobste.rs'
_TAG = re.compile(r'^[a-z0-9][a-z0-9_.+-]{0,39}$')


def parse(locator: str) -> str:
    loc = (locator or '').strip().lower().strip('/')
    if loc.startswith('https://lobste.rs/t/'):
        loc = loc.split('/t/', 1)[1].split('/')[0].split('.json')[0]
    if loc in ('hottest', 'newest') or _TAG.match(loc):
        return loc
    raise ValueError("Lobsters locator must be 'hottest', 'newest' or a tag name like 'programming'.")


def _ts(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _user(value) -> str:
    return (value.get('username') if isinstance(value, dict) else value) or ''


def _post(s: dict) -> dict | None:
    sid = str(s.get('short_id') or '')
    url = https(s.get('comments_url') or s.get('short_id_url'))
    title = plain(s.get('title'), 300)
    if not sid or not url or not title:
        return None
    tags = [t for t in s.get('tags') or [] if isinstance(t, str)]
    return {
        'platform': 'lobsters', 'community': tags[0] if tags else 'Lobsters', 'external_id': sid,
        'url': url, 'link_url': https(s.get('url')), 'title': title_of(title),
        'body': plain(s.get('description_plain') or s.get('description')),
        'author': _user(s.get('submitter_user')), 'media': [],
        'score': int(s.get('score') or 0), 'comments': int(s.get('comment_count') or 0),
        'published_at': _ts(s.get('created_at')), 'nsfw': False, 'spoiler': False,
        'meta': {'short_id': sid, 'tags': tags},
    }


def fetch(locator: str, sort: str, cursor: str) -> Page:
    loc = parse(locator)
    try:
        page = max(1, int(cursor or 1))
    except ValueError:
        raise ValueError('Invalid Lobsters cursor.') from None
    # Built-in feeds follow the sort (paged by path); tag pages do not paginate.
    if loc in ('hottest', 'newest'):
        feed = 'newest' if sort == 'new' else 'hottest'
        if feed == 'newest':
            path = '/newest.json' if page == 1 else f'/newest/page/{page}.json'
        else:
            path = '/hottest.json' if page == 1 else f'/page/{page}.json'
    else:
        path = f'/t/{loc}.json'
    data = get_json(f'{BASE}{path}')
    posts = [p for p in (_post(s) for s in data if isinstance(s, dict)) if p] if isinstance(data, list) else []
    more = loc in ('hottest', 'newest') and len(data) >= 25
    return Page(posts, str(page + 1) if more else '')


def comments(post: dict) -> list[dict]:
    sid = str(post.get('external_id') or (post.get('meta') or {}).get('short_id') or '')
    if not re.fullmatch(r'[a-z0-9]{3,12}', sid):
        raise ValueError('Not a Lobsters post.')
    story = get_json(f'{BASE}/s/{sid}.json')
    nodes: dict[str, dict] = {}
    order = []
    for c in story.get('comments') or []:
        cid = str(c.get('short_id') or '')
        if not cid or c.get('is_deleted') or c.get('is_moderated'):
            continue
        nodes[cid] = {'id': cid, 'author': _user(c.get('commenting_user')),
                      'body': plain(c.get('comment_plain') or c.get('comment')), 'score': int(c.get('score') or 0),
                      'published_at': _ts(c.get('created_at')), 'children': []}
        order.append((cid, c.get('parent_comment')))
    top = []
    for cid, parent in order:
        holder = nodes.get(str(parent)) if parent else None
        (holder['children'] if holder else top).append(nodes[cid])
    return top


ADAPTER = Adapter(key='lobsters', label='Lobsters', parse=parse, fetch=fetch, sorts=('new', 'hot'),
                  comments=comments, example='hottest', discoverable=False)
