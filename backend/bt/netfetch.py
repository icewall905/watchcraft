"""Proxy-aware HTTP GETs for the httpx-based YouTube fetches (RSS feeds, images, proxy test).

httpx/socksio always sends the target hostname to a SOCKS proxy, but some proxies cannot
resolve hostnames themselves (PIA's SOCKS5 answers "Host unreachable" for any hostname).
When a socks* proxy is in use we therefore resolve the hostname locally, connect to the
IPv4, and keep the hostname for SNI/Host so TLS and virtual hosts keep working. Redirects
are followed manually for the same reason: every hop has to be re-resolved.
"""
from __future__ import annotations

import functools
import socket
import urllib.parse as up

import httpx

from .ytdl import pinned_proxy_url, yt_proxy


def _socks_get(url: str, *, params, headers, timeout, proxy: str, follow_redirects: bool = True) -> httpx.Response:
    proxy = pinned_proxy_url(proxy)
    p = up.urlparse(url)
    try:
        ip = socket.gethostbyname(p.hostname or "")
    except OSError as e:
        raise httpx.ConnectError(f"could not resolve {p.hostname!r} locally: {e}") from e
    netloc = ip if p.port is None else f"{ip}:{p.port}"
    with httpx.Client(timeout=timeout, follow_redirects=False, proxy=proxy) as c:
        r: httpx.Response | None = None
        for _ in range(5):
            p = up.urlparse(url)
            host = p.hostname or ""
            ext = {"sni_hostname": host} if p.scheme == "https" else {}
            r = c.get(up.urlunparse(p._replace(netloc=netloc)), params=params, extensions=ext,
                      headers={"Host": host, **(headers or {})})
            params = None  # only the first request carries them
            if follow_redirects and r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                new = up.urljoin(url, r.headers["location"])
                if new == url:
                    break
                url = new
                p = up.urlparse(url)
                try:
                    ip = socket.gethostbyname(p.hostname or "")
                except OSError as e:
                    raise httpx.ConnectError(f"could not resolve {p.hostname!r} locally: {e}") from e
                netloc = ip if p.port is None else f"{ip}:{p.port}"
                continue
            break
    assert r is not None
    return r


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout=20.0, follow_redirects: bool = True, proxy: str | None = None) -> httpx.Response:
    """Blocking GET. Uses the stored throttle proxy unless `proxy` is given (proxy test)."""
    p = proxy if proxy is not None else yt_proxy()
    if p and up.urlparse(p).scheme.startswith("socks"):
        return _socks_get(url, params=params, headers=headers, timeout=timeout, proxy=p,
                          follow_redirects=follow_redirects)
    return httpx.get(url, params=params, headers=headers, timeout=timeout,
                     follow_redirects=follow_redirects, proxy=p or None)


async def aget(url: str, *, headers: dict | None = None, timeout=20.0) -> httpx.Response:
    """Async GET through the stored proxy (no redirect following: callers do their own hops)."""
    from starlette.concurrency import run_in_threadpool
    p = await run_in_threadpool(yt_proxy)  # socks pinning resolves and probes hosts: keep it off the event loop
    if p and up.urlparse(p).scheme.startswith("socks"):
        return await run_in_threadpool(functools.partial(_socks_get, url, params=None, headers=headers, timeout=timeout,
                                                         proxy=p, follow_redirects=False))
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False,
                                 proxy=p or None) as c:
        return await c.get(url)
