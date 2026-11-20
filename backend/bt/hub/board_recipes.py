"""Image-source recipes ("skills") for Mood Boards: declarative data run by this fixed engine, never code.

A recipe names a kind and its fields. The board source curator writes them (board_curator) after checking
that they return images; board runs (moodboards.collect) execute them to fetch more candidates. Every fetch
goes through curation.fetch / social_fetch (HTTPS, public DNS pinned, size bounded) and every result passes
moodboard_sources' candidate gate, exactly like the built-in sources.
"""
import re
from collections import Counter
from urllib.parse import quote_plus, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .. import curation
from . import moodboard_sources, providers, site_recipes
from .site_recipes import get_html
from .watchers import RuleError, _check_selector

KINDS = ('search_template', 'gallery', 'feed', 'subreddit', 'network_search', 'commons_category')
KID_KINDS = ('search_template', 'gallery', 'feed', 'network_search', 'commons_category')
# Platform searches a recipe can name. Keyed ones need their key in Mood Board source settings.
NETWORKS = ('bluesky', 'mastodon', 'tumblr', 'flickr', 'giphy', 'tenor')
KID_NETWORKS = ('giphy', 'tenor')  # only platforms with a strict family filter on child-friendly boards
IMAGE_ATTRS = ('src', 'data-src', 'data-original', 'data-lazy-src', 'srcset', 'data-srcset', 'href')
SKIP_IMAGE = re.compile(r'(logo|icon|avatar|sprite|badge|emoji|spinner|placeholder|pixel|blank|1x1)', re.I)
REDDIT_SORTS = (('top', 'week'), ('top', 'month'), ('top', 'year'), ('hot', ''), ('top', 'all'))


def _https(url, key='url'):
    if not isinstance(url, str) or not url.startswith('https://') or len(url) > 1000:
        raise RuleError(f'{key}: an https:// address is required.')
    return curation.canonical(url)


def _selector(recipe, key, default=''):
    value = str(recipe.get(key) or default).strip()[:200]
    if value:
        _check_selector(key, value)
    return value


def validate(kind: str, recipe: dict) -> dict:
    """Normalized copy of `recipe`; raises RuleError with a readable message."""
    if kind not in KINDS or not isinstance(recipe, dict):
        raise RuleError('Unknown recipe kind.')
    if kind == 'subreddit':
        name = str(recipe.get('name') or '').strip().removeprefix('r/')
        if not re.fullmatch(r'[A-Za-z0-9_]{2,21}', name):
            raise RuleError('name: not a subreddit name.')
        return {'name': name}
    if kind == 'feed':
        return {'url': _https(recipe.get('url'))}
    if kind == 'network_search':
        network = str(recipe.get('network') or '')
        if network not in NETWORKS:
            raise RuleError('network: unsupported platform.')
        query = str(recipe.get('query') or '').strip()[:150]
        return {'network': network, 'query': query}
    if kind == 'commons_category':
        name = str(recipe.get('name') or '').strip().removeprefix('Category:').replace('_', ' ')
        if not re.fullmatch(r'[^|#<>\[\]{}"\n]{2,200}', name):
            raise RuleError('name: not a Wikimedia Commons category.')
        return {'name': name}
    via = str(recipe.get('via') or 'fetch')
    if via not in ('fetch', 'browser'):
        raise RuleError('via: fetch or browser.')
    out = {'via': via,'item': _selector(recipe, 'item'), 'image': _selector(recipe, 'image', 'img'),
           'link': _selector(recipe, 'link')}
    attr = str(recipe.get('image_attr') or 'src')
    if attr not in IMAGE_ATTRS:
        raise RuleError('image_attr: unsupported attribute.')
    out['image_attr'] = attr
    page_param = str(recipe.get('page_param') or '')
    if page_param and not re.fullmatch(r'[A-Za-z0-9_\-\[\]]{1,30}', page_param):
        raise RuleError('page_param: not a query parameter name.')
    out['page_param'] = page_param
    if kind == 'gallery':
        out['url'] = _https(recipe.get('url'))
    else:
        template = str(recipe.get('url_template') or '')
        if '{q}' not in template:
            raise RuleError('url_template: needs a {q} placeholder for the search words.')
        _https(template.replace('{q}', 'x').replace('{page}', '1'), 'url_template')
        out['url_template'] = template[:1000]
    return out


_with_page = site_recipes.with_page


def page_url(kind, recipe, query='', page=1):
    if kind == 'search_template':
        url = recipe['url_template'].replace('{q}', quote_plus(query.strip()[:150]))
        if '{page}' in url:
            return url.replace('{page}', str(page))
        return _with_page(url, recipe.get('page_param'), page)
    return _with_page(recipe['url'], recipe.get('page_param'), page)


def _best_srcset(value):
    best, width = '', -1
    for part in str(value or '').split(','):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1 and re.fullmatch(r'\d+[wx]', bits[1]):
            w = int(bits[1][:-1]) * (1 if bits[1].endswith('w') else 1000)
        if w > width:
            best, width = bits[0], w
    return best


def _image_of(node, attr):
    if node is None:
        return ''
    if attr in ('srcset', 'data-srcset'):
        return _best_srcset(node.get(attr))
    value = node.get(attr) or ''
    if not value and node.name == 'img':  # lazy pages often keep the real address in one of these
        value = _best_srcset(node.get('srcset') or node.get('data-srcset')) or node.get('data-src') or node.get('src') or ''
    return str(value).strip()


def parse_page(data: bytes, url: str, recipe: dict, limit=30) -> list[dict]:
    """Candidates from one gallery/search page by the recipe's selectors."""
    soup = BeautifulSoup(data, 'html.parser')
    title = soup.title.get_text(' ', strip=True)[:120] if soup.title else urlsplit(url).hostname or ''
    blocks = soup.select(recipe['item']) if recipe.get('item') else [soup]
    out, seen = [], set()
    for block in blocks[:300]:
        images = block.select(recipe.get('image') or 'img') if not recipe.get('item') else \
            [block.select_one(recipe.get('image') or 'img') or (block if block.name == 'img' else None)]
        for node in images[:300]:
            src = _image_of(node, recipe.get('image_attr') or 'src')
            if not src or src.startswith('data:') or SKIP_IMAGE.search(src):
                continue
            image = urljoin(url, src)
            if not urlsplit(image).path.lower().rsplit('?', 1)[0].endswith(moodboard_sources.IMAGE_EXT) and \
                    not re.search(r'(image|img|photo|media|upload|cdn|static)', image, re.I):
                continue
            link = block.select_one(recipe['link']) if recipe.get('link') else None
            if link is None and node is not None:
                link = node.find_parent('a')
            href = urljoin(url, link.get('href')) if link is not None and link.get('href') else image
            alt = (node.get('alt') or node.get('title') or '') if node is not None else ''
            caption = alt or (link.get_text(' ', strip=True) if link is not None else '') or title
            item = moodboard_sources._candidate('site', caption, '', href.split('#', 1)[0], image, title)
            if not item or item['source_url'] in seen:
                continue
            seen.add(item['source_url'])
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def network_available(network: str, child: bool = False) -> bool:
    cfg = moodboard_sources.source_settings()
    if child and network not in KID_NETWORKS:
        return False
    return {'bluesky': bool(cfg.get('bluesky')), 'mastodon': bool(cfg.get('mastodon') and cfg.get('mastodon_instance')),
            'tumblr': bool(cfg.get('tumblr_api_key')), 'flickr': bool(cfg.get('flickr_api_key')),
            'giphy': bool(cfg.get('giphy_api_key')), 'tenor': bool(cfg.get('tenor_api_key'))}.get(network, False)


def _network(recipe, query, limit, offset, child):
    cfg = moodboard_sources.source_settings()
    network, q = recipe['network'], (recipe.get('query') or query).strip()
    if not network_available(network, child):
        raise ValueError(network + ' is not enabled.')
    ms = moodboard_sources
    if network == 'bluesky':
        return ms.bluesky_search(q, limit, offset)
    if network == 'mastodon':
        return ms.mastodon_search(q, limit, offset, cfg['mastodon_instance'])
    if network == 'tumblr':
        return ms.tumblr_search(q, limit, offset, cfg['tumblr_api_key'])
    if network == 'flickr':
        return ms.flickr_search(q, limit, offset, cfg['flickr_api_key'])
    if network == 'giphy':
        return ms.giphy_search(q, limit, offset, cfg['giphy_api_key'], rating='g' if child else 'pg-13')
    return ms.tenor_search(q, limit, offset, cfg['tenor_api_key'], contentfilter='high' if child else 'medium')


def fetch(kind: str, recipe: dict, query: str = '', limit: int = 30, offset: int = 0, child: bool = False) -> list[dict]:
    """Run one recipe. Raises on network/format failure so callers can count broken recipes."""
    page = offset // 30 + 1
    if kind == 'network_search':
        return _network(recipe, query, min(limit, 30), offset, child)
    if kind == 'commons_category':
        return moodboard_sources.commons_search(f'incategory:"{recipe["name"]}"', min(limit, 30), offset)
    if kind == 'subreddit':
        sort, period = REDDIT_SORTS[(offset // 30) % len(REDDIT_SORTS)]
        params = {'limit': '100', 'raw_json': '1', **({'t': period} if period else {})}
        return moodboard_sources.reddit_listing(
            f'https://www.reddit.com/r/{recipe["name"]}/{sort}.json?' + urlencode(params), limit)
    if kind == 'feed':
        data, final, _ = curation.fetch(recipe['url'], with_metadata=True)
        out = []
        for row in providers.parse_feed(data, final):
            item = moodboard_sources._candidate('site', row.get('title'), row.get('summary'), row.get('url'),
                                                row.get('image'), row.get('publisher') or urlsplit(final).hostname)
            if item:
                out.append(item)
        return out[:limit]
    data, final = get_html(page_url(kind, recipe, query, page), recipe.get('via') or 'fetch')
    return parse_page(data, final, recipe, limit)


# --- Proposing recipes from a page the curator found -------------------------------------------------------

def _signature(node):
    classes = [c for c in (node.get('class') or []) if re.fullmatch(r'[A-Za-z][\w-]{0,40}', c)
               and not re.search(r'\d{3,}', c)]
    return node.name + ('.' + classes[0] if classes else '')


def propose(data: bytes, url: str, query: str = '', via: str = 'fetch') -> list[tuple[str, dict]]:
    """Candidate (kind, recipe) pairs for a fetched HTML page, best first. Heuristic and cheap; the curator
    runs each and keeps only the ones that actually return images that fit."""
    soup = BeautifulSoup(data, 'html.parser')
    base = curation.canonical(url)
    out: list[tuple[str, dict]] = []
    # Repeated containers around images: the item selector of a gallery.
    counts, attrs = Counter(), Counter()
    for img in soup.find_all('img')[:600]:
        src = _image_of(img, 'src')
        if not src or src.startswith('data:') or SKIP_IMAGE.search(src):
            continue
        for key in ('data-src', 'data-original', 'data-lazy-src', 'srcset', 'data-srcset', 'src'):
            if img.get(key):
                attrs[key] += 1
                break
        parent = img.parent
        for _ in range(3):
            if parent is None or parent.name in ('body', 'html', '[document]'):
                break
            counts[_signature(parent)] += 1
            parent = parent.parent
    image_attr = attrs.most_common(1)[0][0] if attrs else 'src'
    items = [sig for sig, n in counts.most_common(6) if n >= 4 and sig.split('.')[0] not in ('a', 'span')][:2]
    gallery = [{'item': sig if '.' in sig else '', 'image': 'img', 'image_attr': image_attr, 'link': '', 'via': via}
               for sig in items]
    if not gallery:
        gallery = [{'item': '', 'image': 'img', 'image_attr': image_attr, 'link': '', 'via': via}]
    page_param = site_recipes.page_param(soup)
    # A GET search form on the site makes the best recipe: the board's own words, fresh pages every run.
    template = site_recipes.search_template(soup, base)
    if template:
        for g in gallery:
            out.append(('search_template', {**g, 'url_template': template, 'page_param': page_param}))
    for g in gallery:
        out.append(('gallery', {**g, 'url': base, 'page_param': page_param}))
    for href in site_recipes.feeds(soup, base):
        out.append(('feed', {'url': href}))
    valid = []
    for kind, recipe in out:
        try:
            valid.append((kind, validate(kind, recipe)))
        except RuleError:
            continue
    return valid


def known_network(url: str):
    """Sites with a dedicated recipe kind, recognised from their address."""
    m = re.match(r'https?://(?:www\.|old\.)?reddit\.com/r/([A-Za-z0-9_]{2,21})', url or '')
    if m:
        return 'subreddit', {'name': m.group(1)}
    return None


def describe(kind: str, recipe: dict) -> str:
    if kind == 'subreddit':
        return 'r/' + recipe['name']
    if kind == 'network_search':
        return recipe['network'].capitalize() + (' search "' + recipe['query'] + '"' if recipe.get('query') else ' search')
    if kind == 'commons_category':
        return 'Commons category ' + recipe['name']
    browser = ' (browser)' if recipe.get('via') == 'browser' else ''
    if kind == 'search_template':
        return 'site search ' + (urlsplit(recipe['url_template']).hostname or '') + browser
    return kind + ' ' + (urlsplit(recipe['url']).hostname or '') + browser


def host_of(kind: str, recipe: dict) -> str:
    if kind == 'subreddit':
        return 'reddit.com'
    if kind == 'network_search':
        return {'bluesky': 'bsky.app', 'mastodon': 'mastodon', 'tumblr': 'tumblr.com', 'flickr': 'flickr.com',
                'giphy': 'giphy.com', 'tenor': 'tenor.com'}[recipe['network']]
    if kind == 'commons_category':
        return 'commons.wikimedia.org'
    return (urlsplit(recipe.get('url') or recipe.get('url_template', '').replace('{q}', 'x')).hostname or '').lower()
