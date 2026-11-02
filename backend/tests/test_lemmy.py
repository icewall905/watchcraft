import json
from urllib.parse import parse_qs, urlsplit

import pytest

from bt.hub.lemmy import community_source, lemmy


def post(**changes):
    return {'post': {'id': 1, 'name': 'A story', 'ap_id': 'https://origin.example/post/7',
                     'body': '<script>bad()</script><p>Discussion</p>', 'url': 'https://news.example/story',
                     'published': '2026-11-02T12:00:00Z', **changes},
            'community': {'name': 'news', 'visibility': 'Public', 'actor_id': 'https://origin.example/c/news'},
            'creator': {'name': 'alice'}, 'counts': {'score': 8, 'comments': 3}}


def mock_api(monkeypatch, posts=None, v4=False):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if '/api/v3/site' in url and v4:
            raise ValueError('Source returned HTTP 404.')
        return json.dumps({'version': '1.0.0' if v4 else '0.19.21'} if url.endswith('/site')
                          else {('items' if v4 else 'posts'): posts if posts is not None else [post()]}).encode()
    monkeypatch.setattr('bt.hub.lemmy.social_fetch.fetch', fetch)
    return calls


def test_bounded_community_canonical_identity_and_article_separate(monkeypatch):
    calls = mock_api(monkeypatch, [post(), post()])
    items = lemmy('https://fetch.example/c/news@origin.example', 'unrelated query')
    assert len(calls) == 2
    assert parse_qs(urlsplit(calls[1]).query) == {'community_name': ['news@origin.example'], 'sort': ['Hot'], 'limit': ['20'], 'show_nsfw': ['false']}
    assert len(items) == 1
    assert items[0]['external_id'] == items[0]['url'] == 'https://origin.example/post/7'
    assert items[0]['publisher'] == '!news@origin.example'
    assert items[0]['meta']['article_url'] == 'https://news.example/story'
    assert items[0]['body'] == 'Discussion'


@pytest.mark.parametrize('changes', [{'removed': True}, {'deleted': True}, {'nsfw': True}, {'ap_id': 'http://unsafe.example/post/7'}])
def test_excluded_posts(monkeypatch, changes):
    mock_api(monkeypatch, [post(**changes)])
    assert lemmy('https://fetch.example/c/news@origin.example') == []


@pytest.mark.parametrize('changes', [{'removed': True}, {'deleted': True}, {'nsfw': True}, {'visibility': 'local_only'}, {'name': 'other'}])
def test_excluded_communities(monkeypatch, changes):
    view = post()
    view['community'].update(changes)
    mock_api(monkeypatch, [view])
    assert lemmy('https://fetch.example/c/news@origin.example') == []


def test_v4_capabilities_sort(monkeypatch):
    calls = mock_api(monkeypatch, v4=True)
    assert lemmy('https://fetch.example/c/news@origin.example', config={'sort': 'TopDay', 'limit': 30, 'item_type': 'meme'})[0]['type'] == 'meme'
    assert len(calls) == 3
    assert '/api/v4/post/list?' in calls[-1]
    assert parse_qs(urlsplit(calls[-1]).query)['time_range_seconds'] == ['86400']
    assert parse_qs(urlsplit(calls[-1]).query)['sort'] == ['top']


@pytest.mark.parametrize('code', [401, 403, 429, 500])
def test_no_fallback_on_denied_or_transient(monkeypatch, code):
    calls = []
    def fail(url, **kwargs):
        calls.append(url)
        raise ValueError(f'Source returned HTTP {code}.')
    monkeypatch.setattr('bt.hub.lemmy.social_fetch.fetch', fail)
    with pytest.raises(ValueError, match=str(code)):
        lemmy('https://fetch.example/c/news', config={'allow_rss': True})
    assert len(calls) == 1


@pytest.mark.parametrize('source', ['https://fetch.example', 'http://fetch.example/c/news', 'https://user:pw@fetch.example/c/news',
                                   'https://fetch.example/c/news?auth=secret', 'https://fetch.example/c/news/extra', '!news@fetch.example/extra'])
def test_invalid_source(source):
    with pytest.raises(ValueError):
        community_source(source)


def test_rss_fallback_explicit_and_bounded(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if '/api/' in url:
            raise ValueError('Source returned HTTP 404.')
        return b'<rss><channel><title>News</title><item><title>A story</title><link>https://origin.example/post/7</link></item></channel></rss>'
    monkeypatch.setattr('bt.hub.lemmy.social_fetch.fetch', fetch)
    with pytest.raises(ValueError, match='Enable RSS'):
        lemmy('https://fetch.example/c/news')
    assert len(calls) == 2
    calls.clear()
    items = lemmy('https://fetch.example/c/news', config={'allow_rss': True})
    assert len(calls) == 3
    assert items[0]['provider'] == 'lemmy'
    assert items[0]['meta']['rss_fallback'] is True
    assert calls[-1] == 'https://fetch.example/feeds/c/news.xml?sort=Hot'


def test_local_community_does_not_accept_same_name_remote(monkeypatch):
    mock_api(monkeypatch)
    assert lemmy('https://fetch.example/c/news') == []


def test_v4_published_counts_and_actor_schema(monkeypatch):
    view = post(published=None, published_at='2026-11-02T12:00:00Z', score=12, comments=4)
    view.pop('counts')
    view['community']['ap_id'] = view['community'].pop('actor_id')
    mock_api(monkeypatch, [view], v4=True)
    item = lemmy('https://fetch.example/c/news@origin.example')[0]
    assert item['published_at'] is not None
    assert item['meta']['observations'] == {'score': 12, 'comments': 4}
    assert item['meta']['social_post'] is True
    assert item['meta']['ranking'] == 'Hot'
    assert item['meta']['observed_at'] > 0
