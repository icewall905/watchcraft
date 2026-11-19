"""Site-recipe helpers shared by the Mood Board image curator and the Common Room source expedition.

Nothing here knows about images or posts: fetching a page (plain or through the isolated browser),
paging a listing, and spotting a site's feeds, search form and next-page parameter.
"""
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .. import curation

BROWSER_HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
                   'Accept-Language': 'en-GB,en;q=0.9'}
MAX_HTML = 3_000_000
PARAM = re.compile(r'[A-Za-z0-9_\-\[\]]{1,30}')


def get_html(url: str, via: str = 'fetch') -> tuple[bytes, str]:
    """(html, final URL) by plain fetch with a browser identity, or through the isolated browser sidecar."""
    if via == 'browser':
        from . import browser
        page = browser.render(url)
        return page['html'].encode('utf-8', 'replace'), page.get('final_url') or url
    data, final, _ = curation.fetch(url, kinds=curation.HTML_TYPES, limit=MAX_HTML, headers=BROWSER_HEADERS,
                                    with_metadata=True)
    return data, final


def with_page(url: str, param: str, page: int) -> str:
    if not param or page <= 1:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param] + [(param, str(page))]
    return urlunsplit(parts._replace(query=urlencode(query)))


def page_param(soup) -> str:
    """The query parameter a listing uses for page 2, if a link shows it."""
    for a in soup.find_all('a', href=True)[:2000]:
        m = re.search(r'[?&](page|p|pg|paged)=2\b', a['href'])
        if m:
            return m.group(1)
    return ''


def search_template(soup, base: str) -> str:
    """https URL template with {q} from the page's first GET search form, or ''."""
    for form in soup.find_all('form')[:20]:
        if (form.get('method') or 'get').lower() != 'get':
            continue
        box = form.find('input', attrs={'type': re.compile('^(search|text)$', re.I)}) or \
            form.find('input', attrs={'name': re.compile('^(q|s|query|search|keywords?)$')})
        if not box or not box.get('name') or not PARAM.fullmatch(box['name']):
            continue
        action = urljoin(base, form.get('action') or base)
        if not action.startswith('https://'):
            continue
        hidden = [(i['name'], i.get('value') or '') for i in form.find_all('input', attrs={'type': 'hidden'})
                  if i.get('name') and len(i['name']) <= 30][:4]
        return action.split('?', 1)[0] + '?' + urlencode(hidden + [(box['name'], 'QQQ')]).replace('QQQ', '{q}')
    return ''


def feeds(soup, base: str) -> list[str]:
    """https feed URLs the page advertises with <link rel=alternate type=rss/atom/json>."""
    out = []
    for link in soup.find_all('link', rel=True)[:50]:
        if 'alternate' in [r.lower() for r in link.get('rel')] and \
                re.search(r'(rss|atom|feed)', link.get('type') or '', re.I):
            href = urljoin(base, link.get('href') or '')
            if href.startswith('https://') and href not in out:
                out.append(href)
    return out
