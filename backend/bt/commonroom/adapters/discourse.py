"""Discourse forums via their public JSON endpoints."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from . import Adapter, Page, get_json, https, plain, title_of

_HOST = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$')
_CAT = re.compile(r'^/c/((?:[a-z0-9_-]+/)+)(\d+)(?:/|$)')
PERIODS = {'top_week': 'weekly', 'top_month': 'monthly', 'top_year': 'yearly', 'top_all': 'all'}


def parse(locator: str) -> str:
    raw = (locator or '').strip()
    if not raw.lower().startswith('https://'):
        raise ValueError('Discourse locator must be an https:// forum URL.')
    p = urlsplit(raw)
    host = (p.hostname or '').lower()
    if p.username or p.password or (p.port not in (None, 443)) or not _HOST.match(host):
        raise ValueError('Discourse locator needs a plain forum hostname (no credentials, port or IP).')
    base = f'https://{host}'
    m = _CAT.match(p.path.lower())
    return f'{base}/c/{m.group(1)}{m.group(2)}' if m else base


def _split(locator: str) -> tuple[str, str]:
    loc = parse(locator)
    i = loc.find('/c/')
    return (loc, '') if i < 0 else (loc[:i], loc[i:])


def _ts(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def fetch(locator: str, sort: str, cursor: str) -> Page:
    base, cat = _split(locator)
    try:
        page = max(0, int(cursor or 0))
    except ValueError:
        raise ValueError('Invalid Discourse cursor.') from None
    if sort in PERIODS:
        url = f'{base}{cat}/l/top.json?period={PERIODS[sort]}&page={page}' if cat else \
            f'{base}/top.json?period={PERIODS[sort]}&page={page}'
    else:
        url = f'{base}{cat}.json?order=created&page={page}' if cat else \
            f'{base}/latest.json?order=created&page={page}'
    tl = get_json(url).get('topic_list') or {}
    host = urlsplit(base).hostname
    posts = []
    for t in tl.get('topics') or []:
        tid, slug, title = t.get('id'), t.get('slug') or 'topic', plain(t.get('title'), 300)
        if not tid or not title or not re.fullmatch(r'[A-Za-z0-9_-]+', str(slug)):
            continue
        image = https(t.get('image_url'))
        posts.append({
            'platform': 'discourse', 'community': host, 'external_id': f'{host}:{tid}',
            'url': f'{base}/t/{slug}/{tid}', 'link_url': '', 'title': title_of(title),
            'body': plain(t.get('excerpt')), 'author': '',
            'media': [{'kind': 'image', 'url': image, 'poster': '', 'w': 0, 'h': 0}] if image else [],
            'score': int(t.get('like_count') or 0), 'comments': max(0, int(t.get('posts_count') or 1) - 1),
            'published_at': _ts(t.get('created_at')), 'nsfw': False, 'spoiler': False,
            'meta': {'base': base, 'topic_id': tid, 'tags': [x if isinstance(x, str) else x.get('name', '')
                                                             for x in t.get('tags') or []]},
        })
    return Page(posts, str(page + 1) if tl.get('more_topics_url') else '')


def comments(post: dict) -> list[dict]:
    meta = post.get('meta') or {}
    base, tid = str(meta.get('base') or ''), str(meta.get('topic_id') or '')
    if parse(base) != base or not tid.isdigit():
        raise ValueError('Not a Discourse post.')
    data = get_json(f'{base}/t/{tid}.json')
    nodes: dict[int, dict] = {}
    order = []
    for p in (data.get('post_stream') or {}).get('posts') or []:
        n = p.get('post_number')
        if not isinstance(n, int) or n == 1 or p.get('hidden'):
            continue
        likes = next((int(a.get('count') or 0) for a in p.get('actions_summary') or [] if a.get('id') == 2), 0)
        nodes[n] = {'id': str(p.get('id') or n), 'author': p.get('username') or '', 'body': plain(p.get('cooked')),
                    'score': likes, 'published_at': _ts(p.get('created_at')), 'children': []}
        order.append((n, p.get('reply_to_post_number')))
    top = []
    for n, parent in order:
        holder = nodes.get(parent) if isinstance(parent, int) and parent < n else None
        (holder['children'] if holder else top).append(nodes[n])
    return top


ADAPTER = Adapter(key='discourse', label='Discourse', parse=parse, fetch=fetch,
                  sorts=('new', 'top_week', 'top_month', 'top_year', 'top_all'), comments=comments,
                  example='https://forum.example.com', discoverable=False)
