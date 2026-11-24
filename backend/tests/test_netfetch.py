"""netfetch: proxy-aware GETs (image proxy path)."""
import asyncio
import os
import tempfile
import time

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='bt-netfetch-test-'))

import httpx

from bt import netfetch

SOCKS = 'socks5://proxy.example:1080'


def test_async_get_through_a_socks_proxy_uses_the_socks_path_without_following_redirects(monkeypatch):
    seen = {}

    def socks_get(url, *, params, headers, timeout, proxy, follow_redirects=True):
        seen.update(url=url, proxy=proxy, follow_redirects=follow_redirects, headers=headers)
        return httpx.Response(200, content=b'img')

    monkeypatch.setattr(netfetch, 'yt_proxy', lambda: SOCKS)
    monkeypatch.setattr(netfetch, '_socks_get', socks_get)
    r = asyncio.run(netfetch.aget('https://i.ytimg.com/vi/x/hq.jpg', headers={'User-Agent': 'ua'}))
    assert r.content == b'img'
    assert seen == {'url': 'https://i.ytimg.com/vi/x/hq.jpg', 'proxy': SOCKS, 'follow_redirects': False,
                    'headers': {'User-Agent': 'ua'}}


def test_slow_socks_pinning_does_not_block_the_event_loop(monkeypatch):
    def slow_proxy():
        time.sleep(0.5)  # DNS + probes of the proxy hosts
        return SOCKS

    monkeypatch.setattr(netfetch, 'yt_proxy', slow_proxy)
    monkeypatch.setattr(netfetch, '_socks_get', lambda url, **k: httpx.Response(200))

    async def main():
        lag = 0.0

        async def ticker():
            nonlocal lag
            for _ in range(20):
                t = time.perf_counter()
                await asyncio.sleep(0.02)
                lag = max(lag, time.perf_counter() - t - 0.02)

        await asyncio.gather(netfetch.aget('https://i.ytimg.com/x.jpg'), ticker())
        return lag

    assert asyncio.run(main()) < 0.2


def test_socks_get_can_leave_redirects_to_the_caller(monkeypatch):
    hits = []

    def handler(request):
        hits.append(request.headers['host'])
        if request.headers['host'] == 'a.example':
            return httpx.Response(302, headers={'location': 'https://b.example/x'})
        return httpx.Response(200, content=b'b')

    real_client = httpx.Client
    monkeypatch.setattr(netfetch.httpx, 'Client',
                        lambda **k: real_client(transport=httpx.MockTransport(handler), timeout=k.get('timeout')))
    monkeypatch.setattr(netfetch, 'pinned_proxy_url', lambda p: p)
    monkeypatch.setattr(netfetch.socket, 'gethostbyname', lambda h: '203.0.113.7')
    kw = dict(params=None, headers=None, timeout=5, proxy=SOCKS)
    assert netfetch._socks_get('https://a.example/x', follow_redirects=False, **kw).status_code == 302
    assert hits == ['a.example']
    assert netfetch._socks_get('https://a.example/x', **kw).content == b'b'
    assert hits == ['a.example', 'a.example', 'b.example']


def test_image_fetch_treats_socket_errors_as_a_miss(monkeypatch):
    from bt import extras

    async def reset(url, **k):
        raise ConnectionResetError('proxy dropped the connection')

    monkeypatch.setattr(netfetch, 'aget', reset)
    assert asyncio.run(extras._fetch_image('https://i.ytimg.com/vi/x/hq.jpg')) is None
