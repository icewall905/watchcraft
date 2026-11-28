"""Lemmy communities (API v3). Locator: ``community@instance.tld``."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote, urlencode, urlsplit

from . import Adapter, Page, get_json, https, plain, title_of

HOST = r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
_LOC = re.compile(rf'^!?([a-z0-9_]{{1,100}})@({HOST})(?: via ({HOST}))?$')
_URL_PATH = re.compile(r'^/c/([A-Za-z0-9_]{1,100})(?:@(' + HOST + r'))?/?$', re.I)
_IMAGE = re.compile(r'\.(?:jpe?g|png|gif|webp|avif)(?:\?.*)?$', re.I)
SORTS = {'new': 'New', 'hot': 'Hot', 'top_day': 'TopDay', 'top_week': 'TopWeek', 'top_month': 'TopMonth',
         'top_year': 'TopYear', 'top_all': 'TopAll'}
LIMIT = 50
MAX_PAGE = 20


def parse(locator: str) -> str:
    s = (locator or '').strip()
    if s.startswith('https://'):
        p = urlsplit(s)
        m = _URL_PATH.match(p.path)
        host = (p.hostname or '').lower()
        if not m or not re.fullmatch(HOST, host):
            raise ValueError('Use a Lemmy community like community@lemmy.world or https://lemmy.world/c/community.')
        s = f'{m.group(1)}@{m.group(2) or host}' + (f' via {host}' if m.group(2) and m.group(2).lower() != host else '')
    m = _LOC.match(s.lower())
    if not m:
        raise ValueError('Use a Lemmy community like community@lemmy.world (or community@home.tld via lemmy.world).')
    via = f' via {m.group(3)}' if m.group(3) and m.group(3) != m.group(2) else ''
    return f'{m.group(1)}@{m.group(2)}{via}'


def _ts(v) -> float:
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _post(row: dict, instance: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    p, c = row.get('post') or {}, row.get('community') or {}
    url = https(p.get('ap_id'))
    if not url or p.get('removed') or p.get('deleted'):
        return None
    raw = https(p.get('url'))
    link = '' if _IMAGE.search(raw) else raw
    media = []
    img = raw if _IMAGE.search(raw) else https(p.get('thumbnail_url'))
    if img:
        media.append({'kind': 'image', 'url': img, 'poster': '', 'w': 0, 'h': 0})
    counts = row.get('counts') or {}
    name = c.get('name') or ''
    actor = urlsplit(c.get('actor_id') or '').hostname or instance
    return {
        'platform': 'lemmy', 'community': f'!{name}@{actor}' if name else '',
        'external_id': str(p.get('ap_id') or p.get('id')), 'url': url, 'link_url': link,
        'title': title_of(plain(p.get('name'))), 'body': plain(p.get('body')),
        'author': str((row.get('creator') or {}).get('name') or ''), 'media': media,
        'score': _int(counts.get('score')), 'comments': _int(counts.get('comments')),
        'published_at': _ts(p.get('published')), 'nsfw': bool(p.get('nsfw') or c.get('nsfw')), 'spoiler': False,
        'meta': {'instance': instance, 'post_id': p.get('id')},
    }


def _page(data, instance: str, cursor: str) -> Page:
    rows = (data or {}).get('posts') if isinstance(data, dict) else None
    rows = rows if isinstance(rows, list) else []
    posts = [x for x in (_post(r, instance) for r in rows) if x]
    n = int(cursor) if cursor.isdigit() else 1
    return Page(posts, str(n + 1) if len(rows) >= LIMIT and n < MAX_PAGE else '')


def _page_no(cursor: str) -> int:
    return max(1, min(int(cursor), MAX_PAGE)) if cursor.isdigit() else 1


def fetch(locator: str, sort: str, cursor: str = '') -> Page:
    loc = parse(locator)
    loc, _, via = loc.partition(' via ')
    instance = via or loc.split('@', 1)[1]
    q = urlencode({'community_name': loc, 'sort': SORTS.get(sort, 'New'), 'limit': LIMIT, 'page': _page_no(cursor)})
    return _page(get_json(f'https://{instance}/api/v3/post/list?{q}'), instance, str(_page_no(cursor)))


def search(query: str, cursor: str = '') -> Page:
    if not (query or '').strip():
        return Page()
    q = urlencode({'q': query.strip()[:200], 'type_': 'Posts', 'sort': 'New', 'limit': LIMIT, 'page': _page_no(cursor)})
    return _page(get_json(f'https://lemmy.world/api/v3/search?{q}'), 'lemmy.world', str(_page_no(cursor)))


def comments(post: dict) -> list[dict]:
    meta = post.get('meta') or {}
    instance, pid = str(meta.get('instance') or ''), meta.get('post_id')
    if not re.fullmatch(HOST, instance) or not isinstance(pid, int):
        return []
    q = urlencode({'post_id': pid, 'sort': 'Top', 'max_depth': 6, 'limit': 50, 'type_': 'All'})
    data = get_json(f'https://{instance}/api/v3/comment/list?{q}')
    nodes: dict[str, dict] = {}
    order: list[tuple[str, str]] = []
    for row in (data.get('comments') if isinstance(data, dict) else None) or []:
        c = (row or {}).get('comment') or {}
        if c.get('removed') or c.get('deleted'):
            continue
        ids = str(c.get('path') or '').split('.')
        if len(ids) < 2 or not all(i.isdigit() for i in ids):
            continue
        cid = ids[-1]
        nodes[cid] = {'id': cid, 'author': str((row.get('creator') or {}).get('name') or ''),
                      'body': plain(c.get('content')), 'score': _int((row.get('counts') or {}).get('score')),
                      'published_at': _ts(c.get('published')), 'children': []}
        order.append((cid, ids[-2]))
    top = []
    for cid, parent in order:
        (nodes[parent]['children'] if parent in nodes else top).append(nodes[cid])
    return top


ADAPTER = Adapter(key='lemmy', label='Lemmy', parse=parse, fetch=fetch, sorts=tuple(SORTS), search=search,
                  comments=comments, example='selfhosted@lemmy.world, or linux@lemmy.ml via lemmy.world')
