"""Hacker News via the Algolia API."""
from __future__ import annotations

from . import Adapter, Page, MAX_BODY, get_json, https, plain, title_of

BASE = 'https://hn.algolia.com/api/v1'
LOCATORS = {'front': 'front_page', 'new': 'story', 'ask': 'ask_hn', 'show': 'show_hn', 'best': 'story'}
MAX_COMMENTS = 600


def parse(locator: str) -> str:
    loc = (locator or '').strip().lower().strip('/')
    if loc not in LOCATORS:
        raise ValueError(f"Hacker News locator must be one of: {', '.join(LOCATORS)}.")
    return loc


def _post(hit: dict) -> dict | None:
    oid = str(hit.get('objectID') or '')
    title = plain(hit.get('title') or hit.get('story_title') or '', 300)
    if not oid.isdigit() or not title:
        return None
    return {
        'platform': 'hackernews', 'community': 'Hacker News', 'external_id': oid,
        'url': f'https://news.ycombinator.com/item?id={oid}', 'link_url': https(hit.get('url')),
        'title': title_of(title), 'body': plain(hit.get('story_text'), MAX_BODY),
        'author': hit.get('author') or '', 'media': [],
        'score': int(hit.get('points') or 0), 'comments': int(hit.get('num_comments') or 0),
        'published_at': float(hit.get('created_at_i') or 0), 'nsfw': False, 'spoiler': False,
        'meta': {'id': oid},
    }


def _page(url: str, page: int) -> Page:
    data = get_json(url)
    posts = [p for p in (_post(h) for h in data.get('hits') or []) if p]
    more = page + 1 < int(data.get('nbPages') or 0)
    return Page(posts, str(page + 1) if more else '')


def _cursor(cursor: str) -> int:
    try:
        return max(0, int(cursor or 0))
    except ValueError:
        raise ValueError('Invalid Hacker News cursor.') from None


def fetch(locator: str, sort: str, cursor: str) -> Page:
    loc = parse(locator)
    page = _cursor(cursor)
    tag = LOCATORS[loc]
    if loc == 'best':
        url = f'{BASE}/search_by_date?tags=story&numericFilters=points%3E%3D100&hitsPerPage=50&page={page}'
    elif loc == 'front' or sort == 'hot':
        url = f'{BASE}/search?tags={tag}&hitsPerPage=50&page={page}'
    else:
        url = f'{BASE}/search_by_date?tags={tag}&hitsPerPage=50&page={page}'
    return _page(url, page)


def search(query: str, cursor: str) -> Page:
    from urllib.parse import quote
    page = _cursor(cursor)
    return _page(f'{BASE}/search_by_date?query={quote(query)}&tags=story&hitsPerPage=50&page={page}', page)


def comments(post: dict) -> list[dict]:
    oid = str(post.get('external_id') or (post.get('meta') or {}).get('id') or '')
    if not oid.isdigit():
        raise ValueError('Not a Hacker News post.')
    root = get_json(f'{BASE}/items/{oid}')
    budget = [MAX_COMMENTS]

    def walk(nodes) -> list[dict]:
        out = []
        for n in nodes or []:
            if budget[0] <= 0:
                break
            budget[0] -= 1
            body = plain(n.get('text'))
            kids = walk(n.get('children'))
            if not body and not kids:
                continue  # deleted comment
            out.append({'id': str(n.get('id') or ''), 'author': n.get('author') or '', 'body': body,
                        'score': int(n.get('points') or 0), 'published_at': float(n.get('created_at_i') or 0),
                        'children': kids})
        return out
    return walk(root.get('children'))


ADAPTER = Adapter(key='hackernews', label='Hacker News', parse=parse, fetch=fetch, sorts=('new', 'hot'),
                  search=search, comments=comments, example='front, ask or show')
