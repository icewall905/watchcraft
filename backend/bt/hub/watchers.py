"""Declarative website watchers: listing/page/sitemap rules, health diagnosis and repair proposals.

Rules are data, never code: CSS selectors (compiled by soupsieve) and URL path globs. Anything an
agent proposes is validated against the same schema and tested against the live page before use.
"""
import difflib
import gzip
import io
import pathlib
import pickle
import re
import statistics
import subprocess
import sys
from fnmatch import fnmatchcase
from urllib.parse import urljoin, urlsplit, urlunsplit

import soupsieve
from bs4 import BeautifulSoup, Tag

from ..urls import canonical
from .extract import clean, extract_page, ts

SELECTOR_MAX = 300
LIST_MAX = 10
LISTING_LIMIT = 50
SITEMAP_DOCS = 50  # sitemap documents fetched per check, indexes included
SITEMAP_DEPTH = 3
SITEMAP_URLS = 5000
SITEMAP_KINDS = ('application/xml', 'text/xml', 'application/gzip', 'application/x-gzip', 'application/octet-stream')
GZIP_MAX = 20_000_000

# Fields each configurable provider accepts; anything else is dropped.
SELECTORS = {'item_selector', 'link_selector', 'title_selector', 'date_selector', 'content_selector'}
FIELDS = {
    'html_listing': {'item_selector', 'link_selector', 'title_selector', 'date_selector', 'allowed_patterns',
                     'ignored_patterns', 'ignore_selectors', 'strip_query', 'auto_repair'},
    'page_watch': {'content_selector', 'ignore_selectors', 'alert_on_change', 'min_change_chars', 'auto_repair'},
    'sitemap': {'allowed_patterns', 'ignored_patterns', 'strip_query'},
}
CONFIGURABLE = set(FIELDS)
GENERIC_TITLES = {'read more', 'continue reading', 'more', 'learn more', 'read article', 'read the post', 'read post',
                  'read the full story', 'full story', 'details', 'view', 'open', 'link'}


class RuleError(ValueError):
    pass


_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'|\[[^\]]*\]')


def _check_selector(key: str, sel: str) -> None:
    """Only simple selectors: no pseudo-classes and at most three steps, so matching stays cheap."""
    try:
        soupsieve.compile(sel)
    except Exception as e:  # noqa: BLE001 — soupsieve raises several error types
        raise RuleError(f'{key}: invalid CSS selector "{sel[:60]}" ({str(e).splitlines()[0][:120]}).')
    bare = re.sub(r'\s*([>+~])\s*', r'\1', _QUOTED.sub('[]', sel))  # attribute values may contain anything
    if ':' in bare:
        raise RuleError(f'{key}: pseudo-classes such as :not or :nth-child are not supported; use tags, classes and attributes.')
    if '~' in bare:
        raise RuleError(f'{key}: the "~" sibling combinator is not supported; use "+" or ">".')
    parts = [part.strip() for part in bare.split(',')]
    if len(parts) > 4 or any(len([c for c in re.split(r'[\s>+]+', part) if c]) > 3 for part in parts):
        raise RuleError(f'{key}: use at most three steps per selector (e.g. "main .post > h2") and four alternatives.')
    # Each "any depth" (space) step costs a walk up the tree per element; one is enough, use ">" for the rest.
    if any(len(re.findall(r'\s+', part)) > 1 for part in parts):
        raise RuleError(f'{key}: use at most one space (any-depth) step per selector; use ">" for direct children, e.g. "main .post > h2".')


def normalize(provider: str, config: dict | None) -> dict:
    """Validate a watcher definition; raises RuleError with a user-facing message."""
    if provider not in FIELDS:
        if config:
            raise RuleError('This source type has no watcher rules.')
        return {}
    out = {}
    for key, value in (config or {}).items():
        if key not in FIELDS[provider] or value in (None, '', []):
            continue
        if key in SELECTORS or key == 'ignore_selectors':
            values = value if key == 'ignore_selectors' else [value]
            if not isinstance(values, list) or len(values) > LIST_MAX:
                raise RuleError(f'{key}: use at most {LIST_MAX} selectors.')
            for sel in values:
                if not isinstance(sel, str) or not sel.strip() or len(sel) > SELECTOR_MAX:
                    raise RuleError(f'{key}: each selector must be 1–{SELECTOR_MAX} characters.')
                _check_selector(key, sel.strip())
            out[key] = [v.strip() for v in values] if key == 'ignore_selectors' else value.strip()
        elif key in ('allowed_patterns', 'ignored_patterns'):
            if not isinstance(value, list) or len(value) > LIST_MAX or not all(
                    isinstance(p, str) and p.startswith('/') and 0 < len(p) <= 200 for p in value):
                raise RuleError(f'{key}: use up to {LIST_MAX} path patterns starting with "/", e.g. /blog/*.')
            out[key] = value
        elif key in ('strip_query', 'alert_on_change', 'auto_repair'):
            out[key] = bool(value)
        elif key == 'min_change_chars':
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10_000:
                raise RuleError('min_change_chars: use a whole number from 0 to 10000.')
            out[key] = value
    return out


def path_allowed(url: str, config: dict) -> bool:
    path = urlsplit(url).path or '/'
    allowed = config.get('allowed_patterns') or []
    if allowed and not any(fnmatchcase(path, p) for p in allowed):
        return False
    return not any(fnmatchcase(path, p) for p in config.get('ignored_patterns') or [])


def _canonical(url: str, config: dict) -> str:
    url = canonical(url)
    if config.get('strip_query'):
        p = urlsplit(url)
        url = urlunsplit((p.scheme, p.netloc, p.path, '', ''))
    return url


# Selector matching walks up the tree, so its cost grows with depth × elements. Real pages nest 20–40 levels.
MAX_DEPTH = 64
MAX_ELEMENTS = 50_000


def uses_selectors(config: dict) -> bool:
    return any(config.get(k) for k in (*SELECTORS, 'ignore_selectors'))


def _soup(data: bytes | str, config: dict, guard: bool = True) -> BeautifulSoup:
    soup = BeautifulSoup(data, 'html.parser')
    stack, count = [(soup, 0)], 0
    while guard and stack:  # iterative, linear in the number of elements
        node, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_ELEMENTS:
            raise ValueError(f'The page is too deeply nested or too large (over {MAX_DEPTH} levels or '
                             f'{MAX_ELEMENTS:,} elements) for selector rules; the default rules still work.')
        stack.extend((child, depth + 1) for child in node.children if isinstance(child, Tag))
    for sel in config.get('ignore_selectors') or []:
        for node in soup.select(sel):
            node.decompose()
    return soup


def _date(node) -> float | None:
    if node is None:
        return None
    for attr in ('datetime', 'content', 'title'):
        if node.get(attr):
            value = ts(node[attr])
            if value:
                return value
    return ts(clean(node.get_text(' '), 100))


# ---------- html listing

EXTRACT_SECONDS = 20
_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])


def _bounded(func, *args):
    """Run selector matching in a child process that is killed after EXTRACT_SECONDS.

    Matching cost depends on the watched page, which its owner controls; static limits on selectors and pages keep
    the usual case fast, and this hard limit keeps a hostile combination from tying up the server."""
    try:
        done = subprocess.run([sys.executable, '-m', 'bt.hub.watchers'], input=pickle.dumps((func.__name__, args)),
                              capture_output=True, timeout=EXTRACT_SECONDS, cwd=_PACKAGE_ROOT, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError(f'These rules took longer than {EXTRACT_SECONDS} seconds on this page; use simpler selectors.')
    if done.returncode != 0 or not done.stdout:
        raise ValueError('Rule extraction stopped unexpectedly.')
    ok, value = pickle.loads(done.stdout)  # our own child's output
    if not ok:
        raise ValueError(value)
    return value


def listing(data: bytes, base: str, config: dict | None = None) -> list[dict]:
    """Article links from repeated containers on one host, using declarative rules."""
    config = config or {}
    if not uses_selectors(config):
        return _extract(_soup(data, config, guard=False), base, config)  # default rules: linear on any page
    return _bounded(_listing, data, base, config)


def _listing(data: bytes, base: str, config: dict) -> list[dict]:
    return _extract(_soup(data, config), base, config)


def _accept(title: str, url: str, base: str, host, seen: set, config: dict) -> bool:
    return not (len(title) < 8 or title.lower().rstrip(' .…→»›') in GENERIC_TITLES
                or urlsplit(url).hostname != host or url == base or url in seen or not path_allowed(url, config))


def _extract(soup: BeautifulSoup, base: str, config: dict) -> list[dict]:
    """Listing extraction on an already-parsed page whose ignored areas are removed."""
    host = urlsplit(base).hostname
    items, seen = [], set()
    if not config.get('item_selector') and not config.get('link_selector'):
        # Default rules, as before rules existed: every titled same-host link inside an <article>.
        links = ((box, link) for box in soup.find_all('article') for link in box.find_all('a', href=True))
        for box, link in links:
            try:
                url = _canonical(urljoin(base, link['href']), config)
            except (ValueError, TypeError):
                continue
            title = clean(link.get_text(' '), 500)
            if not _accept(title, url, base, host, seen, config):
                continue
            seen.add(url)
            item = {'url': url, 'title': title, 'type': 'news', 'publisher': host or '', 'provider': 'html_listing'}
            published = _date(box.select_one(config['date_selector'] if config.get('date_selector') else 'time'))
            if published:
                item['published_at'] = published
            items.append(item)
            if len(items) >= LISTING_LIMIT:
                break
        return items
    # Bounded: nested matches each scan their own subtree, so only the first 1,000 containers are read.
    for box in soup.select(config.get('item_selector') or 'article', limit=1000):
        heading = box.select_one('h1, h2, h3, h4, h5, h6')
        if box.name == 'a' and box.get('href'):
            link = box
        elif config.get('link_selector'):
            link = box.select_one(config['link_selector'])
        else:  # a headline's own link beats a leading image or "read more" link
            link = (heading.select_one('a[href]') if heading else None) or box.select_one('a[href]')
        if link is None or not link.get('href'):
            continue
        try:
            url = _canonical(urljoin(base, link['href']), config)
        except (ValueError, TypeError):
            continue
        title_node = box.select_one(config['title_selector']) if config.get('title_selector') else heading
        title = clean((title_node or link).get_text(' '), 500)
        if title.lower().rstrip(' .…→»›') in GENERIC_TITLES:
            title = clean(link.get_text(' '), 500) if title_node else ''
        if not _accept(title, url, base, host, seen, config):
            continue
        seen.add(url)
        item = {'url': url, 'title': title, 'type': 'news', 'publisher': host or '', 'provider': 'html_listing'}
        published = _date(box.select_one(config['date_selector'])) if config.get('date_selector') else _date(box.select_one('time'))
        if published:
            item['published_at'] = published
        items.append(item)
        if len(items) >= LISTING_LIMIT:
            break
    return items


CANDIDATE_CONTAINERS = ['article', 'main li', 'li', '[class*="post"]', '[class*="article"]', '[class*="entry"]',
                        '[class*="story"]', '[class*="card"]', '[class*="item"]', 'h2', 'h3', 'p', 'dt', 'tr']
WEAK_CONTAINERS = {'li', 'h2', 'h3', 'p', 'dt', 'tr'}
CHROME = ['nav', 'body > header', 'body > footer', 'aside']  # page-level navigation; an article's own header stays


def suggest_listing_rules(data: bytes, base: str, current: dict | None = None) -> list[dict]:
    """Propose up to three tested listing rules, best first. Each carries its own extraction result."""
    return _bounded(_suggest, data, base, current)


def _suggest(data: bytes, base: str, current: dict | None) -> list[dict]:
    keep = {k: v for k, v in (current or {}).items() if k in ('ignore_selectors', 'strip_query', 'auto_repair')}
    keep['ignore_selectors'] = list(dict.fromkeys((keep.get('ignore_selectors') or []) + CHROME))[:LIST_MAX]
    proposals, seen = [], set()
    soup = _soup(data, keep)  # parsed once; every candidate shares the same ignored areas
    listing_path = urlsplit(base).path.rstrip('/').lower()
    for sel in CANDIDATE_CONTAINERS:
        config = {**keep, 'item_selector': sel}
        items = _extract(soup, base, config)
        if len(items) < 3:
            continue
        first = [p for p in (urlsplit(i['url']).path.strip('/').split('/')[0] for i in items) if p]
        if first:
            top = max(set(first), key=first.count)
            # A year or ID segment (/2026/…) would stop matching next year, so only names count.
            if first.count(top) >= 0.7 * len(items) and len(first) == len(items) and not top.isdigit():
                narrowed = {**config, 'allowed_patterns': [f'/{top}/*']}
                narrowed_items = _extract(soup, base, narrowed)
                if len(narrowed_items) >= 3:
                    config, items = narrowed, narrowed_items
        key = tuple(i['url'] for i in items)
        if key in seen:
            continue
        seen.add(key)
        dated = sum(1 for i in items if i.get('published_at'))
        # Articles usually live below the listing's own path (/news → /news/…); site navigation does not.
        below = sum(1 for i in items if listing_path and urlsplit(i['url']).path.lower().startswith(listing_path + '/'))
        # Many links with real titles beats few; dates and a path pattern suggest a real article list.
        score = (min(len(items), 30) + dated + (5 if config.get('allowed_patterns') else 0)
                 + (12 if below >= 0.7 * len(items) else 0) - (8 if sel in WEAK_CONTAINERS else 0))
        proposals.append({'config': config, 'count': len(items), 'sample': preview(items), 'score': score})
    proposals.sort(key=lambda p: p['score'], reverse=True)
    return proposals[:3]


# ---------- page watch

def page(data: bytes, url: str, config: dict | None = None) -> dict:
    config = config or {}
    if not config.get('content_selector') and not config.get('ignore_selectors'):
        return extract_page(data, url)
    return _bounded(_page, data, url, config)


def _page(data: bytes, url: str, config: dict) -> dict:
    soup = _soup(data, config)
    item = extract_page(str(soup).encode(), url)
    if config.get('content_selector'):
        nodes = soup.select(config['content_selector'])
        if not nodes:
            raise ValueError('The content selector matched nothing on the page.')
        text = '\n'.join(n.get_text('\n') for n in nodes)
        item['body'] = '\n\n'.join(line for raw in text.split('\n') if (line := clean(raw, 20_000)))[:20_000]
    return item


def diff(old: str, new: str, limit: int = 40) -> dict:
    """Paragraph-level changes between two observations."""
    a, b = [p for p in old.split('\n') if p.strip()], [p for p in new.split('\n') if p.strip()]
    ops, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        for text in a[i1:i2]:
            removed += len(text)
            if len(ops) < limit: ops.append({'op': 'removed', 'text': text[:500]})
        for text in b[j1:j2]:
            added += len(text)
            if len(ops) < limit: ops.append({'op': 'added', 'text': text[:500]})
    return {'ops': ops, 'added_chars': added, 'removed_chars': removed, 'truncated': len(ops) >= limit}


# ---------- sitemaps

def _xml_bytes(data: bytes) -> bytes:
    if data[:2] != b'\x1f\x8b':
        return data
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        out = f.read(GZIP_MAX + 1)
    if len(out) > GZIP_MAX:
        raise ValueError('Compressed sitemap expands beyond 20 MB.')
    return out


def crawl_sitemap(url: str, config: dict | None = None, fetch=None, max_docs: int = SITEMAP_DOCS) -> list[dict]:
    """Breadth-first traversal of a sitemap and its same-host indexes, bounded in documents, depth and URLs.

    Index children are read newest-lastmod first, so the most recent URLs are inside the caps even on sites whose
    indexes list old sitemaps first. One unreadable child is skipped; the root must be readable."""
    from .providers import _local, _xml
    config = config or {}
    from .. import curation  # the fetcher pulls in the database layer; the extraction child never needs it
    fetch = fetch or (lambda u: curation.fetch(u, kinds=SITEMAP_KINDS, limit=10_000_000, with_metadata=True))
    queue, visited, items, seen, fetched, failed = [(url, 0)], set(), [], set(), 0, 0
    host = None
    while queue and fetched < max_docs and len(items) < SITEMAP_URLS:
        doc, depth = queue.pop(0)
        if doc in visited:
            continue
        visited.add(doc)
        try:
            data, resolved, _ = fetch(doc)
            fetched += 1
            if host is None:
                host = urlsplit(resolved).hostname  # the root may redirect, e.g. to www.
            elif urlsplit(resolved).hostname != host:
                raise ValueError('Sitemap redirected to another host.')
            root = _xml(_xml_bytes(data))
            kind = _local(root.tag)
            if kind not in ('urlset', 'sitemapindex'):
                raise ValueError('Expected an XML sitemap or sitemap index.')
        except Exception:
            if depth == 0:
                raise
            failed += 1
            continue
        children = []
        for entry in root:
            fields = {_local(child.tag): (child.text or '').strip() for child in entry}
            try:
                target = canonical(fields.get('loc', ''))
            except (ValueError, TypeError):
                continue
            if urlsplit(target).hostname != host:
                continue
            if kind == 'sitemapindex':
                if _local(entry.tag) == 'sitemap' and depth + 1 <= SITEMAP_DEPTH and target not in visited:
                    children.append((ts(fields.get('lastmod')) or 0, len(children), target))
                continue
            if _local(entry.tag) != 'url':
                continue
            try:
                target = _canonical(target, config)
            except ValueError:
                continue
            if target in seen or not path_allowed(target, config):
                continue
            seen.add(target)
            # No title: the URL is the title (added when items are used), which keeps shared results small.
            items.append({'url': target, 'type': 'web', 'provider': 'sitemap', 'publisher': host or '',
                          'meta': {'updated_at': ts(fields.get('lastmod'))}})
            if len(items) >= SITEMAP_URLS:
                break
        children.sort(key=lambda c: (-c[0], c[1]))
        queue.extend((target, depth + 1) for _, _, target in children)
    if not items:
        raise ValueError('No same-host HTTPS URLs found in the sitemap' + (f' ({failed} child sitemaps failed).' if failed else '.'))
    items.sort(key=lambda item: item['meta']['updated_at'] or 0, reverse=True)
    return items


# ---------- previews and health

def preview(items: list[dict], n: int = 8) -> list[dict]:
    return [{'title': d.get('title') or d.get('url', ''), 'url': d.get('url', ''), 'published_at': d.get('published_at')
             or (d.get('meta') or {}).get('updated_at'), 'excerpt': clean(d.get('summary') or d.get('body') or '', 240)}
            for d in items[:n]]


SIGNALS = [
    ('rate_limited', re.compile(r'HTTP 429|rate.?limit|too many requests', re.I), 'The source is rate limiting requests.'),
    ('auth', re.compile(r'HTTP 40[13]|forbidden|unauthori[sz]ed|login', re.I), 'The source now refuses access (authentication or blocking).'),
    ('outage', re.compile(r'HTTP 5\d\d|timed? ?out|connection|resolve|unreachable', re.I), 'The source is unreachable or failing on its side.'),
    ('empty_extraction', re.compile(r'matched nothing|no article links|no readable|no same-host|no usable', re.I), 'The rules no longer extract anything.'),
    ('format', re.compile(r'content type|expected an xml|not well-formed|parse', re.I), 'The source returned a different format than before.'),
]


def health(checks: list, enabled: bool = True, provider: str = '') -> dict:
    """Diagnose a source from its recent checks (newest first)."""
    out = {'status': 'unknown', 'signals': [], 'checks': len(checks), 'success_rate': None, 'empty_rate': None,
           'consecutive_failures': 0, 'typical_count': None, 'trend': 'steady'}
    if not enabled:
        out['status'] = 'paused'
    if not checks:
        return out
    ok = [c for c in checks if c.status != 'failed']
    out['success_rate'] = round(len(ok) / len(checks), 2)
    out['empty_rate'] = round(sum(1 for c in ok if not c.item_count) / len(ok), 2) if ok else None
    for c in checks:
        if c.status != 'failed':
            break
        out['consecutive_failures'] += 1
    counts = [c.item_count for c in ok[1:] if c.item_count]
    if counts:
        out['typical_count'] = int(statistics.median(counts))
    signals = {}
    for c in checks[:3]:
        for key, pattern, message in SIGNALS:
            if c.error and pattern.search(c.error) and key not in signals:
                signals[key] = message
    latest = checks[0]
    if (latest.status != 'failed' and out['typical_count'] and out['typical_count'] >= 5
            and latest.item_count < 0.3 * out['typical_count'] and provider != 'page_watch'):
        signals['structure_change'] = (f'Only {latest.item_count} items extracted where {out["typical_count"]} '
                                       'are typical; the page structure may have changed.')
    if len(ok) >= 3 and all(not c.item_count for c in ok[:3]) and provider != 'page_watch':
        signals.setdefault('empty_extraction', 'The last checks extracted nothing.')
    if len(checks) >= 6:
        half = len(checks) // 2
        rate = lambda xs: sum(1 for c in xs if c.status != 'failed') / len(xs)
        newer, older = rate(checks[:half]), rate(checks[half:])
        out['trend'] = 'worsening' if newer < older - 0.25 else 'improving' if newer > older + 0.25 else 'steady'
    out['signals'] = [{'key': k, 'message': m} for k, m in signals.items()]
    if enabled:
        broken = (out['consecutive_failures'] >= 3
                  or ('empty_extraction' in signals and out['consecutive_failures'] >= 2)
                  or (len(ok) >= 3 and all(not c.item_count for c in ok[:3]) and provider != 'page_watch'))
        out['status'] = 'broken' if broken else 'degraded' if signals or out['success_rate'] < 0.7 \
            or out['trend'] == 'worsening' else 'healthy'
    return out


_CHILD_TASKS = {'_listing': _listing, '_suggest': _suggest, '_page': _page}

if __name__ == '__main__':  # child process for _bounded
    name, task_args = pickle.loads(sys.stdin.buffer.read())
    try:
        result = (True, _CHILD_TASKS[name](*task_args))
    except ValueError as exc:
        result = (False, str(exc))
    sys.stdout.buffer.write(pickle.dumps(result))
