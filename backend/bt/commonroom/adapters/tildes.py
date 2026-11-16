"""Tildes via RSS (no JSON API) and HTML comment scraping."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from . import Adapter, Page, get_text, https, plain, title_of

_GROUP = re.compile(r'^[a-z0-9_.]{1,40}$')
_TOPIC = re.compile(r'^/(~[a-z0-9_.]+)/([a-z0-9]{3,12})(?:/|$)')


def parse(locator: str) -> str:
    loc = (locator or '').strip()
    if loc.lower().startswith('https://'):
        p = urlsplit(loc)
        if p.hostname != 'tildes.net':
            raise ValueError('Tildes URL must be on tildes.net.')
        loc = p.path.strip('/').split('/')[0] if p.path.strip('/') else '~'
    loc = loc.lower().lstrip('/')
    if loc in ('~', ''):
        return '~'
    group = loc[1:] if loc.startswith('~') else loc
    if not _GROUP.match(group):
        raise ValueError("Tildes locator must be a group like '~comp' (or '~' for all groups).")
    return '~' + group


def _xml(data: bytes):
    if b'<!DOCTYPE' in data[:2000].upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML entities are not allowed.')
    return ET.fromstring(data)


def _local(tag: str) -> str:
    return tag.split('}')[-1]


def _ts(value) -> float:
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def fetch(locator: str, sort: str, cursor: str) -> Page:
    loc = parse(locator)
    url = 'https://tildes.net/topics.rss' if loc == '~' else f'https://tildes.net/{loc}/topics.rss'
    root = _xml(get_text(url).encode('utf-8'))
    posts = []
    for item in root.iter('item'):
        f = {}
        for ch in item:
            f.setdefault(_local(ch.tag), (ch.text or '').strip())
        page_url = https(f.get('comments') or f.get('link') or f.get('guid'))
        p = urlsplit(page_url)
        m = _TOPIC.match(p.path) if p.hostname == 'tildes.net' else None
        if not m or not f.get('title'):
            continue
        link = https(f.get('link'))
        if urlsplit(link).hostname == 'tildes.net':
            link = ''
        body = plain(f.get('description'))
        posts.append({
            'platform': 'tildes', 'community': m.group(1), 'external_id': m.group(2),
            'url': f'https://tildes.net{p.path}', 'link_url': link, 'title': title_of(plain(f['title'], 300)),
            'body': body, 'author': f.get('creator') or f.get('author') or '', 'media': [],
            'score': 0, 'comments': 0, 'published_at': _ts(f.get('pubDate')),
            'nsfw': False, 'spoiler': False, 'meta': {'group': m.group(1)},
        })
    return Page(posts, '')


def comments(post: dict) -> list[dict]:
    url = https(post.get('url'))
    if urlsplit(url).hostname != 'tildes.net':
        raise ValueError('Not a Tildes post.')
    try:
        soup = BeautifulSoup(get_text(url), 'html.parser')
        root = soup.select_one('ol.comment-tree')
        return _tree(root) if root else []
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - best-effort scrape, markup changes must not break the wall
        return []


def _own(art, selector: str):
    """First match for selector that belongs to this comment, not to a nested reply."""
    for el in art.select(selector):
        if el.find_parent('article', class_='comment') is art:
            return el
    return None


def _tree(ol) -> list[dict]:
    from datetime import datetime
    out = []
    for li in ol.find_all('li', recursive=False):
        art = li.find('article', class_='comment', recursive=False) or li.find('article', class_='comment')
        if art is None:
            continue
        text, user, when = _own(art, '.comment-text'), _own(art, '.link-user'), _own(art, 'time[datetime]')
        kids = art.find('ol', class_='comment-tree') or li.find('ol', class_='comment-tree', recursive=False)
        ts = 0.0
        if when:
            try:
                ts = datetime.fromisoformat(when['datetime'].replace('Z', '+00:00')).timestamp()
            except ValueError:
                pass
        out.append({'id': (art.get('id') or '').replace('comment-', ''),
                    'author': user.get_text(strip=True).lstrip('@') if user else '',
                    'body': plain(text.decode_contents() if text else ''), 'score': 0, 'published_at': ts,
                    'children': _tree(kids) if kids else []})
    return out


ADAPTER = Adapter(key='tildes', label='Tildes', parse=parse, fetch=fetch, sorts=('new',),
                  comments=comments, example='~comp', discoverable=False)
