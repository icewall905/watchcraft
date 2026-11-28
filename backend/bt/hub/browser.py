"""Client for the isolated headless-browser sidecar (browser/server.py).

The sidecar runs Chromium in its own container whose only way out is a public-only egress proxy, so a
hostile page cannot reach the LAN. Only the board source curator, the Common Room source expedition and
recipes marked `via: browser` (board sources, Common Room web sources) use it, for sites that block plain
fetches or build their listings in JavaScript.
Disabled when BT_BROWSER_URL is unset. Returned HTML is untrusted page content.
"""
import os

import httpx

from . import moodboard_sources

TIMEOUT_S = 90


class BrowserUnavailable(ValueError):
    pass


def base() -> str:
    return os.environ.get('BT_BROWSER_URL', '').strip().rstrip('/')


def available() -> bool:
    return bool(base())


def render(url: str, wait_ms: int = 2500, scroll: int = 6) -> dict:
    """{final_url, status, title, html, images} for one public page; raises on failure."""
    if not available():
        raise BrowserUnavailable('The browser sidecar is not configured.')
    if not moodboard_sources._public_url(url):
        raise ValueError('Only public addresses can be rendered.')
    r = httpx.post(base() + '/render', json={'url': url, 'wait_ms': wait_ms, 'scroll': scroll}, timeout=TIMEOUT_S)
    if r.status_code != 200:
        try:
            detail = r.json().get('error', '')
        except ValueError:
            detail = ''
        raise ValueError(f'Browser could not render the page ({r.status_code} {str(detail)[:120]}).')
    data = r.json()
    if not isinstance(data, dict) or not isinstance(data.get('html'), str):
        raise ValueError('Browser returned an unexpected response.')
    status = int(data.get('status') or 0)
    if status >= 400:
        raise ValueError(f'Page answered {status} in the browser.')
    return data
