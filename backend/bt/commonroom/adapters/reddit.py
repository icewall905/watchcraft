"""Reddit adapter: OAuth JSON API when credentials are set, public RSS (new/hot only) otherwise."""
from __future__ import annotations

import html
import re
from urllib.parse import quote, urlencode, urlsplit

from .. import reddit_oauth
from ...hub import providers
from . import Adapter, Page, get_json, https, plain, title_of

NAME = re.compile(r'^[A-Za-z0-9_]{2,21}$')
SORTS = ('new', 'hot', 'top_day', 'top_week', 'top_month', 'top_year', 'top_all')
IMAGE_HOSTS = ('i.redd.it', 'i.imgur.com')
IMAGE_PATH = re.compile(r'\.(?:jpe?g|png|webp|gif)$', re.I)
API = 'https://oauth.reddit.com'


def parse(raw: str) -> str:
    text = (raw or '').strip()
    if '://' in text or text.lower().startswith(('reddit.com', 'www.reddit.com', 'old.reddit.com')):
        parts = urlsplit(text if '://' in text else 'https://' + text)
        host = (parts.hostname or '').lower()
        if host != 'reddit.com' and not host.endswith('.reddit.com'):
            raise ValueError('Use a subreddit like r/selfhosted.')
        path = [p for p in parts.path.split('/') if p]
        if len(path) < 2 or path[0].lower() != 'r':
            raise ValueError('Use a subreddit like r/selfhosted.')
        name = path[1]
    else:
        name = text.lstrip('/')
        if name.lower().startswith('r/'):
            name = name[2:]
        name = name.rstrip('/')
    if not NAME.match(name):
        raise ValueError('Use a subreddit like r/selfhosted (2-21 letters, digits or underscores).')
    return f'r/{name}'


def _sub(locator: str) -> str:
    return parse(locator)[2:]


def _listing_url(name: str, sort: str, cursor: str) -> str:
    if sort not in SORTS:
        sort = 'new'
    path, q = ('new', {}) if sort == 'new' else ('hot', {}) if sort == 'hot' else ('top', {'t': sort[4:]})
    params = {'limit': 100, 'raw_json': 1, **q}
    if cursor:
        params['after'] = cursor
    return f'{API}/r/{name}/{path}?{urlencode(params)}'


def _call(url: str):
    reddit_oauth.pace()
    try:
        return get_json(url, headers=reddit_oauth.headers())
    except ValueError as e:
        if getattr(e, 'status', None) == 401:
            reddit_oauth.drop_token()
        limited = reddit_oauth.rate_limited(e)
        if limited:
            raise limited from e
        raise


def _media(d: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    def add(kind, url, poster='', w=0, h=0):
        url = https(url)
        if url and url not in seen:
            seen.add(url)
            out.append({'kind': kind, 'url': url, 'poster': https(poster), 'w': int(w or 0), 'h': int(h or 0)})

    preview = ((d.get('preview') or {}).get('images') or [{}])[0].get('source') or {}
    rv = ((d.get('secure_media') or {}).get('reddit_video')) or ((d.get('media') or {}).get('reddit_video'))
    if rv and rv.get('fallback_url'):
        add('video', rv['fallback_url'], preview.get('url', ''), rv.get('width'), rv.get('height'))
        return out
    meta, gal = d.get('media_metadata') or {}, (d.get('gallery_data') or {}).get('items') or []
    if gal and meta:
        for it in gal:
            s = (meta.get(it.get('media_id')) or {}).get('s') or {}
            add('image', s.get('u') or s.get('gif') or '', '', s.get('x'), s.get('y'))
        if out:
            return out
    link = d.get('url_overridden_by_dest') or d.get('url') or ''
    host = (urlsplit(link).hostname or '').lower()
    if host in IMAGE_HOSTS and IMAGE_PATH.search(urlsplit(link).path):
        add('image', link, '', preview.get('width'), preview.get('height'))
    elif preview.get('url'):
        add('image', preview['url'], '', preview.get('width'), preview.get('height'))
    return out


def _post(d: dict) -> dict | None:
    if d.get('stickied') or d.get('removed_by_category') or d.get('selftext') in ('[removed]', '[deleted]'):
        return None
    src = d
    parents = d.get('crosspost_parent_list') or []
    media = _media(d) or (_media(parents[0]) if parents else [])
    name = d.get('name') or (f"t3_{d['id']}" if d.get('id') else '')
    permalink = d.get('permalink') or ''
    if not name or not permalink or not d.get('title'):
        return None
    url = 'https://www.reddit.com' + permalink
    link = ''
    if not d.get('is_self'):
        link = https(d.get('url_overridden_by_dest') or d.get('url') or '')
        if link == url or (urlsplit(link).hostname or '') in ('www.reddit.com', 'reddit.com') and '/comments/' in link:
            link = ''
        # Reddit-hosted media (i.redd.it, v.redd.it, gallery) is shown as media, not as an outbound link.
        if (urlsplit(link).hostname or '') in ('i.redd.it', 'v.redd.it'):
            link = ''
    body = plain(src.get('selftext_html') or src.get('selftext') or (parents[0].get('selftext') if parents else ''))
    return {'platform': 'reddit', 'community': 'r/' + (d.get('subreddit') or ''), 'external_id': name,
            'url': url, 'link_url': link, 'title': title_of(plain(d['title'], 400)), 'body': body,
            'author': d.get('author') or '', 'media': media, 'score': int(d.get('score') or 0),
            'comments': int(d.get('num_comments') or 0), 'published_at': float(d.get('created_utc') or 0),
            'nsfw': bool(d.get('over_18')), 'spoiler': bool(d.get('spoiler')), 'meta': {'id': d.get('id') or name[3:]}}


def _page(data) -> Page:
    d = (data or {}).get('data') or {}
    posts = []
    for child in d.get('children') or []:
        if child.get('kind') != 't3':
            continue
        p = _post(child.get('data') or {})
        if p:
            posts.append(p)
    return Page(posts, d.get('after') or '')


_ENTRY_ID = re.compile(r'(t3_[a-z0-9]+)')


def _rss_get(feed: str) -> bytes:
    return providers._reddit_get(feed)


def _rss_page(name: str, sort: str) -> Page:
    window = {'top_week': 'week'}.get(sort, 'day')
    if sort in ('new', 'hot'):
        feed = providers.reddit_feed(f'https://www.reddit.com/r/{name}/{"new" if sort == "new" else "hot"}')
    else:
        feed = providers.reddit_feed(f'https://www.reddit.com/r/{name}/top/?t={window}')
    try:
        root = providers._xml(_rss_get(feed))
    except providers.RateLimited:
        raise
    posts = []
    for entry in root.iter():
        if providers._local(entry.tag) != 'entry':
            continue
        f = {'id': '', 'title': '', 'link': '', 'content': '', 'author': '', 'published': 0.0, 'thumb': ''}
        for c in entry:
            tag = providers._local(c.tag)
            if tag == 'id':
                f['id'] = ''.join(c.itertext()).strip()
            elif tag == 'title':
                f['title'] = ''.join(c.itertext()).strip()
            elif tag == 'link':
                f['link'] = (c.get('href') or '').strip()
            elif tag == 'content':
                f['content'] = ''.join(c.itertext())
            elif tag == 'author':
                f['author'] = ''.join(''.join(n.itertext()) for n in c if providers._local(n.tag) == 'name').strip()
            elif tag in ('published', 'updated') and not f['published']:
                f['published'] = providers.ts(''.join(c.itertext())) or 0.0
            elif tag == 'thumbnail':
                f['thumb'] = c.get('url') or ''
        m = _ENTRY_ID.search(f['id'])
        url = https(f['link'])
        if not (m and url and f['title']):
            continue
        lm = providers._REDDIT_LINK.search(f['content'])
        link = https(html.unescape(lm.group(1))) if lm else ''
        if link == url:
            link = ''
        body = '' if lm else plain(f['content'])
        body = re.sub(r'submitted by\s+/u/\S+.*$', '', body, flags=re.S | re.I).strip()
        thumb = https(f['thumb'])
        author = f['author'].removeprefix('/u/')
        posts.append({'platform': 'reddit', 'community': f'r/{name}', 'external_id': m.group(1), 'url': url,
                      'link_url': link if (urlsplit(link).hostname or '') not in ('www.reddit.com', 'reddit.com') else '',
                      'title': title_of(plain(f['title'], 400)), 'body': body, 'author': author,
                      'media': [{'kind': 'image', 'url': thumb, 'poster': '', 'w': 0, 'h': 0}] if thumb else [],
                      'score': 0, 'comments': 0, 'published_at': float(f['published'] or 0),
                      'nsfw': False, 'spoiler': False, 'meta': {'id': m.group(1)[3:], 'rss': True}})
    return Page(posts, '')


def fetch(locator: str, sort: str = 'new', cursor: str = '') -> Page:
    name = _sub(locator)
    if reddit_oauth.configured():
        return _page(_call(_listing_url(name, sort, cursor)))
    if cursor or sort not in ('new', 'hot', 'top_day', 'top_week'):
        return Page()
    return _rss_page(name, sort)


def search(query: str, cursor: str = '') -> Page:
    if not reddit_oauth.configured() or not (query or '').strip():
        return Page()
    params = {'q': query.strip(), 'sort': 'new', 'limit': 100, 'raw_json': 1, 'type': 'link'}
    if cursor:
        params['after'] = cursor
    return _page(_call(f'{API}/search?{urlencode(params)}'))


def _comment(d: dict) -> dict | None:
    body = d.get('body') or ''
    if body in ('[deleted]', '[removed]') or not d.get('id'):
        return None
    return {'id': d['id'], 'author': d.get('author') or '', 'body': plain(d.get('body_html') or body),
            'score': int(d.get('score') or 0), 'published_at': float(d.get('created_utc') or 0),
            'children': _children(d.get('replies'))}


def _children(listing) -> list[dict]:
    if not isinstance(listing, dict):
        return []
    out = []
    for c in (listing.get('data') or {}).get('children') or []:
        if c.get('kind') != 't1':
            continue
        n = _comment(c.get('data') or {})
        if n:
            out.append(n)
    return out


def comments(post: dict) -> list[dict]:
    pid = str(post.get('external_id') or (post.get('meta') or {}).get('id') or '').removeprefix('t3_')
    if not re.fullmatch(r'[a-z0-9]{1,12}', pid):
        return []
    qs = urlencode({'limit': 50, 'depth': 6, 'sort': 'top', 'raw_json': 1})
    try:
        if reddit_oauth.configured():
            data = _call(f'{API}/comments/{quote(pid)}?{qs}')
        else:
            data = get_json(f'https://www.reddit.com/comments/{quote(pid)}.json?{qs}')
    except ValueError:
        if reddit_oauth.configured():
            raise
        return []  # anonymous reads are best effort; 429 and friends mean no comments this time
    if not isinstance(data, list) or len(data) < 2:
        return []
    return _children(data[1])


ADAPTER = Adapter(key='reddit', label='Reddit', parse=parse, fetch=fetch, sorts=SORTS, search=search,
                  comments=comments, example='r/selfhosted')
