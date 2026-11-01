"""Capture rescue: getting the article when the plain fetch could not read it."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-rescue-'))

import pytest

from bt.hub import rescue

LONG = 'This is a substantial article body. ' * 20  # comfortably over MIN_BODY


def page(text=LONG):
    return f'<html><head><title>T</title></head><body><article><p>{text}</p></article></body></html>'.encode()


def ctx(**kw):
    return {'url': 'https://example.org/story', 'title': 'A story', 'html': b'', 'data': b'', 'feed_url': '', **kw}


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    """The fixed order runs unless a test asks for the model."""
    monkeypatch.setattr(rescue.ai, 'enabled', lambda: False)
    # The sandbox probe is cached for the process; each test starts from unknown.
    monkeypatch.setattr(rescue, '_sandbox_ok', None)


def test_refetch_tries_another_browser_identity(monkeypatch):
    seen = []

    def fetch(url, **kwargs):
        seen.append(kwargs.get('headers', {}).get('User-Agent', ''))
        if len(seen) == 1:
            raise ValueError('Source returned HTTP 403.')
        return page(), url, {'content_type': 'text/html'}

    monkeypatch.setattr(rescue.curation, 'fetch', fetch)
    assert rescue.tool_refetch(ctx()).startswith('This is a substantial')
    assert len(seen) == 2  # the first identity was refused, the second worked


def test_variants_try_the_amp_and_print_views(monkeypatch):
    tried = []

    def fetch(url, **kwargs):
        tried.append(url)
        if url.endswith('/amp'):
            return page(), url, {'content_type': 'text/html'}
        raise ValueError('Source returned HTTP 404.')

    monkeypatch.setattr(rescue.curation, 'fetch', fetch)
    assert rescue.tool_variants(ctx()) != ''
    assert 'https://example.org/story/amp' in tried


def test_json_api_reads_the_wordpress_rest_api(monkeypatch):
    def fetch(url, **kwargs):
        assert url == 'https://example.org/wp-json/wp/v2/posts?slug=story'
        return b'[{"content": {"rendered": "<p>' + LONG.encode() + b'</p>"}}]', url, {'content_type': 'application/json'}

    monkeypatch.setattr(rescue.curation, 'fetch', fetch)
    assert rescue.tool_json_api(ctx()) .startswith('This is a substantial')


def test_json_api_falls_back_to_the_json_ld_article_body():
    html = ('<html><head><script type="application/ld+json">'
            '{"@type": "NewsArticle", "articleBody": "' + LONG + '"}</script></head><body></body></html>').encode()
    assert rescue.tool_json_api(ctx(html=html)).startswith('This is a substantial')


def test_feed_full_reads_the_source_feed_content(monkeypatch):
    feed = ('<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>F</title><item>'
            '<title>A story</title><link>https://example.org/story</link>'
            f'<content:encoded><![CDATA[<p>{LONG}</p>]]></content:encoded></item></channel></rss>').encode()

    def fetch(url, **kwargs):
        assert url == 'https://example.org/feed.xml'
        return feed, url, {'content_type': 'application/rss+xml'}

    monkeypatch.setattr(rescue.curation, 'fetch', fetch)
    assert rescue.tool_feed_full(ctx(feed_url='https://example.org/feed.xml')).startswith('This is a substantial')
    # Without a known feed there is nothing to read.
    assert rescue.tool_feed_full(ctx()) == ''


def test_wayback_follows_the_nearest_snapshot(monkeypatch):
    def fetch(url, **kwargs):
        if url.startswith('https://archive.org/wayback/available'):
            return (b'{"archived_snapshots": {"closest": {"url": "http://web.archive.org/web/1/https://example.org/story"}}}',
                    url, {'content_type': 'application/json'})
        assert url.startswith('https://web.archive.org/')  # upgraded to HTTPS before fetching
        return page(), url, {'content_type': 'text/html'}

    monkeypatch.setattr(rescue.curation, 'fetch', fetch)
    assert rescue.tool_wayback(ctx()).startswith('This is a substantial')


def test_ocr_reads_a_scanned_pdf(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd[0])
        if cmd[0] == 'pdftoppm':
            out = cmd[-1].rsplit('/', 1)[0]
            open(f'{out}/p-1.png', 'wb').write(b'png')
        class R:
            returncode, stdout, stderr = 0, LONG.encode(), b''
        return R()

    monkeypatch.setattr(rescue.subprocess, 'run', run)
    monkeypatch.setattr(rescue.shutil, 'which', lambda name: f'/usr/bin/{name}')
    assert rescue.tool_ocr(ctx(data=b'%PDF-1.4 fake')).startswith('This is a substantial')
    assert calls[0] == 'pdftoppm' and 'tesseract' in calls
    # A page that is not a PDF is not OCR'd.
    assert rescue.tool_ocr(ctx(data=b'<html></html>')) == ''


def test_render_refuses_without_a_sandbox(monkeypatch):
    """Rendering untrusted pages must not fall back to a bare browser."""
    monkeypatch.setattr(rescue.shutil, 'which', lambda name: '/usr/bin/google-chrome' if name == 'google-chrome' else None)
    assert rescue.tool_render(ctx()) == ''


def test_render_refuses_even_with_a_working_sandbox(monkeypatch):
    monkeypatch.setattr(rescue, 'sandbox_works', lambda: True)
    monkeypatch.setattr(rescue.shutil, 'which', lambda name: f'/usr/bin/{name}')
    def forbidden(*args, **kwargs):
        pytest.fail('Untrusted browser must never launch without network isolation')
    monkeypatch.setattr(rescue.subprocess, 'run', forbidden)
    assert rescue.tool_render(ctx()) == ''



def test_rescue_tries_the_cheap_tools_first(monkeypatch):
    order = []

    def fake(name):
        def tool(_ctx):
            order.append(name)
            return LONG if name == 'variants' else ''
        return tool

    for name in rescue.ORDER:
        monkeypatch.setitem(rescue.TOOLS, name, (rescue.TOOLS[name][0], fake(name)))
    got = rescue.rescue(ctx(), 'No readable text was found on this page.')
    assert got['tool'] == 'variants'
    assert order == ['refetch', 'variants']  # stopped as soon as one worked


def test_rescue_asks_the_model_when_the_cheap_tools_fail(monkeypatch):
    order = []

    def fake(name):
        def tool(_ctx):
            order.append(name)
            return LONG if name == 'wayback' else ''
        return tool

    for name in rescue.ORDER:
        monkeypatch.setitem(rescue.TOOLS, name, (rescue.TOOLS[name][0], fake(name)))
    monkeypatch.setattr(rescue.ai, 'enabled', lambda: True)
    picks = iter(['wayback'])
    monkeypatch.setattr(rescue.ai, 'choose_rescue_tool', lambda *a: next(picks, ''))
    got = rescue.rescue(ctx(), 'No readable text was found on this page.')
    assert got['tool'] == 'wayback'
    assert order == ['refetch', 'variants', 'wayback']


def test_rescue_reports_nothing_when_every_tool_fails(monkeypatch):
    for name in rescue.ORDER:
        monkeypatch.setitem(rescue.TOOLS, name, (rescue.TOOLS[name][0], lambda _ctx: ''))
    got = rescue.rescue(ctx(), 'No readable text was found on this page.')
    assert got['body'] == '' and got['tool'] == ''
    assert len(got['notes']) == len(rescue.ORDER)  # every tool was tried and reported


def test_a_tool_that_raises_does_not_stop_the_others(monkeypatch):
    def boom(_ctx):
        raise RuntimeError('tool exploded')

    monkeypatch.setitem(rescue.TOOLS, 'refetch', ('boom', boom))
    monkeypatch.setitem(rescue.TOOLS, 'variants', ('works', lambda _ctx: LONG))
    got = rescue.rescue(ctx(), 'failed')
    assert got['tool'] == 'variants'
    assert any('refetch' in n for n in got['notes'])


def test_the_sandbox_probe_is_cached(monkeypatch):
    """A container that cannot create namespaces must not spawn a doomed bwrap every time."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        class R:
            returncode = 1
        return R()

    monkeypatch.setattr(rescue.subprocess, 'run', run)
    monkeypatch.setattr(rescue.shutil, 'which', lambda name: f'/usr/bin/{name}')
    assert rescue.sandbox_works() is False
    assert rescue.sandbox_works() is False
    assert len(calls) == 1  # probed once, then remembered


def test_nvd_api_recovers_a_short_record_before_page_variants(monkeypatch):
    import json
    seen = []
    def fetch(url, **kwargs):
        seen.append(url)
        assert url == 'https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=CVE-2024-21545'
        assert kwargs['kinds'] == ('application/json',)
        return json.dumps({'vulnerabilities': [{'cve': {'id': 'CVE-2024-21545',
            'descriptions': [{'lang': 'es', 'value': 'Otro'}, {'lang': 'en', 'value': 'A short advisory.'}]}}]}).encode(), url, {}
    monkeypatch.setattr(rescue.curation, 'fetch', fetch)
    got = rescue.rescue(ctx(url='https://nvd.nist.gov/vuln/detail/CVE-2024-21545'))
    assert got['tool'] == 'json_api' and 'A short advisory.' in got['body']
    assert 'Source: https://services.nvd.nist.gov/' in got['body']
    assert len(seen) == 1


@pytest.mark.parametrize('record', [
    {'id': 'CVE-2024-99999', 'descriptions': [{'lang': 'en', 'value': LONG}]},
    {'id': 'CVE-2024-21545', 'descriptions': [{'lang': 'en', 'value': ''}]},
    {'id': 'CVE-2024-21545', 'descriptions': [{'lang': 'es', 'value': LONG}]},
])
def test_nvd_api_refuses_missing_or_mismatched_description(monkeypatch, record):
    import json
    monkeypatch.setattr(rescue.curation, 'fetch', lambda url, **kw:
        (json.dumps({'vulnerabilities': [{'cve': record}]}).encode(), url, {}))
    assert rescue.tool_json_api(ctx(url='https://nvd.nist.gov/vuln/detail/CVE-2024-21545')) == ''


@pytest.mark.parametrize('url', ['https://nvd.nist.gov.evil.test/vuln/detail/CVE-2024-21545',
    'https://evil.test/nvd.nist.gov/vuln/detail/CVE-2024-21545',
    'https://nvd.nist.gov/vuln/detail/not-a-cve', 'http://nvd.nist.gov/vuln/detail/CVE-2024-21545'])
def test_nvd_route_is_limited_to_exact_public_record_urls(url):
    assert rescue._nvd_id(url) == ''


@pytest.mark.parametrize('count,long_text,expected', [(1, False, False), (11, False, True), (1, True, True)])
def test_ocr_reports_page_and_text_limits(monkeypatch, count, long_text, expected):
    seen = []
    def run(cmd, **kwargs):
        seen.append(cmd)
        if cmd[0] == 'pdftoppm':
            assert cmd[cmd.index('-l') + 1] == str(rescue.OCR_PAGES + 1)
            for n in range(count):
                rescue.Path(cmd[-1] + f'-{n + 1:02}.png').write_bytes(b'png')
        class Result:
            returncode = 0
            stdout = b'x' * (rescue.TEXT_LIMIT + 1) if long_text else b'Page text'
        return Result()
    monkeypatch.setattr(rescue.subprocess, 'run', run)
    monkeypatch.setattr(rescue.shutil, 'which', lambda name: '/usr/bin/' + name)
    context = ctx(data=b'%PDF-1.4 fake')
    assert rescue.tool_ocr(context)
    assert context['_ocr_truncated'] is expected
    assert sum(cmd[0] == 'tesseract' for cmd in seen) == min(count, rescue.OCR_PAGES)
