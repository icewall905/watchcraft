"""Pull-to-refresh on News sends article collectors out for more; old small budgets migrate to 500."""
import time

from sqlmodel import SQLModel

from test_curation import client  # noqa: F401  (isolated DB/API fixture)

from bt.db import session
from bt.hub import engine, migrate
from bt.hub.models import Curator
from bt.models import Setting


def add(name, *, types=('news',), last_run=None, status='', next_run=0.0, enabled=True, interval=6):
    with session() as s:
        c = Curator(profile_id=1, name=name, kind='watch', content_types=list(types), interval_hours=interval,
                    enabled=enabled, last_run=last_run, last_status=status, next_run=next_run)
        s.add(c); s.commit(); s.refresh(c)
        return c.id


def test_refresh_starts_idle_article_collectors_only(client, monkeypatch):
    started = []
    monkeypatch.setattr(engine, 'start', started.append)
    now = time.time()
    idle = add('Idle', last_run=now - 3600)
    add('Just ran', last_run=now - 60)
    add('Running', status='running', next_run=now + 600)
    add('Video', types=('video',))
    add('Paused', enabled=False)
    add('Manual only', interval=0)
    r = client.post('/api/hub/news/refresh').json()
    assert started == [idle]
    assert r['started'] == 1 and r['running'] == 1 and r['recent'] == 1 and r['next_at'] is None
    with session() as s:
        assert s.get(Curator, idle).last_status == 'queued'


def test_refresh_reports_when_everything_just_ran(client, monkeypatch):
    monkeypatch.setattr(engine, 'start', lambda cid: None)
    add('Just ran', last_run=time.time() - 60)
    r = client.post('/api/hub/news/refresh').json()
    assert r['started'] == 0 and r['recent'] == 1 and r['next_at'] > time.time()


def test_new_curators_default_to_a_deep_budget():
    assert Curator(profile_id=1, name='x').max_per_run == 500


def test_v5_raises_small_article_budgets_and_leaves_others(tmp_path):
    from sqlalchemy import create_engine
    from sqlmodel import Session
    db = tmp_path / 'deployed.db'
    eng = create_engine(f'sqlite:///{db}')
    with eng.begin() as conn:
        SQLModel.metadata.create_all(conn)
    with Session(eng) as s:
        s.add(Setting(key='watchcraft_schema_version', value={'version': 4}))
        for i, (kind, types, n) in enumerate([('watch', ['news'], 30), ('watch', ['article', 'web'], 20),
                                              ('watch', ['news'], 800), ('watch', ['meme'], 16),
                                              ('research', ['news'], 40)], 1):
            s.add(Curator(id=i, profile_id=1, name=f'c{i}', kind=kind, content_types=types, max_per_run=n))
        s.commit()
    with eng.begin() as conn:
        migrate.migrate(conn)
        migrate.migrate(conn)  # idempotent
    with Session(eng) as s:
        assert [s.get(Curator, i).max_per_run for i in range(1, 6)] == [500, 500, 800, 16, 40]


REDDIT_ATOM = b'''<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"
  xmlns:media="http://search.yahoo.com/mrss/"><title>top scoring links : worldnews</title>
<entry><title>Harbour vote passes</title><link href="https://www.reddit.com/r/worldnews/comments/a1/harbour/"/>
  <published>2026-11-13T08:00:00+00:00</published><media:thumbnail url="https://b.thumbs.redditmedia.com/x.jpg"/>
  <content type="html">&lt;a href="https://www.bbc.co.uk/news/harbour?a=1&amp;amp;b=2"&gt;[link]&lt;/a&gt; &lt;a href="https://www.reddit.com/r/worldnews/comments/a1/"&gt;[comments]&lt;/a&gt;</content></entry>
<entry><title>Discussion thread</title><link href="https://www.reddit.com/r/worldnews/comments/a2/"/>
  <content type="html">&lt;a href="https://www.reddit.com/r/worldnews/comments/a2/"&gt;[link]&lt;/a&gt;</content></entry>
<entry><title>A picture</title><link href="https://www.reddit.com/r/worldnews/comments/a3/"/>
  <content type="html">&lt;a href="https://i.redd.it/pic.jpg"&gt;[link]&lt;/a&gt;</content></entry>
</feed>'''


def test_reddit_resolves_linked_articles_and_skips_self_posts(monkeypatch):
    from bt import curation
    from bt.hub.providers import PROVIDERS, reddit_feed
    asked = []
    monkeypatch.setattr(curation, 'fetch', lambda url, **kw: asked.append(url) or REDDIT_ATOM)
    items = PROVIDERS['subreddit'].run('r/worldnews')
    assert asked == ['https://www.reddit.com/r/worldnews/top/.rss?t=day&limit=50']
    assert [i['url'] for i in items] == ['https://www.bbc.co.uk/news/harbour?a=1&b=2']
    item = items[0]
    assert item['publisher'] == 'bbc.co.uk' and item['type'] == 'news' and item['published_at']
    assert item['meta']['discussion_url'].startswith('https://www.reddit.com/r/worldnews/comments/a1')
    assert reddit_feed('https://www.reddit.com/r/science/new/') == 'https://www.reddit.com/r/science/new/.rss?limit=50'
    for bad in ('https://example.com/r/news', 'r/x', 'r/../etc'):
        try:
            reddit_feed(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_watch_runs_skip_old_articles_and_take_newest_first(client, monkeypatch):
    from sqlmodel import select
    from bt.hub import polling
    monkeypatch.setattr(engine, 'NEWS_MAX_AGE', 14 * 86400)
    from bt.hub.models import ContentItem, CuratorSource, Source
    now = time.time()
    rows = [{'url': f'https://news.example/{n}', 'title': f'Harbour story {n}', 'type': 'news', 'provider': 'rss',
             'publisher': 'Example', 'published_at': now - age, 'meta': {}}
            for n, age in enumerate([30 * 86400, 3600, 2 * 86400, 20 * 86400])]
    monkeypatch.setattr(polling, 'fetch', lambda prov, url, fmt, **kw: polling.Result(
        items=[dict(r) for r in rows], fetched_at=now, shared=False, error=''))
    with session() as s:
        c = Curator(profile_id=1, name='Harbour', kind='watch', content_types=['news'], keywords=['harbour'],
                    ai_enabled=False, max_per_run=1, interval_hours=6)
        src = Source(profile_id=1, provider='rss', url='https://news.example/feed', name='Example')
        s.add(c); s.add(src); s.commit(); s.refresh(c); s.refresh(src)
        s.add(CuratorSource(curator_id=c.id, source_id=src.id)); s.commit()
        cid = c.id
    engine.run(cid, 'manual')
    with session() as s:
        titles = [i.title for i in s.exec(select(ContentItem)).all()]
    assert titles == ['Harbour story 1']


def test_bing_news_unwraps_click_links_per_query(monkeypatch):
    from bt import curation
    from bt.hub.providers import PROVIDERS
    rss = b'''<rss version="2.0"><channel><title>harbour - Bing News</title>
      <item><title>Harbour vote passes</title><link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.bbc.co.uk%2fnews%2fh1&amp;c=1</link>
        <pubDate>Fri, 13 Nov 2026 08:00:00 GMT</pubDate><description>Council approves it.</description></item>
      <item><title>Insecure</title><link>http://www.bing.com/news/apiclick.aspx?url=http%3a%2f%2fexample.org%2fx</link></item>
    </channel></rss>'''
    asked = []
    monkeypatch.setattr(curation, 'fetch', lambda url, **kw: asked.append(url) or rss)
    assert PROVIDERS['bing_news'].run('', '') == []
    items = PROVIDERS['bing_news'].run('', 'harbour vote')
    assert 'q=harbour+vote' in asked[0] and 'format=rss' in asked[0]
    assert [(i['url'], i['publisher'], i['provider']) for i in items] == [('https://www.bbc.co.uk/news/h1', 'bbc.co.uk', 'bing_news')]


def test_bluesky_collects_linked_articles_from_accounts_and_feeds(monkeypatch):
    import json
    from bt import curation
    from bt.hub.providers import PROVIDERS, bluesky_source
    post = lambda link, title, labels=(): {'post': {
        'uri': 'at://did:plc:x/app.bsky.feed.post/abc', 'author': {'handle': 'reuters.com'}, 'labels': [{'val': v} for v in labels],
        'record': {'createdAt': '2026-11-13T08:00:00Z'}, 'likeCount': 4,
        'embed': {'$type': 'app.bsky.embed.external#view', 'external': {'uri': link, 'title': title, 'description': 'd'}}}}
    feed = {'feed': [post('https://www.reuters.com/a', 'Harbour vote'), post('https://www.reuters.com/a', 'dup'),
                     post('https://bsky.app/profile/x', 'internal'), post('https://x.example/n', 'nsfw', ('porn',)),
                     {'post': {'uri': 'at://x/p/1', 'author': {}, 'record': {}}}]}
    asked = []
    def fetch(url, **kw):
        asked.append(url)
        return json.dumps({'did': 'did:plc:abcdefghijklmnopqrstuvwx'} if 'getProfile' in url else feed).encode()
    monkeypatch.setattr(curation, 'fetch', fetch)
    items = PROVIDERS['bluesky'].run('https://bsky.app/profile/reuters.com')
    assert 'getAuthorFeed' in asked[0] and [i['url'] for i in items] == ['https://www.reuters.com/a']
    assert items[0]['meta']['discussion_url'] == 'https://bsky.app/profile/reuters.com/post/abc'
    PROVIDERS['bluesky'].run('https://bsky.app/profile/someone.example/feed/news')
    assert 'getProfile' in asked[1] and 'app.bsky.feed.generator%2Fnews' in asked[2]
    assert bluesky_source('@apnews.com') == ('author', 'apnews.com', '')
    for bad in ('https://evil.example/profile/x.com', 'https://bsky.app/profile/'):
        try:
            bluesky_source(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_tiktok_lists_account_clips_as_local_moments(monkeypatch):
    import yt_dlp
    from bt.hub.providers import PROVIDERS, tiktok_handle
    asked = []

    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            asked.append((url, self.opts.get('extract_flat'), download))
            return {'entries': [
                {'id': '7707687163302776086', 'title': 'Plane lands', 'duration': 14, 'timestamp': 1794585748,
                 'thumbnails': [{'url': 'https://p16.tiktokcdn.com/a.jpg'}], 'uploader': 'bbcnews'},
                {'id': '7707687163302776087', 'title': 'Long', 'duration': 600},
                {'id': 'not-a-number', 'title': 'Bad'}]}
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYDL)
    items = PROVIDERS['tiktok'].run('https://www.tiktok.com/@bbcnews')
    assert asked == [('https://www.tiktok.com/@bbcnews', True, False)]
    assert len(items) == 1
    clip = items[0]
    assert clip['type'] == 'meme' and clip['provider'] == 'tiktok' and clip['published_at'] == 1794585748
    assert clip['url'] == 'https://www.tiktok.com/@bbcnews/video/7707687163302776086'
    assert clip['meta']['media'] == {'type': 'video', 'via': 'yt-dlp', 'url': clip['url']}
    assert tiktok_handle('@nasa') == tiktok_handle('https://m.tiktok.com/@nasa?lang=en') == 'nasa'
    for bad in ('https://evil.example/@nasa', 'https://www.tiktok.com/tag/funny', '@a', '@bad/handle'):
        try:
            tiktok_handle(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_ytdlp_clip_capture_is_host_allowlisted_and_bounded(monkeypatch):
    import pytest
    import yt_dlp
    from bt.hub import capture
    for bad in ('http://www.tiktok.com/@a/video/1', 'https://evil.example/@a/video/1', 'https://127.0.0.1/x'):
        with pytest.raises(ValueError, match='not supported'):
            capture.fetch_ytdlp_clip(bad)
    seen = {}

    class FakeYDL:
        def __init__(self, opts): seen.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def download(self, urls):
            out = seen['outtmpl'].replace('%(ext)s', 'mp4')
            with open(out, 'wb') as fh:
                fh.write(b'\0\0\0\x18ftypmp42' + b'\0' * 32)
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYDL)
    data = capture.fetch_ytdlp_clip('https://www.tiktok.com/@nasa/video/7707687163302776086')
    assert data[4:8] == b'ftyp'
    assert seen['max_filesize'] == capture.VIDEO_MAX_BYTES and seen['noplaylist'] is True
    assert seen['allowed_extractors'] == ['^TikTok$']
