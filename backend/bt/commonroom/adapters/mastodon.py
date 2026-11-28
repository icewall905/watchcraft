"""Mastodon hashtag or local timelines. Locator: ``#tag@instance`` or ``public@instance``."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote, unquote, urlencode, urlsplit

from . import Adapter, Page, get_json, https, plain, title_of

HOST = r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
DEFAULT_HOST = 'mastodon.social'
_LOC = re.compile(rf'^(#[\w]{{1,100}}|public)(?:@({HOST}))?$')
LIMIT = 40


def parse(locator: str) -> str:
    s = (locator or '').strip()
    if s.startswith('https://'):
        p = urlsplit(s)
        host = (p.hostname or '').lower()
        m = re.fullmatch(r'/(?:tags|tag)/([^/]+)/?', p.path)
        if not re.fullmatch(HOST, host) or not (m or p.path.rstrip('/') in ('', '/public', '/public/local')):
            raise ValueError('Use #tag@mastodon.social, public@mastodon.social or an instance hashtag URL.')
        s = f'#{unquote(m.group(1))}@{host}' if m else f'public@{host}'
    m = _LOC.match(s.lower())
    if not m:
        raise ValueError('Use #tag@mastodon.social or public@mastodon.social.')
    return f'{m.group(1)}@{m.group(2) or DEFAULT_HOST}'


def _ts(v) -> float:
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _media(items) -> list[dict]:
    out = []
    for m in items if isinstance(items, list) else []:
        if not isinstance(m, dict):
            continue
        meta = (m.get('meta') or {}).get('original') or {}
        w, h = _int(meta.get('width')), _int(meta.get('height'))
        if m.get('type') == 'image' and https(m.get('url')):
            out.append({'kind': 'image', 'url': https(m['url']), 'poster': '', 'w': w, 'h': h})
        elif m.get('type') in ('video', 'gifv') and https(m.get('url')):
            out.append({'kind': 'video', 'url': https(m['url']), 'poster': https(m.get('preview_url')), 'w': w, 'h': h})
    return out[:10]


def _status(s: dict, instance: str, community: str = '') -> dict | None:
    if not isinstance(s, dict):
        return None
    s = s.get('reblog') if isinstance(s.get('reblog'), dict) else s
    if s.get('visibility') not in (None, 'public', 'unlisted'):
        return None
    url = https(s.get('url')) or https(s.get('uri'))
    if not url:
        return None
    text = plain(s.get('content'))
    media = _media(s.get('media_attachments'))
    if not text and not media:
        return None
    acct = s.get('account') if isinstance(s.get('account'), dict) else {}
    return {
        'platform': 'mastodon', 'community': community,
        'external_id': str(s.get('id') or ''), 'url': url, 'link_url': https((s.get('card') or {}).get('url')),
        'title': title_of(text) or title_of(plain(s.get('spoiler_text'))), 'body': text,
        'author': str(acct.get('acct') or acct.get('username') or ''), 'media': media,
        'score': _int(s.get('favourites_count')), 'comments': _int(s.get('replies_count')),
        'published_at': _ts(s.get('created_at')), 'nsfw': bool(s.get('sensitive')),
        'spoiler': bool(s.get('spoiler_text')),
        'language': str(s.get('language') or ''),
        'meta': {'instance': instance, 'status_id': str(s.get('id') or ''), 'reblogs': _int(s.get('reblogs_count'))},
    }


def _posts(data, instance: str, community: str = '') -> list[dict]:
    return [x for x in (_status(s, instance, community) for s in data) if x] if isinstance(data, list) else []


def fetch(locator: str, sort: str, cursor: str = '') -> Page:
    target, instance = parse(locator).split('@', 1)
    base = f'https://{instance}/api/v1/timelines/'
    params = {'limit': LIMIT}
    if cursor:
        if not re.fullmatch(r'\d{1,30}', cursor):
            raise ValueError('Bad cursor.')
        params['max_id'] = cursor
    if target == 'public':
        url = base + 'public?' + urlencode({**params, 'local': 'true'})
    else:
        url = base + f'tag/{quote(target[1:], safe="")}?' + urlencode(params)
    data = get_json(url)
    if not isinstance(data, list):
        raise ValueError('This Mastodon instance does not allow anonymous timelines.')
    ids = [str(s.get('id')) for s in data if isinstance(s, dict) and s.get('id')]
    return Page(_posts(data, instance, f'{target}@{instance}'), ids[-1] if len(data) >= LIMIT and ids else '')


def comments(post: dict) -> list[dict]:
    meta = post.get('meta') or {}
    instance, sid = str(meta.get('instance') or ''), str(meta.get('status_id') or '')
    if not re.fullmatch(HOST, instance) or not re.fullmatch(r'\d{1,30}', sid):
        return []
    data = get_json(f'https://{instance}/api/v1/statuses/{sid}/context')
    nodes: dict[str, dict] = {}
    order: list[tuple[str, str]] = []
    for s in (data.get('descendants') if isinstance(data, dict) else None) or []:
        if not isinstance(s, dict) or not s.get('id'):
            continue
        acct = s.get('account') if isinstance(s.get('account'), dict) else {}
        cid = str(s['id'])
        nodes[cid] = {'id': cid, 'author': str(acct.get('acct') or ''), 'body': plain(s.get('content')),
                      'score': _int(s.get('favourites_count')), 'published_at': _ts(s.get('created_at')), 'children': []}
        order.append((cid, str(s.get('in_reply_to_id') or '')))
    top = []
    for cid, parent in order:
        (nodes[parent]['children'] if parent in nodes else top).append(nodes[cid])
    return top


ADAPTER = Adapter(key='mastodon', label='Mastodon', parse=parse, fetch=fetch, sorts=('new',), search=None,
                  comments=comments, example='#selfhosted@mastodon.social')
