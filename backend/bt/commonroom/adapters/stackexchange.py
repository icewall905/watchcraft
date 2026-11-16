"""Stack Exchange network via API 2.3."""
from __future__ import annotations

import gzip
import html
import json
import re
import time
from urllib.parse import quote

from . import JSON_TYPES, Adapter, Page, https, plain, title_of
from ...hub import social_fetch

API = 'https://api.stackexchange.com/2.3'
_SITE = re.compile(r'^[a-z0-9.]{2,40}$')
SORTS = {'new': 'creation', 'hot': 'hot', 'top_week': 'week', 'top_month': 'month', 'top_all': 'votes'}
_backoff_until = 0.0


def parse(locator: str) -> str:
    loc = (locator or '').strip().lower()
    if loc.startswith('https://') and '.stackexchange.com' in loc:
        loc = loc.split('//', 1)[1].split('.stackexchange.com')[0]
    elif loc.startswith('https://'):
        loc = loc.split('//', 1)[1].split('/')[0].removesuffix('.com')
    if not _SITE.match(loc):
        raise ValueError("Stack Exchange locator must be a site API name like 'superuser' or 'stackoverflow'.")
    return loc


def _key() -> str:
    try:
        from ...db import get_settings
        return str((get_settings().get('commonroom') or {}).get('stackexchange_key') or '').strip()
    except Exception:  # noqa: BLE001 - key is optional
        return ''


def _call(path: str, params: str) -> dict:
    global _backoff_until
    wait = _backoff_until - time.monotonic()
    if wait > 0:
        raise ValueError(f'Stack Exchange asked us to back off. Retry in {int(wait) + 1} seconds.')
    url = f'{API}{path}?{params}'
    key = _key()
    if key:
        url += f'&key={quote(key)}'
    data = social_fetch.fetch(url, kinds=JSON_TYPES, limit=4_000_000)
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    out = json.loads(data)
    if out.get('error_id'):
        raise ValueError(f"Stack Exchange error: {out.get('error_message') or out['error_id']}")
    if out.get('backoff'):
        secs = max(1, min(int(out['backoff']), 3600))
        _backoff_until = time.monotonic() + secs  # valid response: return it, pause the next call
    return out


def _post(q: dict, site: str) -> dict | None:
    qid = q.get('question_id')
    url = https(q.get('link'))
    title = plain(html.unescape(q.get('title') or ''), 300)
    if not qid or not url or not title:
        return None
    return {
        'platform': 'stackexchange', 'community': site, 'external_id': f'{site}:{qid}', 'url': url,
        'link_url': '', 'title': title_of(title), 'body': plain(q.get('body')),
        'author': html.unescape((q.get('owner') or {}).get('display_name') or ''), 'media': [],
        'score': int(q.get('score') or 0), 'comments': int(q.get('answer_count') or 0),
        'published_at': float(q.get('creation_date') or 0), 'nsfw': False, 'spoiler': False,
        'meta': {'site': site, 'question_id': qid, 'tags': q.get('tags') or []},
    }


def _cursor(cursor: str) -> int:
    try:
        return max(1, int(cursor or 1))
    except ValueError:
        raise ValueError('Invalid Stack Exchange cursor.') from None


def _page(data: dict, site_of, page: int) -> Page:
    posts = [p for p in (_post(q, site_of(q)) for q in data.get('items') or []) if p]
    return Page(posts, str(page + 1) if data.get('has_more') else '')


def fetch(locator: str, sort: str, cursor: str) -> Page:
    site = parse(locator)
    page = _cursor(cursor)
    data = _call('/questions', f'site={site}&sort={SORTS.get(sort, "creation")}&order=desc&pagesize=50'
                               f'&page={page}&filter=withbody')
    return _page(data, lambda q: site, page)


def search(query: str, cursor: str) -> Page:
    page = _cursor(cursor)
    data = _call('/search/advanced', f'q={quote(query)}&sort=creation&order=desc&site=stackoverflow'
                                     f'&pagesize=50&page={page}&filter=withbody')
    return _page(data, lambda q: 'stackoverflow', page)


def comments(post: dict) -> list[dict]:
    meta = post.get('meta') or {}
    site, qid = str(meta.get('site') or ''), str(meta.get('question_id') or '')
    if not _SITE.match(site) or not qid.isdigit():
        raise ValueError('Not a Stack Exchange post.')
    data = _call(f'/questions/{qid}/answers', f'site={site}&sort=votes&order=desc&filter=withbody&pagesize=20')
    return [{'id': str(a.get('answer_id') or ''), 'author': html.unescape((a.get('owner') or {}).get('display_name') or ''),
             'body': plain(a.get('body')), 'score': int(a.get('score') or 0),
             'published_at': float(a.get('creation_date') or 0), 'children': []}
            for a in data.get('items') or []]


ADAPTER = Adapter(key='stackexchange', label='Stack Exchange', parse=parse, fetch=fetch,
                  sorts=('new', 'hot', 'top_week', 'top_month', 'top_all'), search=search, comments=comments,
                  example='superuser', discoverable=False)
