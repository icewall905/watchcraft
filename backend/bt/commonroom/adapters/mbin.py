"""Mbin / kbin magazines. Locator: ``magazine@instance`` (bare name defaults to kbin.earth)."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote, urlencode, urlsplit

from . import Adapter, Page, get_json, https, plain, title_of

HOST = r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
DEFAULT_HOST = 'kbin.earth'
_LOC = re.compile(rf'^!?([a-z0-9_]{{1,50}})(?:@({HOST}))?(?: via ({HOST}))?$')
_URL_PATH = re.compile(r'^/m/([A-Za-z0-9_]{1,50})(?:@(' + HOST + r'))?/?$', re.I)
# ladder sort -> (sort, time)
SORTS = {'new': ('newest', ''), 'hot': ('hot', ''), 'top_day': ('top', '1d'), 'top_week': ('top', '1w'),
         'top_month': ('top', '1m'), 'top_year': ('top', '1y'), 'top_all': ('top', 'all')}
PER_PAGE = 50
MAX_PAGE = 20


def parse(locator: str) -> str:
    s = (locator or '').strip()
    if s.startswith('https://'):
        p = urlsplit(s)
        m = _URL_PATH.match(p.path)
        host = (p.hostname or '').lower()
        if not m or not re.fullmatch(HOST, host):
            raise ValueError('Use a magazine like technology@kbin.earth or https://kbin.earth/m/technology.')
        s = f'{m.group(1)}@{m.group(2) or host}' + (f' via {host}' if m.group(2) and m.group(2).lower() != host else '')
    m = _LOC.match(s.lower())
    if not m:
        raise ValueError('Use a magazine like technology@kbin.earth (or tech@home.tld via kbin.earth).')
    home = m.group(2) or DEFAULT_HOST
    via = f' via {m.group(3)}' if m.group(3) and m.group(3) != home else ''
    return f'{m.group(1)}@{home}{via}'


def _ts(v) -> float:
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _name(d) -> str:
    d = d if isinstance(d, dict) else {}
    return str(d.get('username') or d.get('name') or '')


def _entry(e: dict, host: str) -> dict | None:
    if not isinstance(e, dict) or not e.get('entryId') or e.get('isDeleted'):
        return None
    mag = e.get('magazine') or {}
    mname = str(mag.get('name') or '')
    url = https(e.get('apId')) or ''
    slug = quote(str(e.get('slug') or '-'), safe='-_')
    url = f'https://{host}/m/{quote(mname, safe="@._-")}/t/{e["entryId"]}/{slug}' if mname else url
    if not url:
        return None
    raw = https(e.get('url'))
    img = e.get('image') if isinstance(e.get('image'), dict) else {}
    iurl = https(img.get('storageUrl')) or (https(img.get('sourceUrl')) if 'sourceUrl' in img else '')
    media = [{'kind': 'image', 'url': iurl, 'poster': '', 'w': _int(img.get('width')), 'h': _int(img.get('height'))}] if iurl else []
    uv, dv = _int(e.get('uv')), _int(e.get('dv'))
    return {
        'platform': 'mbin', 'community': f'm/{mname}' + (f'@{host}' if mname else ''),
        'external_id': str(e['entryId']), 'url': url,
        'link_url': raw if raw and not re.search(r'\.(?:jpe?g|png|gif|webp)(?:\?.*)?$', raw, re.I) else '',
        'title': title_of(plain(e.get('title'))), 'body': plain(e.get('body')), 'author': _name(e.get('user')),
        'media': media, 'score': (uv - dv) if (uv or dv) else _int(e.get('favourites')),
        'comments': _int(e.get('numComments')), 'published_at': _ts(e.get('createdAt')),
        'nsfw': bool(e.get('isAdult') or mag.get('isAdult')), 'spoiler': False,
        'meta': {'instance': host, 'entry_id': e['entryId']},
    }


def fetch(locator: str, sort: str, cursor: str = '') -> Page:
    loc, _, via = parse(locator).partition(' via ')
    name, home = loc.split('@', 1)
    host = via or home
    # A remote magazine is addressed on the via-instance as name@home.
    mname = name if host == home else f'{name}@{home}'
    mag = get_json(f'https://{host}/api/magazine/name/{quote(mname, safe="@")}')
    mid = mag.get('magazineId') if isinstance(mag, dict) else None
    if not isinstance(mid, int):
        raise ValueError(f'Magazine {name}@{host} not found.')
    s, t = SORTS.get(sort, SORTS['new'])
    n = max(1, min(int(cursor), MAX_PAGE)) if cursor.isdigit() else 1
    params = {'sort': s, 'p': n, 'perPage': PER_PAGE}
    if t:
        params['time'] = t
    data = get_json(f'https://{host}/api/magazine/{mid}/entries?{urlencode(params)}')
    items = data.get('items') if isinstance(data, dict) else None
    items = items if isinstance(items, list) else []
    pg = (data.get('pagination') if isinstance(data, dict) else None) or {}
    max_page = _int(pg.get('maxPage')) or MAX_PAGE
    more = bool(items) and n < min(max_page, MAX_PAGE)
    return Page([x for x in (_entry(e, host) for e in items) if x], str(n + 1) if more else '')


def _tree(items) -> list[dict]:
    out = []
    for c in items if isinstance(items, list) else []:
        if not isinstance(c, dict) or c.get('isDeleted'):
            continue
        uv, dv = _int(c.get('uv')), _int(c.get('dv'))
        out.append({'id': str(c.get('commentId') or ''), 'author': _name(c.get('user')), 'body': plain(c.get('body')),
                    'score': (uv - dv) if (uv or dv) else _int(c.get('favourites')),
                    'published_at': _ts(c.get('createdAt')), 'children': _tree(c.get('children'))})
    return out


def comments(post: dict) -> list[dict]:
    meta = post.get('meta') or {}
    host, eid = str(meta.get('instance') or ''), meta.get('entry_id')
    if not re.fullmatch(HOST, host) or not isinstance(eid, int):
        return []
    data = get_json(f'https://{host}/api/entry/{eid}/comments?{urlencode({"sortBy": "top", "d": 6})}')
    return _tree(data.get('items') if isinstance(data, dict) else None)


ADAPTER = Adapter(key='mbin', label='Mbin / kbin', parse=parse, fetch=fetch, sorts=tuple(SORTS),
                  comments=comments, example='technology@kbin.earth, or tech@fedia.io via kbin.earth')
