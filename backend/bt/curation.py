"""Hardened public fetching shared by Hub providers, plus the read-only v1 Discovery table.

v1 curator logic moved to bt.hub; Discovery is kept so v2 migration and rollback work."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urlunsplit, urljoin

from sqlmodel import Field, SQLModel
from .hub.models import Curator  # noqa: F401  (re-export for older imports)

class Discovery(SQLModel, table=True):
    """v1 table (read-only since schema v2)."""
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    curator_id: int = Field(index=True)
    url: str
    title: str
    provider: str
    discovered_at: float = Field(default_factory=time.time)
    saved: bool = False

def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Use a public HTTPS URL on port 443.')
    return urlunsplit(('https', p.netloc.lower(), p.path or '/', p.query, ''))

FEED_TYPES = ('application/atom+xml', 'application/rss+xml', 'application/xml', 'text/xml', 'application/json')
HTML_TYPES = ('text/html', 'application/xhtml+xml')

def fetch(url, kinds=FEED_TYPES, limit=2_000_000, headers=None):
    """Pin validated public DNS to the actual socket; validate every redirect."""
    origin = urlsplit(url).hostname
    for _ in range(4):
        url = canonical(url)
        p = urlsplit(url)
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Source must resolve exclusively to public addresses.')
        conn = http.client.HTTPSConnection(p.hostname, timeout=15, context=ssl.create_default_context())
        # HTTPSConnection wraps this pinned socket with the original hostname for TLS.
        conn._create_connection = lambda *args, **kwargs: socket.create_connection((addresses[0][4][0], 443), timeout=15)
        try:
            conn.request('GET', (p.path or '/') + ('?' + p.query if p.query else ''), headers={'User-Agent': 'WatchCraft/0.2 (+https://github.com/icewall905/watchcraft)', 'Accept': ', '.join(kinds), **((headers or {}) if p.hostname == origin else {})})  # credentials never follow a cross-host redirect
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise ValueError(f'Source returned HTTP {response.status}.')
            kind = response.getheader('Content-Type', '').split(';')[0].lower()
            if kind not in kinds:
                raise ValueError(f'Source returned unsupported content type {kind or "unknown"}.')
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f'Source exceeds {limit // 1_000_000} MB limit.')
            return data
        finally:
            conn.close()
    raise ValueError('Too many redirects.')

