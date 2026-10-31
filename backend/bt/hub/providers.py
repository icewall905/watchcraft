"""Provider registry. Each adapter turns a Source (+ curator queries) into normalized item dicts.

Feed providers read a URL and ignore queries; query providers run each query. All public
fetches go through curation.fetch (HTTPS, public IPs only, bounded). The configured SearXNG
endpoint is admin-trusted configuration and may live on the LAN."""
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

import httpx

from .. import curation
from ..db import get_settings
from . import watchers
from .extract import CVE_RE, classify, clean, extract_page, safe_image, strip_tags, ts

TYPES = [
    ('video', 'Videos', '▶'), ('news', 'News', '📰'), ('article', 'Articles', '📄'), ('web', 'Web resources', '🌐'),
    ('recipe', 'Recipes', '🍳'), ('pattern', 'Craft patterns', '🧶'), ('advisory', 'Security advisories', '🛡'),
    ('project', 'Software projects', '📦'), ('document', 'Documents & papers', '📚'),
    ('person', 'People', '👤'), ('organization', 'Organizations', '🏢'), ('domain', 'Domains', '🔗'),
]
TYPE_KEYS = {t[0] for t in TYPES}


@dataclass
class Provider:
    key: str
    label: str
    mode: str  # feed | query | url
    description: str
    fetch: Callable[[str, str], list[dict]]  # (source url, query) -> items
    capabilities: list = field(default_factory=list)
    needs_url: bool = False
    default_type: str = 'web'

    def availability(self) -> tuple[bool, str]:
        if self.key == 'web_search':
            cfg = get_settings()['search']
            if cfg['provider'] == 'searxng' and cfg['searxng_url']:
                return True, ''
            if cfg['provider'] == 'brave' and cfg['brave_api_key']:
                return True, ''
            return False, 'Configure SearXNG or Brave under Settings → Search & sources.'
        return True, ''

    def run(self, url: str, q: str = '', config: dict | None = None) -> list[dict]:
        """Fetch with the source's watcher rules when this provider supports them."""
        return self.fetch(url, q, config or {}) if self.key in watchers.CONFIGURABLE else self.fetch(url, q)

    def info(self) -> dict:
        ok, reason = self.availability()
        return {'key': self.key, 'label': self.label, 'mode': self.mode, 'needs_url': self.needs_url,
                'capabilities': self.capabilities, 'available': ok, 'reason': reason, 'description': self.description}


def _xml(data: bytes):
    if b'<!DOCTYPE' in data[:2000].upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML entities are not allowed.')
    return ET.fromstring(data)


def _local(tag: str) -> str:
    return tag.split('}')[-1]


def parse_feed(data: bytes, url: str) -> list[dict]:
    if data.lstrip().startswith(b'{'):
        feed = json.loads(data)
        if (not isinstance(feed, dict) or feed.get('version') not in ('https://jsonfeed.org/version/1', 'https://jsonfeed.org/version/1.1')
                or not isinstance(feed.get('title'), str) or not isinstance(feed.get('items'), list)):
            raise ValueError('Not a valid JSON Feed.')
        out = []
        for item in feed['items'][:50]:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                raise ValueError('Invalid JSON Feed item.')
            text_content = item.get('content_text') if isinstance(item.get('content_text'), str) else ''
            html_content = item.get('content_html') if isinstance(item.get('content_html'), str) else ''
            if not isinstance(item.get('content_text'), str) and not isinstance(item.get('content_html'), str):
                raise ValueError('JSON Feed items need text or HTML content.')
            content = text_content or strip_tags(html_content)
            link = item.get('url') or item.get('external_url') or ''
            title = clean(item.get('title') or content, 500)
            if not isinstance(link, str) or not title or not content: continue
            author = item.get('authors') or feed.get('authors') or []
            if not isinstance(author, list): author = []
            out.append({'url': urljoin(url, link), 'title': title, 'type': 'news', 'provider': 'rss',
                        'publisher': clean(feed['title'], 200), 'summary': clean(item.get('summary') or content, 1000),
                        'published_at': ts(item.get('date_published')), 'image': safe_image(item['image']) if isinstance(item.get('image'), str) else '',
                        'author': ', '.join(clean(a.get('name'), 100) for a in author[:3] if isinstance(a, dict) and a.get('name')),
                        'external_id': clean(item['id'], 500), 'meta': {'updated_at': ts(item.get('date_modified'))}})
        return out
    root = _xml(data)
    root_kind = _local(root.tag).lower()
    if root_kind not in ('rss', 'rdf', 'feed'):
        raise ValueError('Not an RSS or Atom feed.')
    if root_kind in ('rss', 'rdf') and not any(_local(c.tag).lower() == 'channel' for c in root):
        raise ValueError('RSS feed is missing its channel.')
    channel = clean(next((''.join(c.itertext()) for c in root.iter() if _local(c.tag) == 'title'), ''), 200)
    youtube = (urlsplit(url).hostname or '').endswith('youtube.com')
    out = []
    for entry in root.iter():
        if _local(entry.tag) not in ('item', 'entry'):
            continue
        f: dict = {'meta': {}, 'publisher': channel, 'provider': 'rss'}
        for c in entry:
            tag, text = _local(c.tag), ''.join(c.itertext())
            if tag == 'title': f['title'] = clean(text, 500)
            elif tag == 'link' and c.get('rel', 'alternate') == 'alternate': f['url'] = (c.get('href') or c.text or '').strip()
            elif tag in ('thumbnail', 'content') and c.get('url', '').startswith('https://'): f['image'] = safe_image(c.get('url'))
            elif tag in ('description', 'summary', 'content') and not f.get('summary'): f['summary'] = strip_tags(text)
            elif tag in ('pubDate', 'published', 'date') and not f.get('published_at'): f['published_at'] = ts(text)
            elif tag == 'updated': f['meta']['updated_at'] = ts(text)
            elif tag in ('author', 'creator'): f['author'] = clean(text, 200)
            elif tag == 'videoId': f['meta']['video_id'] = text.strip()
            elif tag == 'group':
                for g in c.iter():
                    if _local(g.tag) == 'thumbnail': f['image'] = safe_image(g.get('url', ''))
                    if _local(g.tag) == 'description' and not f.get('summary'): f['summary'] = clean(''.join(g.itertext()), 1000)
            elif tag == 'enclosure' and (c.get('type') or '').startswith('image/'): f['image'] = safe_image(c.get('url', ''))
        if not f.get('title') or not f.get('url'):
            continue
        f['url'] = urljoin(url, f['url'])
        f['type'] = 'video' if youtube or f['meta'].get('video_id') else 'news'
        out.append(f)
        if len(out) >= 50:
            break
    return out


def rss(url: str, _q: str = '') -> list[dict]:
    return parse_feed(curation.fetch(url), url)


def github_releases(url: str, _q: str = '') -> list[dict]:
    p = urlsplit(url)
    parts = p.path.strip('/').split('/')
    if p.hostname != 'github.com' or len(parts) != 2 or any(not x or not all(c.isalnum() or c in '-_.' for c in x) for x in parts):
        raise ValueError('Use https://github.com/owner/repository.')
    data = json.loads(curation.fetch(f'https://api.github.com/repos/{parts[0]}/{parts[1]}/releases?per_page=20'))
    if not isinstance(data, list):
        raise ValueError('Unexpected GitHub response.')
    repo = f'{parts[0]}/{parts[1]}'
    return [{'url': r['html_url'], 'title': f"{repo} {r.get('name') or r['tag_name']}", 'type': 'project',
             'summary': clean(r.get('body'), 1000), 'published_at': ts(r.get('published_at')), 'publisher': 'GitHub',
             'author': clean((r.get('author') or {}).get('login'), 100), 'external_id': f"gh-release:{r.get('id')}",
             'meta': {'repository': repo, 'version': r.get('tag_name', ''), 'prerelease': bool(r.get('prerelease')),
                      'security': 'security' in (r.get('body') or '').lower() or bool(CVE_RE.search(r.get('body') or ''))}}
            for r in data[:20] if r.get('html_url')]


def github_search(_url: str, q: str) -> list[dict]:
    data = json.loads(curation.fetch('https://api.github.com/search/repositories?' + urlencode({'q': q, 'sort': 'updated', 'per_page': 15})))
    return [{'url': r['html_url'], 'title': r['full_name'], 'type': 'project', 'summary': clean(r.get('description'), 1000),
             'publisher': 'GitHub', 'author': clean((r.get('owner') or {}).get('login'), 100),
             'published_at': ts(r.get('created_at')), 'external_id': f"gh-repo:{r.get('id')}",
             'meta': {'stars': r.get('stargazers_count'), 'language': r.get('language') or '', 'license': ((r.get('license') or {}).get('spdx_id') or ''),
                      'pushed_at': ts(r.get('pushed_at')), 'topics': (r.get('topics') or [])[:10]}}
            for r in (data.get('items') or [])[:15] if r.get('html_url')]


def hackernews(_url: str, q: str) -> list[dict]:
    data = json.loads(curation.fetch('https://hn.algolia.com/api/v1/search_by_date?' + urlencode({'query': q, 'tags': 'story', 'hitsPerPage': 20})))
    out = []
    for h in data.get('hits') or []:
        discussion = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        url = h.get('url') or discussion
        out.append({'url': url, 'title': clean(h.get('title'), 500), 'type': 'news', 'publisher': urlsplit(url).hostname or 'Hacker News',
                    'author': clean(h.get('author'), 100), 'published_at': h.get('created_at_i'), 'external_id': f"hn:{h.get('objectID')}",
                    'meta': {'points': h.get('points'), 'comments': h.get('num_comments'), 'discussion_url': discussion}})
    return [o for o in out if o['title']]


def arxiv(_url: str, q: str) -> list[dict]:
    root = _xml(curation.fetch('https://export.arxiv.org/api/query?' + urlencode({'search_query': f'all:{q}', 'sortBy': 'submittedDate', 'max_results': 15})))
    out = []
    for e in root.iter():
        if _local(e.tag) != 'entry':
            continue
        f = {'type': 'document', 'publisher': 'arXiv', 'meta': {'authors': []}}
        for c in e:
            tag = _local(c.tag)
            if tag == 'title': f['title'] = clean(''.join(c.itertext()), 500)
            elif tag == 'id': f['url'] = (c.text or '').strip().replace('http://', 'https://')
            elif tag == 'summary': f['summary'] = clean(''.join(c.itertext()), 1500)
            elif tag == 'published': f['published_at'] = ts(c.text)
            elif tag == 'author': f['meta']['authors'].append(clean(''.join(c.itertext()), 100))
            elif tag == 'link' and c.get('title') == 'pdf': f['meta']['pdf_url'] = (c.get('href') or '').replace('http://', 'https://')
        f['author'] = ', '.join(f['meta']['authors'][:3])
        if f.get('title') and f.get('url'):
            out.append(f)
    return out


def _severity(score) -> str:
    if score is None: return ''
    return 'critical' if score >= 9 else 'high' if score >= 7 else 'medium' if score >= 4 else 'low' if score > 0 else 'none'


def nvd(_url: str, q: str) -> list[dict]:
    cve = CVE_RE.search(q)
    params = {'cveId': cve.group(0).upper()} if cve else {'keywordSearch': q, 'resultsPerPage': 20}
    data = json.loads(curation.fetch('https://services.nvd.nist.gov/rest/json/cves/2.0?' + urlencode(params), limit=8_000_000))
    out = []
    for v in data.get('vulnerabilities') or []:
        c = v.get('cve') or {}
        cid = c.get('id', '')
        desc = next((d.get('value') for d in c.get('descriptions', []) if d.get('lang') == 'en'), '')
        score, vector = None, ''
        for key in ('cvssMetricV40', 'cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
            for metric in (c.get('metrics') or {}).get(key, []):
                if metric.get('type') == 'Primary' or score is None:
                    data_ = metric.get('cvssData') or {}
                    score, vector = data_.get('baseScore'), data_.get('vectorString', '')
            if score is not None:
                break
        products = sorted({':'.join(m.get('criteria', '').split(':')[3:5]) for conf in c.get('configurations', [])
                           for node in conf.get('nodes', []) for m in node.get('cpeMatch', []) if m.get('vulnerable')})[:20]
        out.append({'url': f'https://nvd.nist.gov/vuln/detail/{cid}', 'title': f'{cid}: {clean(desc, 160)}', 'type': 'advisory',
                    'summary': clean(desc, 2000), 'publisher': 'NVD', 'published_at': ts((c.get('published') or '') + 'Z'),
                    'external_id': cid,
                    'meta': {'cve_id': cid, 'cvss_score': score, 'cvss_vector': vector, 'severity': _severity(score),
                             'updated_at': ts((c.get('lastModified') or '') + 'Z'), 'products': products,
                             'known_exploited': bool(c.get('cisaExploitAdd')), 'status': c.get('vulnStatus', ''),
                             'references': [r.get('url') for r in c.get('references', [])[:15] if str(r.get('url', '')).startswith('https://')],
                             'source': 'NVD (authoritative score)'}})
    return out


KEV_URL = 'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json'


def cisa_kev(_url: str, _q: str = '') -> list[dict]:
    data = json.loads(curation.fetch(KEV_URL, limit=12_000_000))
    rows = sorted(data.get('vulnerabilities') or [], key=lambda r: r.get('dateAdded', ''), reverse=True)[:200]
    return [{'url': f"https://nvd.nist.gov/vuln/detail/{r['cveID']}", 'title': f"{r['cveID']}: {r.get('vendorProject', '')} {r.get('product', '')} — {r.get('vulnerabilityName', '')}",
             'type': 'advisory', 'summary': clean(r.get('shortDescription'), 2000), 'publisher': 'CISA KEV',
             'published_at': ts((r.get('dateAdded') or '') + 'T00:00:00Z'), 'external_id': r['cveID'],
             'meta': {'cve_id': r['cveID'], 'vendor': r.get('vendorProject', ''), 'product': r.get('product', ''),
                      'known_exploited': True, 'severity': 'high', 'required_action': clean(r.get('requiredAction'), 500),
                      'due_date': r.get('dueDate', ''), 'ransomware': r.get('knownRansomwareCampaignUse', ''),
                      'source': 'CISA Known Exploited Vulnerabilities (confirmed exploitation, not applicability)'}}
            for r in rows if r.get('cveID')]


def wikipedia(_url: str, q: str) -> list[dict]:
    data = json.loads(curation.fetch('https://en.wikipedia.org/w/api.php?' + urlencode(
        {'action': 'query', 'list': 'search', 'srsearch': q, 'format': 'json', 'srlimit': 8})))
    return [{'url': 'https://en.wikipedia.org/wiki/' + quote(r['title'].replace(' ', '_')), 'title': r['title'], 'type': 'web',
             'summary': strip_tags(r.get('snippet', '')), 'publisher': 'Wikipedia', 'meta': {'wordcount': r.get('wordcount')}}
            for r in (data.get('query') or {}).get('search', [])]


def rdap(_url: str, q: str) -> list[dict]:
    domain = q.strip().lower().removeprefix('https://').removeprefix('http://').split('/')[0]
    if '.' not in domain or not all(c.isalnum() or c in '-.' for c in domain):
        return []  # rdap only answers domain-shaped queries
    data = json.loads(curation.fetch(f'https://rdap.org/domain/{domain}', kinds=curation.FEED_TYPES + ('application/rdap+json',)))
    events = {e.get('eventAction'): e.get('eventDate') for e in data.get('events', [])}
    registrar = next((_vcard_name(e) for e in data.get('entities', []) if 'registrar' in e.get('roles', [])), '')
    return [{'url': f'https://rdap.org/domain/{domain}', 'title': f'{domain} — domain registration', 'type': 'domain',
             'publisher': 'RDAP', 'external_id': f'rdap:{domain}',
             'summary': f"Registrar: {registrar or 'unknown'}. Registered {events.get('registration', '?')[:10]}, expires {events.get('expiration', '?')[:10]}.",
             'meta': {'domain': domain, 'registrar': registrar, 'status': data.get('status', []),
                      'registered': events.get('registration', ''), 'expires': events.get('expiration', ''),
                      'nameservers': [n.get('ldhName', '') for n in data.get('nameservers', [])][:8]}}]


def _vcard_name(entity) -> str:
    for row in (entity.get('vcardArray') or [None, []])[1]:
        if row and row[0] == 'fn':
            return clean(row[3], 200)
    return ''


def page(url: str, _q: str = '') -> list[dict]:
    item = extract_page(curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000), curation.canonical(url))
    item['provider'] = 'page'
    return [item]


def page_watch(url: str, _q: str = '', config: dict | None = None) -> list[dict]:
    data, final, _ = curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000, with_metadata=True)
    item = watchers.page(data, final, config)
    if not item['body']:
        raise ValueError('No readable page text was extracted.')
    item['provider'] = 'page_watch'
    return [item]


def parse_listing(data: bytes, url: str, config: dict | None = None) -> list[dict]:
    return watchers.listing(data, url, config)


def html_listing(url: str, _q: str = '', config: dict | None = None) -> list[dict]:
    data, final, _ = curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000, with_metadata=True)
    items = parse_listing(data, final, config)
    if not items:
        raise ValueError('No article links were found in the listing.')
    return items


def sitemap(url: str, _q: str = '', config: dict | None = None) -> list[dict]:
    """Every same-host URL in a sitemap, following nested indexes within fixed bounds."""
    return watchers.crawl_sitemap(url, config)


def web_search(_url: str, q: str) -> list[dict]:
    cfg = get_settings()['search']
    if cfg['provider'] == 'searxng' and cfg['searxng_url']:
        base = cfg['searxng_url'].rstrip('/')
        # Trusted admin config (often LAN): no redirect following, bounded response.
        r = httpx.get(f'{base}/search', params={'q': q, 'format': 'json'}, timeout=20, follow_redirects=False,
                      headers={'User-Agent': 'WatchCraft/0.2'})
        r.raise_for_status()
        if len(r.content) > 3_000_000:
            raise ValueError('Search response too large.')
        rows = [(x.get('url'), x.get('title'), x.get('content'), x.get('publishedDate'), x.get('thumbnail') or x.get('img_src'))
                for x in r.json().get('results', [])[:20]]
    elif cfg['provider'] == 'brave' and cfg['brave_api_key']:
        data = json.loads(curation.fetch('https://api.search.brave.com/res/v1/web/search?' + urlencode({'q': q, 'count': 20}),
                                         headers={'X-Subscription-Token': cfg['brave_api_key']}))
        rows = [(x.get('url'), x.get('title'), x.get('description'), x.get('page_age'), (x.get('thumbnail') or {}).get('src'))
                for x in (data.get('web') or {}).get('results', [])[:20]]
    else:
        raise ValueError('No web search provider is configured.')
    out = []
    for url, title, snippet, date, img in rows:
        if not url or not title or not str(url).startswith(('https://', 'http://')):
            continue
        out.append({'url': url, 'title': clean(title, 500), 'summary': strip_tags(snippet or ''), 'published_at': ts(date),
                    'image': safe_image(img or ''), 'publisher': urlsplit(url).hostname or '', 'type': classify({'url': url, 'title': title, 'summary': snippet or ''})})
    return out


PROVIDERS = {p.key: p for p in [
    Provider('rss', 'RSS / Atom / JSON feed', 'feed', 'Public RSS, Atom or JSON feeds, including YouTube channel feeds.', rss,
             ['feed', 'update-monitoring'], needs_url=True, default_type='news'),
    Provider('github_releases', 'GitHub releases', 'feed', 'Releases of one public repository.', github_releases,
             ['feed', 'update-monitoring', 'structured-metadata'], needs_url=True, default_type='project'),
    Provider('github_search', 'GitHub repositories', 'query', 'Search public repositories.', github_search,
             ['keyword-search', 'structured-metadata', 'rate-limited'], default_type='project'),
    Provider('hackernews', 'Hacker News', 'query', 'Stories via the Algolia HN API.', hackernews,
             ['keyword-search', 'pagination'], default_type='news'),
    Provider('arxiv', 'arXiv papers', 'query', 'Research preprints.', arxiv,
             ['keyword-search', 'structured-metadata', 'full-text'], default_type='document'),
    Provider('nvd', 'NVD CVE database', 'query', 'Authoritative CVE records and CVSS scores.', nvd,
             ['keyword-search', 'structured-metadata', 'rate-limited'], default_type='advisory'),
    Provider('cisa_kev', 'CISA Known Exploited Vulnerabilities', 'feed', 'Confirmed in-the-wild exploitation; filtered by your keywords.', cisa_kev,
             ['feed', 'structured-metadata'], default_type='advisory'),
    Provider('wikipedia', 'Wikipedia', 'query', 'Encyclopaedic background for research.', wikipedia,
             ['keyword-search'], default_type='web'),
    Provider('web_search', 'Web search', 'query', 'SearXNG (self-hosted) or Brave Search API.', web_search,
             ['keyword-search', 'requires-configuration'], default_type='web'),
    Provider('rdap', 'Domain registration (RDAP)', 'query', 'Registrar, dates and nameservers for a domain.', rdap,
             ['structured-metadata'], default_type='domain'),
    Provider('html_listing', 'Article listing', 'feed', 'New article links on a public listing page, using editable selector rules.', html_listing,
             ['update-monitoring', 'article-links'], needs_url=True, default_type='news'),
    Provider('sitemap', 'XML sitemap', 'feed', 'New and updated URLs from a public XML sitemap, including nested indexes.', sitemap,
             ['update-monitoring', 'structured-metadata'], needs_url=True, default_type='web'),
    Provider('page', 'Web page', 'url', 'Extract one page (OpenGraph, JSON-LD recipes/articles).', page,
             ['url-extraction', 'structured-metadata'], needs_url=True, default_type='web'),
    Provider('page_watch', 'Page change watcher', 'feed', 'Track text changes on one public page, optionally limited to a content area.', page_watch,
             ['update-monitoring', 'page-changes'], needs_url=True, default_type='web'),
]}
