"""Bluesky author feeds and custom feeds. Locator: ``@handle`` or ``at://did/app.bsky.feed.generator/rkey``."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from . import Adapter, Page, get_json, https, plain, title_of

API = 'https://public.api.bsky.app/xrpc/'
HANDLE = r'(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
ACTOR = rf'(?:did:plc:[a-z2-7]{{24}}|did:web:[a-z0-9.-]{{3,200}}|{HANDLE})'
_FEED = re.compile(rf'^at://({ACTOR})/app\.bsky\.feed\.generator/([A-Za-z0-9._~-]{{1,64}})$')
_AUTHOR = re.compile(rf'^@?({ACTOR})$')
NSFW_LABELS = ('porn', 'sexual', 'nudity', 'graphic-media')
LIMIT = 50


def parse(locator: str) -> str:
    s = (locator or '').strip()
    if s.startswith('https://'):
        p = urlsplit(s)
        parts = [x for x in p.path.split('/') if x]
        if (p.hostname or '').lower() != 'bsky.app' or len(parts) < 2 or parts[0] != 'profile':
            raise ValueError('Use @handle, a bsky.app profile/feed URL or an at:// feed URI.')
        s = f'at://{parts[1]}/app.bsky.feed.generator/{parts[3]}' if len(parts) >= 4 and parts[2] == 'feed' else '@' + parts[1]
    head, sep, tail = s.partition('/app.bsky.')
    s = head.lower() + sep + tail if s.startswith('at://') else s.lower()
    m = _FEED.match(s)
    if m:
        return f'at://{m.group(1).lower()}/app.bsky.feed.generator/{m.group(2)}'
    m = _AUTHOR.match(s)
    if m:
        return '@' + m.group(1).lower()
    raise ValueError('Use @handle.bsky.social, a bsky.app profile/feed URL or an at:// feed URI.')


def _ts(v) -> float:
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _embed_parts(embed: dict) -> tuple[list[dict], str]:
    """(media, outbound link) from a post view embed, including record-with-media."""
    embed = embed if isinstance(embed, dict) else {}
    if isinstance(embed.get('media'), dict):
        embed = embed['media']
    kind = str(embed.get('$type') or '')
    media, link = [], ''
    if kind.endswith('images#view'):
        for i in embed.get('images') or []:
            u = https((i or {}).get('fullsize') or (i or {}).get('thumb'))
            ar = (i or {}).get('aspectRatio') or {}
            if u:
                media.append({'kind': 'image', 'url': u, 'poster': '', 'w': _int(ar.get('width')), 'h': _int(ar.get('height'))})
    elif kind.endswith('video#view'):
        thumb = https(embed.get('thumbnail'))
        ar = embed.get('aspectRatio') or {}
        # The HLS playlist is the playable source; the thumbnail is the poster.
        src = https(embed.get('playlist'))
        if src:
            media.append({'kind': 'video', 'url': src, 'poster': thumb, 'w': _int(ar.get('width')), 'h': _int(ar.get('height'))})
        elif thumb:
            media.append({'kind': 'image', 'url': thumb, 'poster': '', 'w': 0, 'h': 0})
    elif kind.endswith('external#view'):
        ext = embed.get('external') or {}
        link = https(ext.get('uri'))
        thumb = https(ext.get('thumb'))
        if thumb:
            media.append({'kind': 'image', 'url': thumb, 'poster': '', 'w': 0, 'h': 0})
    return media, link


def _url(post: dict) -> str:
    handle = (post.get('author') or {}).get('handle')
    rkey = str(post.get('uri') or '').rsplit('/', 1)[-1]
    return f'https://bsky.app/profile/{handle}/post/{rkey}' if handle and rkey else ''


def _post(row: dict, community: str = '') -> dict | None:
    post = (row or {}).get('post') if isinstance(row, dict) else None
    if not isinstance(post, dict) or not post.get('uri'):
        return None
    url = _url(post)
    if not url:
        return None
    rec = post.get('record') if isinstance(post.get('record'), dict) else {}
    text = plain(rec.get('text'))
    media, link = _embed_parts(post.get('embed'))
    if not text and not media:
        return None
    labels = {str((l or {}).get('val')) for l in post.get('labels') or []}
    author = post.get('author') or {}
    return {
        'platform': 'bluesky', 'community': community, 'external_id': str(post['uri']), 'url': url,
        'link_url': link, 'title': title_of(text), 'body': text, 'author': str(author.get('handle') or ''),
        'media': media, 'score': _int(post.get('likeCount')), 'comments': _int(post.get('replyCount')),
        'published_at': _ts(rec.get('createdAt') or post.get('indexedAt')),
        'nsfw': bool(labels & set(NSFW_LABELS)), 'spoiler': False,
        'language': str(((rec.get('langs') or [''])[:1] or [''])[0] or ''),
        'meta': {'uri': str(post['uri']), 'cid': str(post.get('cid') or ''), 'reposts': _int(post.get('repostCount'))},
    }


def _xrpc(method: str, **params) -> dict:
    data = get_json(API + method + '?' + urlencode({k: v for k, v in params.items() if v not in ('', None)}))
    if not isinstance(data, dict):
        raise ValueError('Unexpected Bluesky response.')
    return data


def fetch(locator: str, sort: str, cursor: str = '') -> Page:
    loc = parse(locator)
    if loc.startswith('@'):
        data = _xrpc('app.bsky.feed.getAuthorFeed', actor=loc[1:], limit=LIMIT, cursor=cursor, filter='posts_no_replies')
    else:
        actor, rkey = _FEED.match(loc).groups()
        if not actor.startswith('did:'):
            did = _xrpc('app.bsky.actor.getProfile', actor=actor).get('did')
            if not isinstance(did, str) or not did.startswith('did:'):
                raise ValueError('Bluesky account not found.')
            actor = did
        data = _xrpc('app.bsky.feed.getFeed', feed=f'at://{actor}/app.bsky.feed.generator/{rkey}', limit=LIMIT, cursor=cursor)
    rows = data.get('feed') if isinstance(data.get('feed'), list) else []
    posts = []
    for r in rows:
        # Skip reposts so each post appears under its own author.
        if isinstance(r, dict) and isinstance(r.get('reason'), dict) and 'Repost' in str(r['reason'].get('$type')):
            continue
        p = _post(r, loc if loc.startswith('@') else '')
        if p:
            posts.append(p)
    nxt = data.get('cursor')
    return Page(posts, nxt if isinstance(nxt, str) and rows and nxt != cursor else '')


def _replies(items, depth: int = 0) -> list[dict]:
    out = []
    for t in items if isinstance(items, list) and depth < 8 else []:
        post = (t or {}).get('post') if isinstance(t, dict) else None
        if not isinstance(post, dict) or not post.get('uri'):
            continue
        rec = post.get('record') if isinstance(post.get('record'), dict) else {}
        out.append({'id': str(post['uri']), 'author': str((post.get('author') or {}).get('handle') or ''),
                    'body': plain(rec.get('text')), 'score': _int(post.get('likeCount')),
                    'published_at': _ts(rec.get('createdAt') or post.get('indexedAt')),
                    'children': _replies(t.get('replies'), depth + 1)})
    return out


def comments(post: dict) -> list[dict]:
    uri = str((post.get('meta') or {}).get('uri') or post.get('external_id') or '')
    if not re.fullmatch(rf'at://{ACTOR}/app\.bsky\.feed\.post/[A-Za-z0-9]{{1,32}}', uri):
        return []
    data = _xrpc('app.bsky.feed.getPostThread', uri=uri, depth=6, parentHeight=0)
    thread = data.get('thread') if isinstance(data.get('thread'), dict) else {}
    return _replies(thread.get('replies'))


ADAPTER = Adapter(key='bluesky', label='Bluesky', parse=parse, fetch=fetch, sorts=('new',), search=None,
                  comments=comments, example='@bsky.app')
