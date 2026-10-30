"""Page metadata extraction (OpenGraph, JSON-LD) and content-type classification.

Retrieved HTML is untrusted: only text and a small allow-list of metadata is kept."""
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

TEXT_LIMIT = 20_000
CVE_RE = re.compile(r'\bCVE-\d{4}-\d{4,7}\b', re.I)
PATTERN_WORDS = ('knit', 'crochet', 'sewing pattern', 'quilt', 'embroider', 'amigurumi', 'cross-stitch', 'cross stitch')
SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'nav', 'footer', 'header', 'form', 'iframe'}


def ts(value) -> float | None:
    """Parse ISO-8601 / RFC-822 dates into unix seconds."""
    if not value or not isinstance(value, str):
        return None
    from email.utils import parsedate_to_datetime
    v = value.strip()
    try:
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError:
        try:
            d = parsedate_to_datetime(v)
        except (TypeError, ValueError, IndexError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def clean(text, limit=500) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:limit]


def strip_tags(html: str, limit=1000) -> str:
    """Feed summaries often contain HTML; keep text only."""
    p = _Text()
    try:
        p.feed(html or '')
    except Exception:  # malformed markup: fall back to a regex strip
        return clean(re.sub(r'<[^>]+>', ' ', html or ''), limit)
    return clean(' '.join(p.parts), limit)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data)


class _Page(_Text):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self.ld, self._in_title, self._in_ld = {}, '', [], False, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or '').lower()
            if key and a.get('content') and key not in self.meta:
                self.meta[key] = a['content']
        elif tag == 'title':
            self._in_title = True
        elif tag == 'script' and (a.get('type') or '').lower() == 'application/ld+json':
            self._in_ld = True
            self.ld.append('')
            return
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        if tag == 'script' and self._in_ld:
            self._in_ld = False
            return
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_ld:
            self.ld[-1] += data
        else:
            super().handle_data(data)


def _ld_nodes(raw: list[str]):
    for chunk in raw:
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                if '@graph' in n:
                    stack.append(n['@graph'])
                yield n


def _types(node) -> set[str]:
    t = node.get('@type', [])
    return {str(x).lower() for x in (t if isinstance(t, list) else [t])}


def _name(v) -> str:
    if isinstance(v, list):
        return ', '.join(filter(None, (_name(x) for x in v[:3])))
    if isinstance(v, dict):
        return clean(v.get('name', ''), 200)
    return clean(v, 200)


def _img(v, base) -> str:
    if isinstance(v, list) and v:
        v = v[0]
    if isinstance(v, dict):
        v = v.get('url', '')
    return safe_image(urljoin(base, v) if isinstance(v, str) and v else '')


def safe_image(url: str) -> str:
    return url if isinstance(url, str) and url.startswith('https://') and len(url) < 2000 else ''


def _duration(v) -> str:
    """ISO-8601 duration (PT1H20M) → '1 h 20 min'."""
    m = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?', str(v or '').strip())
    if not m or not any(m.groups()):
        return ''
    d, h, mi = (int(x or 0) for x in m.groups())
    h += d * 24
    return ' '.join(filter(None, [f'{h} h' if h else '', f'{mi} min' if mi else '']))


def extract_page(html: bytes, url: str) -> dict:
    """Return ContentItem fields for a fetched HTML page."""
    p = _Page()
    try:
        p.feed(html.decode('utf-8', 'replace'))
    except Exception:
        pass
    m = p.meta
    item = {
        'url': url,
        'title': clean(m.get('og:title') or m.get('twitter:title') or p.title, 500) or url,
        'summary': clean(m.get('og:description') or m.get('description') or m.get('twitter:description'), 1000),
        'image': safe_image(urljoin(url, m.get('og:image', ''))) if m.get('og:image') else '',
        'publisher': clean(m.get('og:site_name'), 200),
        'author': clean(m.get('author') or m.get('article:author'), 200),
        'published_at': ts(m.get('article:published_time') or m.get('date') or m.get('dc.date')),
        'body': clean(' '.join(p.parts), TEXT_LIMIT),
        'meta': {},
        'type': 'web',
    }
    if (m.get('og:type') or '').lower() == 'article':
        item['type'] = 'article'
    for node in _ld_nodes(p.ld):
        t = _types(node)
        if 'recipe' in t:
            item['type'] = 'recipe'
            ing = node.get('recipeIngredient') or node.get('ingredients') or []
            steps = node.get('recipeInstructions') or []
            if isinstance(steps, str):
                steps = [steps]
            item['meta'] = {k: v for k, v in {
                'ingredients': [clean(x, 300) for x in ing if isinstance(x, str)][:80],
                'instructions': [clean(s.get('text') if isinstance(s, dict) else s, 1000) for s in steps if s][:60],
                'total_time': _duration(node.get('totalTime')),
                'prep_time': _duration(node.get('prepTime')),
                'cook_time': _duration(node.get('cookTime')),
                'yield': _name(node.get('recipeYield')),
                'cuisine': _name(node.get('recipeCuisine')),
                'category': _name(node.get('recipeCategory')),
                'diet': _name(node.get('suitableForDiet')),
            }.items() if v}
        elif t & {'article', 'newsarticle', 'blogposting', 'techarticle', 'report'}:
            if item['type'] == 'web':
                item['type'] = 'news' if 'newsarticle' in t else 'article'
        else:
            continue
        item['title'] = clean(node.get('headline') or node.get('name'), 500) or item['title']
        item['summary'] = item['summary'] or clean(node.get('description'), 1000)
        item['author'] = item['author'] or _name(node.get('author'))
        item['publisher'] = item['publisher'] or _name(node.get('publisher'))
        item['image'] = item['image'] or _img(node.get('image'), url)
        item['published_at'] = item['published_at'] or ts(node.get('datePublished'))
        if node.get('dateModified'):
            item['meta']['updated_at'] = ts(node.get('dateModified'))
        break
    item['type'] = classify(item)
    return item


def classify(item: dict) -> str:
    """Refine a provisional type from URL and text. Never downgrades a structured type."""
    t = item.get('type') or 'web'
    if t in ('recipe', 'advisory', 'project', 'video', 'document', 'person', 'organization', 'domain'):
        return t
    u = urlsplit(item.get('url', ''))
    host, path = (u.hostname or '').lower(), u.path.lower()
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if host.endswith('youtube.com') or host == 'youtu.be':
        return 'video'
    if path.endswith('.pdf') or host == 'arxiv.org':
        return 'document'
    if host == 'github.com' and len([x for x in path.split('/') if x]) == 2:
        return 'project'
    if CVE_RE.search(text):
        return 'advisory'
    if host.endswith('ravelry.com') or any(w in text for w in PATTERN_WORDS) and 'pattern' in text:
        return 'pattern'
    return t
