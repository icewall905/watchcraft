"""Common Room Reddit adapter: parsing, normalization, paging, search, comments, RSS fallback, token cache."""
import pytest

from bt import curation
from bt.commonroom import reddit_oauth
from bt.commonroom.adapters import ADAPTERS, Page
from bt.commonroom.adapters import reddit as rd


@pytest.fixture(autouse=True)
def oauth(monkeypatch):
    cfg = {'commonroom': {'reddit_client_id': 'cid', 'reddit_secret': 'sec'}}
    monkeypatch.setattr(reddit_oauth, 'get_settings', lambda: cfg)
    monkeypatch.setattr(reddit_oauth.time, 'sleep', lambda s: None)
    reddit_oauth.reset()
    calls = {'n': 0}

    def fake_token(cid, secret):
        calls['n'] += 1
        assert (cid, secret) == ('cid', 'sec')
        return {'access_token': f'tok{calls["n"]}', 'expires_in': 86400}
    monkeypatch.setattr(reddit_oauth, '_request_token', fake_token)
    yield cfg, calls
    reddit_oauth.reset()


def post(**kw):
    d = {'name': 't3_abc', 'id': 'abc', 'title': 'Hello', 'permalink': '/r/foo/comments/abc/hello/',
         'subreddit': 'foo', 'author': 'bob', 'score': 5, 'num_comments': 2, 'created_utc': 1731700000.0,
         'is_self': True, 'selftext': 'plain', 'url': 'https://www.reddit.com/r/foo/comments/abc/hello/'}
    d.update(kw)
    return {'kind': 't3', 'data': d}


def listing(*children, after=''):
    return {'data': {'children': list(children), 'after': after}}


def test_registered_and_parse():
    assert ADAPTERS['reddit'] is rd.ADAPTER
    for raw in ('r/selfhosted', '/r/selfhosted', 'selfhosted', 'https://www.reddit.com/r/selfhosted/top/?t=day',
                'reddit.com/r/selfhosted'):
        assert rd.parse(raw) == 'r/selfhosted'
    for bad in ('', 'r/a', 'bad name', 'https://example.com/r/foo', 'r/' + 'x' * 22):
        with pytest.raises(ValueError):
            rd.parse(bad)
    assert rd.ADAPTER.sorts == ('new', 'hot', 'top_day', 'top_week', 'top_month', 'top_year', 'top_all')


def test_fetch_normalizes_posts_and_pages(monkeypatch):
    seen = []
    gallery = post(id='g1', name='t3_g1', is_self=False, url='https://www.reddit.com/gallery/g1',
                   gallery_data={'items': [{'media_id': 'a'}, {'media_id': 'b'}]},
                   media_metadata={'b': {'s': {'u': 'https://i.redd.it/b.jpg?x=1&amp;y=2', 'x': 10, 'y': 20}},
                                   'a': {'s': {'u': 'https://preview.redd.it/a.jpg', 'x': 5, 'y': 6}}})
    video = post(id='v1', name='t3_v1', is_self=False, url='https://v.redd.it/zzz',
                 secure_media={'reddit_video': {'fallback_url': 'https://v.redd.it/zzz/DASH_720.mp4', 'width': 1280, 'height': 720}},
                 preview={'images': [{'source': {'url': 'https://preview.redd.it/p.jpg?a=1&amp;b=2', 'width': 1, 'height': 1}}]})
    linkp = post(id='l1', name='t3_l1', is_self=False, url_overridden_by_dest='https://example.com/a', title='Link',
                 over_18=True, spoiler=True, selftext='', preview={'images': [{'source': {'url': 'http://insecure/x.jpg'}}]})
    img = post(id='i1', name='t3_i1', is_self=False, url_overridden_by_dest='https://i.redd.it/pic.png')
    cross = post(id='c1', name='t3_c1', is_self=False, url_overridden_by_dest='https://www.reddit.com/r/bar/comments/q/x/',
                 crosspost_parent_list=[{'url_overridden_by_dest': 'https://i.imgur.com/q.jpg'}])
    selfp = post(selftext_html='&lt;div class="md"&gt;&lt;p&gt;hi&lt;/p&gt;&lt;/div&gt;')
    selfp['data']['selftext_html'] = '<div class="md"><p>hi there</p></div>'
    sticky = post(id='s1', name='t3_s1', stickied=True)
    removed = post(id='r1', name='t3_r1', selftext='[removed]')
    gone = post(id='r2', name='t3_r2', removed_by_category='moderator')

    def fake(url, headers=None):
        seen.append((url, headers))
        return listing(selfp, sticky, removed, gone, gallery, video, linkp, img, cross, after='t3_next')
    monkeypatch.setattr(rd, 'get_json', fake)

    page = rd.fetch('r/foo', 'top_week', 't3_prev')
    url, headers = seen[0]
    assert url.startswith('https://oauth.reddit.com/r/foo/top?') and 't=week' in url
    assert 'limit=100' in url and 'raw_json=1' in url and 'after=t3_prev' in url
    assert headers['Authorization'] == 'bearer tok1' and 'WatchCraft' in headers['User-Agent']
    assert page.cursor == 't3_next'
    by = {p['external_id']: p for p in page.posts}
    assert set(by) == {'t3_abc', 't3_g1', 't3_v1', 't3_l1', 't3_i1', 't3_c1'}  # sticky/removed skipped

    s = by['t3_abc']
    assert s['body'] == 'hi there' and s['link_url'] == '' and s['community'] == 'r/foo'
    assert s['url'] == 'https://www.reddit.com/r/foo/comments/abc/hello/'
    assert (s['author'], s['score'], s['comments'], s['published_at'], s['platform']) == ('bob', 5, 2, 1731700000.0, 'reddit')

    assert [m['url'] for m in by['t3_g1']['media']] == ['https://preview.redd.it/a.jpg', 'https://i.redd.it/b.jpg?x=1&y=2']
    v = by['t3_v1']['media'][0]
    assert v['kind'] == 'video' and v['url'].endswith('DASH_720.mp4') and v['poster'] == 'https://preview.redd.it/p.jpg?a=1&b=2'
    assert by['t3_v1']['link_url'] == ''

    link = by['t3_l1']
    assert link['link_url'] == 'https://example.com/a' and link['nsfw'] and link['spoiler'] and link['media'] == []
    assert by['t3_i1']['media'][0] == {'kind': 'image', 'url': 'https://i.redd.it/pic.png', 'poster': '', 'w': 0, 'h': 0}
    assert by['t3_c1']['media'][0]['url'] == 'https://i.imgur.com/q.jpg'
    assert by['t3_c1']['link_url'] == ''  # points at another reddit thread


def test_fetch_defaults_and_end_of_pages(monkeypatch):
    urls = []
    monkeypatch.setattr(rd, 'get_json', lambda u, headers=None: urls.append(u) or listing(post(), after=None))
    assert rd.fetch('foo', 'new', '').cursor == ''
    rd.fetch('foo', 'hot', '')
    rd.fetch('foo', 'bogus', '')
    assert '/r/foo/new?' in urls[0] and '/r/foo/hot?' in urls[1] and '/r/foo/new?' in urls[2]
    assert '&after=' not in urls[0] and '&t=' not in urls[1]


def test_search(monkeypatch, oauth):
    urls = []
    monkeypatch.setattr(rd, 'get_json', lambda u, headers=None: urls.append(u) or listing(post(), after='t3_n'))
    page = rd.search('home lab', 't3_c')
    u = urls[0]
    assert u.startswith('https://oauth.reddit.com/search?') and 'q=home+lab' in u and 'sort=new' in u
    assert 'type=link' in u and 'after=t3_c' in u and page.cursor == 't3_n' and len(page.posts) == 1
    oauth[0]['commonroom']['reddit_secret'] = ''
    assert rd.search('x', '') == Page()
    assert len(urls) == 1


def test_comments_tree(monkeypatch):
    def c(id, body, replies='', **kw):
        return {'kind': 't1', 'data': {'id': id, 'author': 'u' + id, 'body': body, 'score': 3,
                                       'created_utc': 10.0, 'replies': replies, **kw}}
    child = {'data': {'children': [c('c2', 'reply'), {'kind': 'more', 'data': {}}]}}
    tree = [listing(post()), {'data': {'children': [
        c('c1', 'top', child, body_html='<div class="md"><p>top &amp; bold</p></div>'),
        c('c3', '[deleted]'), c('c4', '[removed]'), {'kind': 'more', 'data': {}}]}}]
    urls = []
    monkeypatch.setattr(rd, 'get_json', lambda u, headers=None: urls.append((u, headers)) or tree)
    out = rd.comments({'external_id': 't3_abc'})
    assert urls[0][0].startswith('https://oauth.reddit.com/comments/abc?') and 'depth=6' in urls[0][0]
    assert urls[0][1]['Authorization'].startswith('bearer ')
    assert [x['id'] for x in out] == ['c1']
    assert out[0]['body'] == 'top & bold' and out[0]['score'] == 3
    assert [x['id'] for x in out[0]['children']] == ['c2'] and out[0]['children'][0]['children'] == []


def test_comments_without_oauth_best_effort(monkeypatch, oauth):
    oauth[0]['commonroom']['reddit_client_id'] = ''
    urls = []

    def boom(u, headers=None):
        urls.append(u)
        raise curation.FetchError(429, '30')
    monkeypatch.setattr(rd, 'get_json', boom)
    assert rd.comments({'external_id': 't3_abc'}) == []
    assert urls[0].startswith('https://www.reddit.com/comments/abc.json?')


RSS = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><id>t3_aaa</id><title>Link post</title><author><name>/u/zed</name></author>
<link href="https://www.reddit.com/r/foo/comments/aaa/link_post/"/>
<content type="html">&lt;a href="https://example.com/x?a=1&amp;amp;b=2"&gt;[link]&lt;/a&gt;</content>
<published>2024-11-15T20:00:00+00:00</published><media:thumbnail url="https://b.thumbs.redditmedia.com/t.jpg"/></entry>
<entry><id>t3_bbb</id><title>Self post</title><author><name>/u/amy</name></author>
<link href="https://www.reddit.com/r/foo/comments/bbb/self_post/"/>
<content type="html">&lt;p&gt;Body text&lt;/p&gt;</content><published>2024-11-15T21:00:00+00:00</published></entry></feed>'''


def test_rss_fallback_without_credentials(monkeypatch, oauth):
    oauth[0]['commonroom']['reddit_secret'] = ''
    feeds = []
    monkeypatch.setattr(rd, '_rss_get', lambda f: feeds.append(f) or RSS)
    monkeypatch.setattr(rd, 'get_json', lambda *a, **k: pytest.fail('JSON API must not be used'))
    page = rd.fetch('r/foo', 'hot', '')
    assert feeds[0].startswith('https://www.reddit.com/r/foo/.rss')
    assert page.cursor == '' and [p['external_id'] for p in page.posts] == ['t3_aaa', 't3_bbb']
    a, b = page.posts
    assert a['link_url'] == 'https://example.com/x?a=1&b=2' and a['author'] == 'zed' and a['score'] == 0
    assert a['media'][0]['url'].startswith('https://b.thumbs.redditmedia.com') and a['community'] == 'r/foo'
    assert b['body'] == 'Body text' and b['link_url'] == '' and b['published_at'] > 0
    rd.fetch('r/foo', 'new', '')
    assert '/r/foo/new/.rss' in feeds[1]
    assert rd.fetch('r/foo', 'new', 't3_x').posts == []  # no paging without OAuth


def test_token_cached_and_refreshed(oauth, monkeypatch):
    _, calls = oauth
    assert reddit_oauth.configured()
    assert reddit_oauth.headers()['Authorization'] == 'bearer tok1'
    reddit_oauth.headers()
    assert calls['n'] == 1
    clock = [reddit_oauth.time.monotonic()]
    monkeypatch.setattr(reddit_oauth.time, 'monotonic', lambda: clock[0] + 90_000)
    assert reddit_oauth.headers()['Authorization'] == 'bearer tok2'
    assert 'WatchCraft' in reddit_oauth.headers()['User-Agent']


def test_rate_limit_stops_callers(monkeypatch):
    def limited(u, headers=None):
        raise curation.FetchError(429, '120')
    monkeypatch.setattr(rd, 'get_json', limited)
    with pytest.raises(ValueError, match='slow down'):
        rd.fetch('foo', 'new', '')
    monkeypatch.setattr(rd, 'get_json', lambda *a, **k: pytest.fail('should be blocked by cooldown'))
    with pytest.raises(ValueError, match='slow down'):
        rd.fetch('foo', 'new', '')


def test_rss_fallback_skips_long_top_windows(monkeypatch, oauth):
    oauth[0]['commonroom']['reddit_secret'] = ''
    monkeypatch.setattr(rd, '_rss_get', lambda f: pytest.fail('no RSS read for long windows'))
    for sort in ('top_month', 'top_year', 'top_all'):
        assert rd.fetch('r/foo', sort, '') == Page()
    feeds = []
    monkeypatch.setattr(rd, '_rss_get', lambda f: feeds.append(f) or RSS)
    assert rd.fetch('r/foo', 'top_week', '').posts and 't=week' in feeds[0]


def test_401_drops_token_not_cooldown(monkeypatch, oauth):
    _, calls = oauth
    state = {'n': 0}

    def get(u, headers=None):
        state['n'] += 1
        if state['n'] == 1:
            raise curation.FetchError(401)
        return listing(post())
    monkeypatch.setattr(rd, 'get_json', get)
    with pytest.raises(ValueError):
        rd.fetch('foo', 'new', '')
    assert reddit_oauth._blocked_until == 0.0
    assert rd.fetch('foo', 'new', '').posts
    assert calls['n'] == 2  # a fresh token was fetched after the 401
