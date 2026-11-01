"""Recovering a page that the plain fetch could not read.

Capture gives up when a page renders its article in JavaScript, hides it behind a cookie or bot
wall, or ships it as a scan. Every tool here is a bounded, audited way to get the same text; the
agent picks between them, and a fixed order is used when no model is configured.

Nothing here runs arbitrary code. The one tool that executes untrusted content is `render`, and
it runs the browser inside bubblewrap with the data and media directories masked.
"""
import json
import logging
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlsplit

from .. import curation
from ..config import DATA_DIR, MEDIA_DIR
from . import ai
from .extract import TEXT_LIMIT, extract_page, strip_tags

log = logging.getLogger(__name__)

RENDER_TIMEOUT_S = 45
OCR_PAGES = 10
OCR_TIMEOUT_S = 120
MIN_BODY = 200  # shorter than this is a cookie notice, not an article

# Cheap and deterministic first: these fix a good share of failures without a model.
FIRST = ('refetch', 'variants')

UAS = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
    'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
)


def _body(html: bytes, url: str) -> str:
    try:
        return extract_page(html, url)['body']
    except Exception:  # noqa: BLE001 — a tool that cannot parse simply did not work
        return ''


def _get(url: str, kinds=curation.HTML_TYPES, limit: int = 20_000_000, headers: dict | None = None):
    return curation.fetch(url, kinds=kinds, limit=limit, headers=headers, with_metadata=True)


def tool_refetch(ctx) -> str:
    """The same page with a different browser identity; some walls only stop the default one."""
    for ua in UAS:
        try:
            data, final, _ = _get(ctx['url'], headers={'User-Agent': ua, 'Accept-Language': 'en-GB,en;q=0.9'})
        except Exception:  # noqa: BLE001
            continue
        body = _body(data, final)
        if len(body) >= MIN_BODY:
            return body
    return ''


def _variants(url: str) -> list[str]:
    p = urlsplit(url)
    base = url.rstrip('/')
    out = [base + suffix for suffix in ('/amp', '/amp/', '?amp=1', '?output=1', '?print=1', '/print')]
    if p.hostname and not p.hostname.startswith('amp.'):
        out.append(p._replace(netloc='amp.' + p.netloc).geturl())
    return out


def tool_variants(ctx) -> str:
    """AMP and print views are usually server-rendered even when the article page is not."""
    for candidate in _variants(ctx['url']):
        try:
            data, final, _ = _get(candidate)
        except Exception:  # noqa: BLE001
            continue
        body = _body(data, final)
        if len(body) >= MIN_BODY:
            return body
    return ''


def tool_json_api(ctx) -> str:
    """The article as the site's own API or structured data already holds it."""
    p = urlsplit(ctx['url'])
    slug = p.path.rstrip('/').rsplit('/', 1)[-1]
    if slug and '.' not in slug:
        try:
            data, _, _ = _get(f'{p.scheme}://{p.netloc}/wp-json/wp/v2/posts?slug={quote(slug)}',
                              kinds=('application/json',), limit=5_000_000)
            rows = json.loads(data)
            if isinstance(rows, list) and rows:
                body = strip_tags(((rows[0].get('content') or {}).get('rendered') or ''), TEXT_LIMIT)
                if len(body) >= MIN_BODY:
                    return body
        except Exception:  # noqa: BLE001 — not a WordPress site, or the API is closed
            pass
    return _json_ld_body(ctx.get('html') or b'', ctx['url'])


def _json_ld_body(html: bytes, url: str) -> str:
    """articleBody from JSON-LD, which publishers ship for search engines."""
    text = html.decode('utf-8', 'replace')
    for match in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S | re.I):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            body = node.get('articleBody')
            if isinstance(body, str) and len(body) >= MIN_BODY:
                return body[:TEXT_LIMIT]
    return ''


def tool_feed_full(ctx) -> str:
    """The source feed's own full text, which many publishers ship even when the page is walled."""
    feed = ctx.get('feed_url') or ''
    if not feed:
        return ''
    try:
        data, _, _ = _get(feed, kinds=curation.FEED_TYPES, limit=5_000_000)
    except Exception:  # noqa: BLE001
        return ''
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ''
    want = ctx['url'].rstrip('/')
    for entry in root.iter():
        if entry.tag.rsplit('}', 1)[-1] not in ('item', 'entry'):
            continue
        link = ''
        for child in entry:
            tag = child.tag.rsplit('}', 1)[-1]
            if tag == 'link':
                link = (child.get('href') or child.text or '').strip()
                if link:
                    break
        if not link or link.rstrip('/') != want:
            continue
        for child in entry:
            if child.tag.rsplit('}', 1)[-1] in ('encoded', 'content'):
                body = strip_tags(''.join(child.itertext()), TEXT_LIMIT)
                if len(body) >= MIN_BODY:
                    return body
    return ''


def tool_wayback(ctx) -> str:
    """The nearest archived snapshot, for a page that is gone or refuses this address."""
    try:
        data, _, _ = _get('https://archive.org/wayback/available?url=' + quote(ctx['url'], safe=''),
                          kinds=('application/json',), limit=200_000)
        closest = (json.loads(data).get('archived_snapshots') or {}).get('closest') or {}
        target = str(closest.get('url') or '')
        if not target:
            return ''
        html, final, _ = _get(target.replace('http://', 'https://', 1))
        return _body(html, final)
    except Exception:  # noqa: BLE001
        return ''


def _chrome() -> str:
    for name in ('google-chrome', 'chromium', 'chromium-browser'):
        found = shutil.which(name)
        if found:
            return found
    return ''


def tool_render(ctx) -> str:
    """Run the page's JavaScript in a browser, inside bubblewrap with the data dirs masked."""
    chrome, bwrap = _chrome(), shutil.which('bwrap')
    if not chrome or not bwrap:
        return ''
    # The profile lives in the sandbox's own /tmp: the host filesystem is read-only in there, and
    # Chrome refuses to start without a writable one. The data and media directories are masked.
    cmd = ['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
           '--tmpfs', str(DATA_DIR), '--tmpfs', str(MEDIA_DIR), '--unshare-pid', '--die-with-parent',
           chrome, '--headless=new', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
           '--disable-crash-reporter', '--disable-extensions', '--no-first-run',
           '--user-data-dir=/tmp/chrome-profile', '--virtual-time-budget=8000', '--dump-dom', ctx['url']]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=RENDER_TIMEOUT_S, check=False)
    except (subprocess.SubprocessError, OSError):
        return ''
    if out.returncode != 0 or not out.stdout:
        return ''
    return _body(out.stdout, ctx['url'])


def tool_ocr(ctx) -> str:
    """A scanned PDF has no text layer; read the page images instead."""
    data = ctx.get('data') or b''
    if not data.startswith(b'%PDF-'):
        return ''
    if not shutil.which('tesseract') or not shutil.which('pdftoppm'):
        return ''
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 'in.pdf'
        src.write_bytes(data)
        try:
            subprocess.run(['pdftoppm', '-r', '200', '-png', '-l', str(OCR_PAGES), str(src), str(Path(tmp) / 'p')],
                           capture_output=True, timeout=OCR_TIMEOUT_S, check=False)
        except (subprocess.SubprocessError, OSError):
            return ''
        pages = []
        for image in sorted(Path(tmp).glob('p*.png')):
            try:
                out = subprocess.run(['tesseract', str(image), 'stdout'], capture_output=True,
                                     timeout=OCR_TIMEOUT_S, check=False)
            except (subprocess.SubprocessError, OSError):
                continue
            text = out.stdout.decode('utf-8', 'replace').strip()
            if text:
                pages.append(text)
    return '\n\n'.join(pages)[:TEXT_LIMIT]


TOOLS = {
    'refetch': ('Retry with a different browser identity', tool_refetch),
    'variants': ('Try the AMP or print view of the same article', tool_variants),
    'json_api': ('Read the article from the site API or its JSON-LD articleBody', tool_json_api),
    'feed_full': ('Read the full text the source feed already carries', tool_feed_full),
    'wayback': ('Read the nearest archived snapshot', tool_wayback),
    'render': ('Run the page JavaScript in a sandboxed browser', tool_render),
    'ocr': ('Read a scanned PDF with OCR', tool_ocr),
}
ORDER = ('refetch', 'variants', 'json_api', 'feed_full', 'wayback', 'render', 'ocr')


def _run(name: str, ctx, tried: list, notes: list) -> str:
    tried.append(name)
    try:
        body = TOOLS[name][1](ctx) or ''
    except Exception as e:  # noqa: BLE001 — one broken tool must not stop the others
        log.warning('rescue tool %s failed: %s', name, e)
        notes.append(f'{name}: {type(e).__name__}')
        return ''
    notes.append(f'{name}: {"recovered" if body else "nothing"}')
    return body if len(body) >= MIN_BODY else ''


def rescue(ctx: dict, reason: str = '') -> dict:
    """Try to get the article text another way. Returns {body, tool, notes}."""
    tried, notes = [], []
    for name in FIRST:
        body = _run(name, ctx, tried, notes)
        if body:
            return {'body': body, 'tool': name, 'notes': notes}
    remaining = [n for n in ORDER if n not in tried]
    if ai.enabled():
        for _ in range(3):
            pick = ai.choose_rescue_tool(ctx, reason, remaining, notes)
            if not pick:
                break
            body = _run(pick, ctx, tried, notes)
            if body:
                return {'body': body, 'tool': pick, 'notes': notes}
            remaining = [n for n in remaining if n != pick]
            if not remaining:
                break
    for name in remaining:
        body = _run(name, ctx, tried, notes)
        if body:
            return {'body': body, 'tool': name, 'notes': notes}
    return {'body': '', 'tool': '', 'notes': notes}
