"""Render one public page in headless Chromium and report its HTML and images.

POST /render {"url": "https://…", "wait_ms": 2500, "scroll": 6}
  -> {"final_url", "status", "title", "html", "images": [{"src", "alt", "href", "width", "height"}]}
GET /health -> {"ok": true}

Runs in its own container with no data and no secrets. Every browser request is forced through
EGRESS_PROXY (public internet only, enforced by the proxy on resolved addresses); WebRTC and QUIC
cannot bypass it. A fresh browser is launched per request, so pages never share state.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

PROXY = os.environ.get('EGRESS_PROXY', '')
MAX_HTML = 3_000_000
MAX_BODY = 8_192
SLOTS = threading.BoundedSemaphore(int(os.environ.get('BROWSER_SLOTS', '2')))
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/131.0.0.0 Safari/537.36')
ARGS = ['--proxy-bypass-list=<-loopback>', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--webrtc-ip-handling-policy=disable_non_proxied_udp', '--disable-quic', '--disable-background-networking',
        '--disable-sync', '--no-first-run', '--disable-dev-shm-usage']
IMAGES_JS = '''() => [...document.images].slice(0, 800).map(i => ({
  src: i.currentSrc || i.src || i.getAttribute('data-src') || '', alt: (i.alt || i.title || '').slice(0, 300),
  href: (i.closest('a') && i.closest('a').href) || '', width: i.naturalWidth || 0, height: i.naturalHeight || 0}))'''


def render(url: str, wait_ms: int = 2500, scroll: int = 6) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=ARGS, proxy={'server': PROXY})
        try:
            context = browser.new_context(user_agent=UA, accept_downloads=False, service_workers='block',
                                          viewport={'width': 1366, 'height': 900}, locale='en-GB')
            # Only web requests leave the page; media and fonts are skipped to save bandwidth.
            context.route('**/*', lambda route: route.abort() if (
                urlsplit(route.request.url).scheme not in ('http', 'https', 'data', 'blob')
                or route.request.resource_type in ('media', 'font')) else route.continue_())
            page = context.new_page()
            page.set_default_timeout(25_000)
            response = page.goto(url, wait_until='domcontentloaded')
            try:
                page.wait_for_load_state('networkidle', timeout=wait_ms)
            except Exception:  # noqa: BLE001 — busy pages never go idle; keep what has loaded
                pass
            for _ in range(scroll):  # lazy galleries load as you scroll
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(400)
            html = page.content()
            return {'final_url': page.url, 'status': response.status if response else 0, 'title': page.title()[:300],
                    'html': html[:MAX_HTML], 'images': page.evaluate(IMAGES_JS)}
        finally:
            browser.close()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == '/health':
            return self._send(200, {'ok': bool(PROXY)})
        self._send(404, {'error': 'not found'})

    def do_POST(self):  # noqa: N802
        if self.path != '/render':
            return self._send(404, {'error': 'not found'})
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length > MAX_BODY:
                raise ValueError('request too large')
            req = json.loads(self.rfile.read(length) or b'{}')
            url = str(req.get('url') or '')
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname or len(url) > 2000 or parts.username:
                raise ValueError('a public http(s) URL is required')
            wait_ms = max(0, min(int(req.get('wait_ms', 2500)), 8000))
            scroll = max(0, min(int(req.get('scroll', 6)), 12))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._send(400, {'error': str(exc)[:200]})
        if not PROXY:
            return self._send(503, {'error': 'egress proxy not configured'})
        if not SLOTS.acquire(timeout=60):
            return self._send(429, {'error': 'browser busy'})
        try:
            self._send(200, render(url, wait_ms, scroll))
        except Exception as exc:  # noqa: BLE001
            self._send(502, {'error': f'{type(exc).__name__}: {str(exc).splitlines()[0][:200]}'})
        finally:
            SLOTS.release()

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args), flush=True)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 9300), Handler).serve_forever()
