import json
from urllib.parse import parse_qs, urlsplit

import pytest

from bt.commonroom.adapters import ADAPTERS
from bt.commonroom.adapters import bluesky, lemmy, mastodon, mbin
from bt.hub import social_fetch


@pytest.fixture
def api(monkeypatch):
    """Register routes: api.route(substring, payload | callable | Exception). First match wins."""
    routes, calls = [], []

    def fake(url, **kwargs):
        calls.append(url)
        for key, val in routes:
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return json.dumps(val(url) if callable(val) else val).encode()
        raise AssertionError(f'unexpected fetch {url}')

    monkeypatch.setattr(social_fetch, 'fetch', fake)
    fake.route = lambda k, v: routes.append((k, v))
    fake.calls = calls
    return fake


def q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def denied(code):
    from bt.curation import FetchError
    return FetchError(code)


def test_search_unsupported():
    assert ADAPTERS['mastodon'].search is None and ADAPTERS['bluesky'].search is None


def test_registered():
    for k in ('lemmy', 'mbin', 'mastodon', 'bluesky'):
        assert ADAPTERS[k].key == k


# ---------------- Lemmy

@pytest.mark.parametrize('raw,out', [
    ('selfhosted@lemmy.world', 'selfhosted@lemmy.world'),
    ('!SelfHosted@Lemmy.World', 'selfhosted@lemmy.world'),
    ('https://lemmy.ml/c/linux', 'linux@lemmy.ml'),
    ('https://lemmy.world/c/linux@lemmy.ml', 'linux@lemmy.ml via lemmy.world'),
    ('linux@lemmy.ml via Lemmy.World', 'linux@lemmy.ml via lemmy.world'),
    ('linux@lemmy.ml via lemmy.ml', 'linux@lemmy.ml'),
])
def test_lemmy_parse(raw, out):
    assert lemmy.parse(raw) == out


@pytest.mark.parametrize('raw', ['', 'linux', 'a@b', 'http://lemmy.ml/c/x', 'x@localhost', 'x@lemmy.world/../a',
                                 'https://lemmy.ml/u/bob', 'a b@lemmy.world'])
def test_lemmy_parse_bad(raw):
    with pytest.raises(ValueError):
        lemmy.parse(raw)


def lpost(i, **post):
    return {'post': {'id': i, 'name': f'T{i}', 'ap_id': f'https://lemmy.world/post/{i}', 'body': '**hi**',
                     'url': 'https://news.example/a', 'published': '2026-11-01T10:00:00Z', **post},
            'community': {'name': 'linux', 'actor_id': 'https://lemmy.ml/c/linux'},
            'creator': {'name': 'bob'}, 'counts': {'score': 9, 'comments': 4}}


def test_lemmy_fetch_and_paging(api):
    api.route('/api/v3/post/list', lambda u: {'posts': [lpost(i) for i in range(50 if q(u)['page'] == '1' else 2)]})
    page = lemmy.fetch('linux@lemmy.ml', 'top_week', '')
    assert q(api.calls[-1]) == {'community_name': 'linux@lemmy.ml', 'sort': 'TopWeek', 'limit': '50', 'page': '1'}
    assert api.calls[-1].startswith('https://lemmy.ml/api/v3/post/list')
    assert page.cursor == '2' and len(page.posts) == 50
    p = page.posts[0]
    assert (p['platform'], p['community'], p['url'], p['link_url'], p['score'], p['comments'], p['author']) == \
        ('lemmy', '!linux@lemmy.ml', 'https://lemmy.world/post/0', 'https://news.example/a', 9, 4, 'bob')
    assert p['published_at'] > 0 and p['meta']['post_id'] == 0 and not p['nsfw']
    last = lemmy.fetch('linux@lemmy.ml', 'new', '2')
    assert last.cursor == '' and len(last.posts) == 2


def test_lemmy_via_instance(api):
    api.route('/api/v3/post/list', {'posts': [lpost(1)]})
    page = lemmy.fetch('linux@lemmy.ml via lemmy.world', 'new', '')
    assert api.calls[-1].startswith('https://lemmy.world/api/v3/post/list')
    assert q(api.calls[-1])['community_name'] == 'linux@lemmy.ml' and page.posts[0]['meta']['instance'] == 'lemmy.world'


def test_lemmy_image_nsfw_and_page_cap(api):
    row = lpost(1, url='https://i.example/x.PNG', nsfw=True)
    api.route('/api/v3/post/list', {'posts': [row] * 50})
    page = lemmy.fetch('linux@lemmy.ml', 'new', '20')
    assert page.cursor == ''
    p = page.posts[0]
    assert p['link_url'] == '' and p['media'][0]['url'] == 'https://i.example/x.PNG' and p['nsfw']


def test_lemmy_search_and_comments(api):
    api.route('/api/v3/search', {'posts': [lpost(5)]})
    page = lemmy.search('rust', '')
    assert api.calls[-1].startswith('https://lemmy.world/api/v3/search') and q(api.calls[-1])['type_'] == 'Posts'
    assert page.posts[0]['external_id'] == 'https://lemmy.world/post/5'
    assert lemmy.search('  ', '').posts == []

    def c(i, path, text, score=1):
        return {'comment': {'id': i, 'path': path, 'content': text, 'published': '2026-11-01T10:00:00Z'},
                'creator': {'name': f'u{i}'}, 'counts': {'score': score}}
    api.route('/api/v3/comment/list', {'comments': [c(12, '0.12', 'a'), c(34, '0.12.34', 'b'), c(35, '0.12.34.35', 'c'),
                                                      c(40, '0.40', 'd'), c(50, '0.99.50', 'orphan')]})
    tree = lemmy.comments(page.posts[0] | {'meta': {'instance': 'lemmy.ml', 'post_id': 5}})
    assert [t['id'] for t in tree] == ['12', '40', '50']
    assert tree[0]['children'][0]['children'][0]['body'] == 'c'
    assert q(api.calls[-1])['max_depth'] == '6'
    assert lemmy.comments({'meta': {}}) == []


# ---------------- Mbin

@pytest.mark.parametrize('raw,out', [
    ('technology@kbin.earth', 'technology@kbin.earth'),
    ('Fediverse', 'fediverse@kbin.earth'),
    ('!tech@fedia.io', 'tech@fedia.io'),
    ('https://fedia.io/m/tech', 'tech@fedia.io'),
    ('https://kbin.earth/m/tech@fedia.io', 'tech@fedia.io via kbin.earth'),
])
def test_mbin_parse(raw, out):
    assert mbin.parse(raw) == out


@pytest.mark.parametrize('raw', ['', 'a b', 'x@nohost', 'http://fedia.io/m/x', 'https://fedia.io/u/x', 'x@y@fedia.io'])
def test_mbin_parse_bad(raw):
    with pytest.raises(ValueError):
        mbin.parse(raw)


def entry(i, **kw):
    return {'entryId': i, 'title': f'E{i}', 'slug': f'e{i}', 'body': 'b', 'url': 'https://x.example/a',
            'magazine': {'magazineId': 3, 'name': 'tech'}, 'user': {'username': 'ann'},
            'image': {'storageUrl': 'https://kbin.earth/media/i.jpg', 'width': 10, 'height': 20},
            'uv': 5, 'dv': 1, 'numComments': 2, 'isAdult': False, 'createdAt': '2026-11-01T10:00:00+00:00', **kw}


def test_mbin_fetch_paging(api):
    api.route('/api/magazine/name/tech', {'magazineId': 3})
    api.route('/entries', lambda u: {'items': [entry(1), entry(2)], 'pagination': {'currentPage': int(q(u)['p']), 'maxPage': 2}})
    page = mbin.fetch('tech@kbin.earth', 'top_day', '')
    assert q(api.calls[-1]) == {'sort': 'top', 'p': '1', 'perPage': '50', 'time': '1d'}
    assert page.cursor == '2'
    p = page.posts[0]
    assert p['url'] == 'https://kbin.earth/m/tech/t/1/e1' and p['score'] == 4 and p['comments'] == 2
    assert p['media'][0]['w'] == 10 and p['link_url'] == 'https://x.example/a' and p['community'] == 'm/tech@kbin.earth'
    assert mbin.fetch('tech@kbin.earth', 'new', '2').cursor == ''
    assert q(api.calls[-1])['sort'] == 'newest' and 'time' not in q(api.calls[-1])


def test_mbin_via_instance(api):
    api.route('/api/magazine/name/tech@fedia.io', {'magazineId': 3})
    api.route('/entries', {'items': [entry(1)]})
    page = mbin.fetch('tech@fedia.io via kbin.earth', 'new', '')
    assert api.calls[0].startswith('https://kbin.earth/api/magazine/name/tech@fedia.io')
    assert page.posts[0]['url'].startswith('https://kbin.earth/m/')


def test_mbin_missing_magazine_and_comments(api):
    api.route('/api/magazine/name/nope', {'oops': 1})
    with pytest.raises(ValueError):
        mbin.fetch('nope@kbin.earth', 'new', '')
    api.route('/api/entry/1/comments', {'items': [
        {'commentId': 10, 'body': 'top', 'user': {'username': 'a'}, 'uv': 3, 'dv': 0,
         'children': [{'commentId': 11, 'body': 'kid', 'user': {'username': 'b'}, 'children': []}]}]})
    tree = mbin.comments({'meta': {'instance': 'kbin.earth', 'entry_id': 1}})
    assert tree[0]['score'] == 3 and tree[0]['children'][0]['body'] == 'kid'
    assert q(api.calls[-1]) == {'sortBy': 'top', 'd': '6'}
    assert mbin.comments({}) == []


# ---------------- Mastodon

@pytest.mark.parametrize('raw,out', [
    ('#Linux@fosstodon.org', '#linux@fosstodon.org'),
    ('#linux', '#linux@mastodon.social'),
    ('public@mastodon.world', 'public@mastodon.world'),
    ('https://fosstodon.org/tags/Rust', '#rust@fosstodon.org'),
    ('https://mastodon.world', 'public@mastodon.world'),
])
def test_mastodon_parse(raw, out):
    assert mastodon.parse(raw) == out


@pytest.mark.parametrize('raw', ['', 'linux', '#', '#a b@mastodon.social', '#x@bad', 'http://mastodon.social/tags/x',
                                 'https://mastodon.social/@bob'])
def test_mastodon_parse_bad(raw):
    with pytest.raises(ValueError):
        mastodon.parse(raw)


def status(i, **kw):
    return {'id': str(i), 'url': f'https://fosstodon.org/@bob/{i}', 'visibility': 'public',
            'content': '<p>Hello <b>world</b></p><p>more</p>', 'created_at': '2026-11-01T10:00:00.000Z',
            'account': {'acct': 'bob'}, 'favourites_count': 7, 'replies_count': 2, 'sensitive': False,
            'spoiler_text': '', 'tags': [{'name': 'rust'}],
            'media_attachments': [{'type': 'image', 'url': 'https://m.example/a.jpg', 'preview_url': 'https://m.example/p.jpg'},
                                  {'type': 'gifv', 'url': 'https://m.example/a.mp4', 'preview_url': 'https://m.example/g.jpg'}],
            **kw}


def test_mastodon_fetch_paging_and_normalization(api):
    api.route('/timelines/tag/', [status(i) for i in range(100, 60, -1)])
    page = mastodon.fetch('#rust@fosstodon.org', 'new', '')
    assert api.calls[-1] == 'https://fosstodon.org/api/v1/timelines/tag/rust?limit=40'
    assert page.cursor == '61'
    p = page.posts[0]
    assert p['title'] == 'Hello world' and p['body'] == 'Hello world\nmore' and p['url'] == 'https://fosstodon.org/@bob/100'
    assert [m['kind'] for m in p['media']] == ['image', 'video'] and p['media'][1]['poster'] == 'https://m.example/g.jpg'
    assert (p['score'], p['comments'], p['author'], p['community']) == (7, 2, 'bob', '#rust@fosstodon.org')
    mastodon.fetch('#rust@fosstodon.org', 'new', '61')
    assert q(api.calls[-1])['max_id'] == '61'


def test_mastodon_public_boost_sensitive_short_page(api):
    inner = status(5, sensitive=True, spoiler_text='cw', media_attachments=[])
    api.route('/timelines/public', [{'id': '9', 'reblog': inner, 'url': 'https://x.example/boost', 'content': ''}, status(8, content='', media_attachments=[])])
    page = mastodon.fetch('public@mastodon.social', 'new', '')
    assert q(api.calls[-1]) == {'limit': '40', 'local': 'true'}
    assert page.cursor == '' and len(page.posts) == 1
    p = page.posts[0]
    assert p['community'] == 'public@mastodon.social'
    assert p['url'] == 'https://fosstodon.org/@bob/5' and p['nsfw'] and p['spoiler']
    with pytest.raises(ValueError):
        mastodon.fetch('public@mastodon.social', 'new', 'abc')


def test_mastodon_login_required(api):
    api.route('/timelines/public', {'error': 'This API requires an authenticated user'})
    with pytest.raises(ValueError):
        mastodon.fetch('public@mastodon.social', 'new', '')


def test_mastodon_comments_tree(api):
    api.route('/statuses/1/context', {'ancestors': [], 'descendants': [
        status(2, in_reply_to_id='1'), status(3, in_reply_to_id='2'), status(4, in_reply_to_id='1')]})
    tree = mastodon.comments({'meta': {'instance': 'fosstodon.org', 'status_id': '1'}})
    assert [t['id'] for t in tree] == ['2', '4'] and tree[0]['children'][0]['id'] == '3'
    assert mastodon.comments({'meta': {'instance': 'bad host', 'status_id': '1'}}) == []


# ---------------- Bluesky

DID = 'did:plc:' + 'a' * 24


@pytest.mark.parametrize('raw,out', [
    ('@Bsky.App', '@bsky.app'),
    ('bsky.app', '@bsky.app'),
    (DID, '@' + DID),
    ('https://bsky.app/profile/bsky.app', '@bsky.app'),
    (f'at://{DID}/app.bsky.feed.generator/Whats-Hot', f'at://{DID}/app.bsky.feed.generator/Whats-Hot'),
    ('https://bsky.app/profile/bsky.app/feed/Cool', 'at://bsky.app/app.bsky.feed.generator/Cool'),
])
def test_bluesky_parse(raw, out):
    assert bluesky.parse(raw) == out


@pytest.mark.parametrize('raw', ['', '@x', '@a b.com', 'https://example.com/profile/x.com', 'https://bsky.app/search',
                                 'at://x.com/app.bsky.feed.post/abc', 'at://bad/app.bsky.feed.generator/x'])
def test_bluesky_parse_bad(raw):
    with pytest.raises(ValueError):
        bluesky.parse(raw)


def bpost(i, **kw):
    return {'post': {'uri': f'at://{DID}/app.bsky.feed.post/3k{i}', 'cid': 'c', 'author': {'handle': 'bsky.app'},
                     'record': {'text': 'Hello Bluesky\nline2', 'createdAt': '2026-11-01T10:00:00Z'},
                     'likeCount': 12, 'replyCount': 3, 'repostCount': 1,
                     'embed': {'$type': 'app.bsky.embed.images#view',
                               'images': [{'fullsize': 'https://cdn.bsky.app/i.jpg', 'aspectRatio': {'width': 4, 'height': 3}}]},
                     **kw}}


def test_bluesky_author_feed(api):
    repost = bpost(9)
    repost['reason'] = {'$type': 'app.bsky.feed.defs#reasonRepost'}
    api.route('getAuthorFeed', {'feed': [bpost(1), repost, bpost(2, labels=[{'val': 'porn'}], embed={
        '$type': 'app.bsky.embed.external#view', 'external': {'uri': 'https://news.example/x', 'thumb': 'https://cdn.bsky.app/t.jpg'}})],
        'cursor': 'next1'})
    page = bluesky.fetch('@bsky.app', 'new', '')
    assert q(api.calls[-1])['actor'] == 'bsky.app' and q(api.calls[-1])['limit'] == '50'
    assert page.cursor == 'next1' and len(page.posts) == 2
    a, b = page.posts
    assert a['url'] == 'https://bsky.app/profile/bsky.app/post/3k1' and a['title'] == 'Hello Bluesky'
    assert (a['score'], a['comments'], a['nsfw'], a['media'][0]['w']) == (12, 3, False, 4)
    assert b['nsfw'] and b['link_url'] == 'https://news.example/x' and b['media'][0]['url'].endswith('t.jpg')
    again = bluesky.fetch('@bsky.app', 'new', 'next1')
    assert q(api.calls[-1])['cursor'] == 'next1'
    assert again.cursor == ''  # server echoed the same cursor: stop paging


def test_bluesky_feed_resolves_handle_and_video(api):
    api.route('getProfile', {'did': DID})
    vid = bpost(3, embed={'$type': 'app.bsky.embed.video#view', 'playlist': 'https://video.bsky.app/p.m3u8',
                           'thumbnail': 'https://video.bsky.app/t.jpg'})
    api.route('getFeed', {'feed': [vid]})
    page = bluesky.fetch('at://bsky.app/app.bsky.feed.generator/Cool', 'new', '')
    assert q(api.calls[-1])['feed'] == f'at://{DID}/app.bsky.feed.generator/Cool'
    assert page.cursor == '' and page.posts[0]['media'][0]['kind'] == 'video'
    assert page.posts[0]['media'][0]['poster'] == 'https://video.bsky.app/t.jpg'


def test_bluesky_thread_comments(api):
    def r(i, kids=()):
        return {'post': bpost(i)['post'], 'replies': list(kids)}
    api.route('getPostThread', {'thread': {'post': bpost(0)['post'], 'replies': [r(1, [r(2)]), r(3), {'$type': 'blocked'}]}})
    tree = bluesky.comments({'meta': {'uri': f'at://{DID}/app.bsky.feed.post/3k0'}})
    assert [t['id'][-3:] for t in tree] == ['3k1', '3k3'] and tree[0]['children'][0]['body'].startswith('Hello')
    assert q(api.calls[-1])['depth'] == '6'
    assert bluesky.comments({'meta': {'uri': 'junk'}, 'external_id': 'junk'}) == []


def test_all_output_urls_https(api):
    api.route('/timelines/public', [status(1, url='http://x.example/p', uri='http://x.example/p')])
    assert mastodon.fetch('public@mastodon.social', 'new', '').posts == []
