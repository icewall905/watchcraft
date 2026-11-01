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


def test_render_runs_the_browser_inside_bubblewrap(monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen['cmd'] = cmd
        class R:
            returncode, stdout, stderr = 0, page(), b''
        return R()

    monkeypatch.setattr(rescue.subprocess, 'run', run)
    monkeypatch.setattr(rescue.shutil, 'which', lambda name: f'/usr/bin/{name}')
    assert rescue.tool_render(ctx()).startswith('This is a substantial')
    cmd = seen['cmd']
    assert cmd[0] == 'bwrap' and '--unshare-pid' in cmd
    assert '--tmpfs' in cmd  # the data and media directories are masked
    # Chrome needs a writable profile inside the sandbox, and the '=' form: a space-separated
    # value is read as a second URL and headless mode refuses it.
    assert '--user-data-dir=/tmp/chrome-profile' in cmd


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
