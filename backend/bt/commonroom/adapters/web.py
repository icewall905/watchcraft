"""Article listings scraped from a web page by a declarative recipe (data, never code).

Locator: compact JSON ``{"url": ..., "via": "fetch"|"browser", "item": css, "link": css, "title": css,
"summary": css, "page_param": name}``; a site search uses ``url_template`` with ``{q}`` plus a fixed
``q``. A bare https URL is accepted and gets an empty recipe (heuristic item detection). Pages go
through the SSRF-safe fetch or the isolated browser sidecar (``hub.site_recipes.get_html``).

``propose`` turns a fetched page into candidate recipes; the source expedition keeps only the ones
whose sample posts the judge says fit."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup

from ... import curation
from ...hub import site_recipes
from ...hub.watchers import RuleError, _check_selector
from . import Adapter, Page, plain, title_of

MAX_PAGES = 10
MIN_TITLE = 15
SELECTORS = ('item', 'link', 'title', 'summary')
SKIP_LINK = re.compile(r'(/tag/|/tags/|/category/|/categories/|/author/|/authors/|/user/|/login|/signup|/register|'
                       r'/search|/about|/contact|/privacy|/terms|#)', re.I)


def _https(url: str, key: str) -> str:
    if not isinstance(url, str) or not url.startswith('https://') or len(url) > 1000:
        raise ValueError(f'{key}: an https:// address is required.')
    parts = urlsplit(url)
    if parts.username or parts.password or not parts.hostname:
        raise ValueError(f'{key}: use a plain https:// address.')
    return url


def recipe_of(raw) -> dict:
    """Normalized recipe from a locator string or dict; raises ValueError."""
    if isinstance(raw, str):
        text = raw.strip()
        raw = json.loads(text) if text.startswith('{') else {'url': text}
    if not isinstance(raw, dict):
        raise ValueError('Not a web recipe.')
    out: dict = {}
    if raw.get('url_template'):
        template = str(raw['url_template'])[:1000]
        if '{q}' not in template:
            raise ValueError('url_template: needs {q}.')
        _https(template.replace('{q}', 'x'), 'url_template')
        q = str(raw.get('q') or '').strip()[:150]
        if not q:
            raise ValueError('q: a site search needs its words.')
        out.update(url_template=template, q=q)
    else:
        out['url'] = curation.canonical(_https(str(raw.get('url') or ''), 'url'))
    via = str(raw.get('via') or 'fetch')
    if via not in ('fetch', 'browser'):
        raise ValueError('via: fetch or browser.')
    out['via'] = via
    for key in SELECTORS:
        value = str(raw.get(key) or '').strip()[:200]
        if value:
            try:
                _check_selector(key, value)
            except RuleError as e:
                raise ValueError(str(e)) from None
            out[key] = value
    param = str(raw.get('page_param') or '')
    if param:
        if not site_recipes.PARAM.fullmatch(param):
            raise ValueError('page_param: not a query parameter name.')
        out['page_param'] = param
    return out


def parse(locator: str) -> str:
    try:
        return json.dumps(recipe_of(locator), sort_keys=True, separators=(',', ':'))
    except json.JSONDecodeError:
        raise ValueError('A web source is an https:// listing page.') from None


def host(locator: str) -> str:
    r = recipe_of(locator)
    return urlsplit(r.get('url') or r.get('url_template', '')).hostname or ''


def page_url(recipe: dict, page: int = 1) -> str:
    url = recipe['url_template'].replace('{q}', quote_plus(recipe['q'])) if recipe.get('url_template') else recipe['url']
    return site_recipes.with_page(url, recipe.get('page_param', ''), page)


def _ts(node) -> float:
    t = node.find('time') if node is not None else None
    value = (t.get('datetime') or '') if t is not None else ''
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else 0.0
    except ValueError:
        return 0.0


def _best_link(node, base: str, selector: str = ''):
    if selector:
        a = node.select_one(selector)
        return a if a is not None and a.get('href') else None
    best, size = None, 0
    for a in node.find_all('a', href=True)[:40]:
        text = a.get_text(' ', strip=True)
        if len(text) > size and not SKIP_LINK.search(a['href']):
            best, size = a, len(text)
    return best


def _items(soup, recipe: dict):
    if recipe.get('item'):
        return soup.select(recipe['item'])[:100]
    found = soup.find_all('article')[:100]
    if len(found) >= 3:
        return found
    sig = (_candidates(soup) or [''])[0]
    return soup.select(sig)[:100] if sig else []


def posts_of(data: bytes, final: str, recipe: dict) -> list[dict]:
    soup = BeautifulSoup(data, 'html.parser')
    site = urlsplit(final).hostname or ''
    out, seen = [], set()
    for node in _items(soup, recipe):
        a = _best_link(node, final, recipe.get('link', ''))
        if a is None:
            continue
        url = urljoin(final, a['href']).split('#', 1)[0]
        if not url.startswith('https://') or url in seen:
            continue
        head = node.select_one(recipe['title']) if recipe.get('title') else None
        title = plain((head or a).get_text(' ', strip=True), 400)
        if len(title) < MIN_TITLE:
            continue
        summ = node.select_one(recipe['summary']) if recipe.get('summary') else node.find('p')
        body = plain(summ.get_text(' ', strip=True)) if summ is not None else ''
        if body == title:
            body = ''
        img = node.find('img')
        image = urljoin(final, img.get('src') or '') if img is not None and img.get('src') else ''
        seen.add(url)
        out.append({'platform': 'web', 'community': site, 'external_id': url[:500], 'url': url, 'link_url': '',
                    'title': title_of(title), 'body': body, 'author': '',
                    'media': [{'kind': 'image', 'url': image, 'poster': '', 'w': 0, 'h': 0}]
                    if image.startswith('https://') else [],
                    'score': 0, 'comments': 0, 'published_at': _ts(node), 'nsfw': False, 'spoiler': False,
                    'meta': {'site': site}})
    return out


def fetch(locator: str, sort: str = 'new', cursor: str = '') -> Page:
    recipe = recipe_of(locator)
    try:
        page = max(1, int(cursor or 1))
    except ValueError:
        raise ValueError('Invalid web cursor.') from None
    if page > 1 and not recipe.get('page_param'):
        return Page()
    data, final = site_recipes.get_html(page_url(recipe, page), recipe['via'])
    posts = posts_of(data, final, recipe)
    more = bool(posts) and bool(recipe.get('page_param')) and page < MAX_PAGES
    return Page(posts, str(page + 1) if more else '')


# --- Proposing recipes from a page the expedition found ----------------------------------------------------

def _signature(node) -> str:
    classes = [c for c in (node.get('class') or []) if re.fullmatch(r'[A-Za-z][\w-]{0,40}', c)
               and not re.search(r'\d{3,}', c)]
    return node.name + ('.' + classes[0] if classes else '')


def _candidates(soup) -> list[str]:
    """Selectors of repeated containers around long-titled links: the items of an article listing."""
    counts = Counter()
    for a in soup.find_all('a', href=True)[:1500]:
        if len(a.get_text(' ', strip=True)) < 25 or SKIP_LINK.search(a['href']):
            continue
        parent = a.parent
        for _ in range(3):
            if parent is None or parent.name in ('body', 'html', '[document]', 'main'):
                break
            counts[_signature(parent)] += 1
            parent = parent.parent
    return [sig for sig, n in counts.most_common(8) if n >= 5 and '.' in sig
            and sig.split('.')[0] not in ('a', 'span', 'nav', 'ul', 'header', 'footer')][:3]


def propose(data: bytes, url: str, query: str = '', via: str = 'fetch') -> list[tuple[str, str]]:
    """Candidate (platform, locator) pairs for a fetched page, best first: its feeds, then listing
    recipes (site search with ``query`` first, then the page itself)."""
    soup = BeautifulSoup(data, 'html.parser')
    base = curation.canonical(url)
    out: list[tuple[str, str]] = [('rss', f) for f in site_recipes.feeds(soup, base)]
    param = site_recipes.page_param(soup)
    items = _candidates(soup) or ['']
    template = site_recipes.search_template(soup, base) if query else ''
    recipes = []
    if template:
        recipes += [{'url_template': template, 'q': query, 'item': i, 'via': via, 'page_param': param} for i in items[:1]]
    recipes += [{'url': base, 'item': i, 'via': via, 'page_param': param} for i in items]
    for r in recipes:
        try:
            out.append(('web', parse(json.dumps(r))))
        except ValueError:
            continue
    return out


ADAPTER = Adapter(key='web', label='Website', parse=parse, fetch=fetch, sorts=('new',),
                  example='https://example.com/blog', discoverable=False)
