import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from test_curation import client  # noqa: F401

from bt.hub import moodboard_sources as sources


def settings(monkeypatch, **cfg):
    base = {'web': False, 'reddit': False, 'bluesky': False, 'mastodon': False, 'mastodon_instance': 'mastodon.social',
            'flickr_api_key': '', 'tumblr_api_key': '', 'source_timeout_s': 20}
    monkeypatch.setattr(sources, 'get_settings', lambda: {'search': {'provider': 'none', 'searxng_url': ''},
                                                          'moodboard_sources': {**base, **cfg}})


def http(monkeypatch, routes):
    """routes: host -> payload or callable(url); records every URL fetched through the safe fetcher."""
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs == {'kinds': ('application/json',), 'limit': sources.MAX_RESPONSE_BYTES}
        payload = routes[urlsplit(url).hostname]
        payload = payload(url) if callable(payload) else payload
        return json.dumps(payload).encode()
    monkeypatch.setattr(sources.social_fetch, 'fetch', fetch)
    return calls


def reddit_post(n=1, **changes):
    post = {'title': f'Cozy reading nook {n}', 'permalink': f'/r/CozyPlaces/comments/abc{n}/nook/', 'over_18': False,
            'author': 'maker', 'subreddit_name_prefixed': 'r/CozyPlaces', 'url': f'https://i.redd.it/nook{n}.jpg',
            'preview': {'images': [{'source': {'width': 1200, 'height': 1600}}]}}
    post.update(changes)
    return {'kind': 't3', 'data': post}


def test_reddit_image_posts_attribution_and_nsfw(monkeypatch):
    gallery = reddit_post(3, url='https://www.reddit.com/gallery/abc3', is_gallery=True,
                          gallery_data={'items': [{'media_id': 'm1'}]},
                          media_metadata={'m1': {'status': 'valid', 'e': 'Image',
                                                 's': {'u': 'https://preview.redd.it/m1.jpg?width=800&amp;s=x', 'x': 800, 'y': 600}}})
    calls = http(monkeypatch, {'www.reddit.com': {'data': {'children': [
        reddit_post(1), reddit_post(2, over_18=True), gallery, reddit_post(4, url='https://example.com/article'),
        reddit_post(5, is_video=True), reddit_post(6, spoiler=True), reddit_post(7, title='nude beach'),
        reddit_post(8, over_18=None)]}}})
    found = sources.reddit_search('cozy nook', 30, offset=30)
    params = parse_qs(urlsplit(calls[0]).query)
    assert params['include_over_18'] == ['off'] and params['sort'] == ['new'] and params['q'] == ['cozy nook']
    assert [f['source_url'] for f in found] == ['https://www.reddit.com/r/CozyPlaces/comments/abc1/nook/',
                                                'https://www.reddit.com/r/CozyPlaces/comments/abc3/nook/']
    assert found[0]['image_url'] == 'https://i.redd.it/nook1.jpg' and found[0]['width'] == 1200
    assert found[0]['attribution'] == 'u/maker in r/CozyPlaces (Reddit)' and found[0]['provider'] == 'reddit'
    assert found[0]['license'] == sources.RIGHTS
    assert found[1]['image_url'] == 'https://preview.redd.it/m1.jpg?width=800&s=x'


def bsky_post(n=1, **changes):
    post = {'uri': f'at://did:plc:abc/app.bsky.feed.post/3k{n}', 'author': {'handle': 'artist.bsky.social'},
            'record': {'text': f'Watercolor botanicals {n}'},
            'embed': {'$type': 'app.bsky.embed.images#view', 'images': [
                {'fullsize': f'https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:abc/b{n}@jpeg', 'alt': 'fern',
                 'aspectRatio': {'width': 900, 'height': 1200}}]}}
    post.update(changes)
    return post


def test_bluesky_images_labels_and_pagination(monkeypatch):
    with_media = bsky_post(4, embed={'$type': 'app.bsky.embed.recordWithMedia#view',
                                     'media': bsky_post(4)['embed']})
    calls = http(monkeypatch, {'public.api.bsky.app': {'posts': [
        bsky_post(1), bsky_post(2, labels=[{'val': 'porn'}]), bsky_post(3, embed={'$type': 'app.bsky.embed.external#view'}),
        with_media, bsky_post(5, author={'handle': 'x.bsky.social', 'labels': [{'val': 'sexual'}]}),
        bsky_post(6, author={'handle': 'bad handle'})]}})
    found = sources.bluesky_search('watercolor botanicals', 30, offset=60)
    params = parse_qs(urlsplit(calls[0]).query)
    assert params['cursor'] == ['100'] and params['sort'] == ['top']
    assert [f['source_url'] for f in found] == ['https://bsky.app/profile/artist.bsky.social/post/3k1',
                                                'https://bsky.app/profile/artist.bsky.social/post/3k4']
    assert found[0]['attribution'] == '@artist.bsky.social (Bluesky)' and found[0]['height'] == 1200
    assert 'fern' in found[0]['description']


def toot(n=1, **changes):
    status = {'url': f'https://mastodon.social/@painter/{n}', 'visibility': 'public', 'sensitive': False,
              'spoiler_text': '', 'content': f'<p>Fresh garden sketch {n}</p>', 'account': {'acct': 'painter'},
              'media_attachments': [{'type': 'image', 'url': f'https://files.mastodon.social/media/{n}.png',
                                     'description': 'pencil roses', 'meta': {'original': {'width': 640, 'height': 480}}}]}
    status.update(changes)
    return status


def test_mastodon_tag_rotation_sensitive_and_time_paging(monkeypatch):
    calls = http(monkeypatch, {'mastodon.social': [toot(1), toot(2, sensitive=True), toot(3, spoiler_text='cw'),
                                                   toot(4, media_attachments=[{'type': 'gifv', 'url': 'https://x.example/a.mp4'}]),
                                                   {'reblog': toot(5)}]})
    first = sources.mastodon_search('garden sketch', 30, 0)
    assert urlsplit(calls[0]).path == '/api/v1/timelines/tag/gardensketch'
    assert 'max_id' not in parse_qs(urlsplit(calls[0]).query)
    assert [f['source_url'] for f in first] == ['https://mastodon.social/@painter/1', 'https://mastodon.social/@painter/5']
    assert first[0]['title'] == 'Fresh garden sketch 1' and first[0]['attribution'] == '@painter (Mastodon)'
    sources.mastodon_search('garden sketch', 30, 30)
    assert urlsplit(calls[1]).path.endswith('/garden')
    sources.mastodon_search('garden sketch', 30, 90)  # Tags exhausted: step back in time.
    assert urlsplit(calls[2]).path.endswith('/gardensketch') and parse_qs(urlsplit(calls[2]).query)['max_id']
    with pytest.raises(ValueError):
        sources.mastodon_search('garden', 30, 0, instance='localhost')


def test_key_sources_skip_silently_without_key_and_parse_with_key(monkeypatch):
    calls = http(monkeypatch, {
        'api.flickr.com': {'stat': 'ok', 'photos': {'photo': [
            {'id': '123', 'owner': '99@N01', 'ownername': 'Ann', 'title': 'Lake cabin', 'license': '4',
             'url_l': 'https://live.staticflickr.com/1/123_l.jpg', 'width_l': 1024, 'height_l': 768}]}},
        'api.tumblr.com': {'response': [
            {'type': 'photo', 'post_url': 'https://cabins.tumblr.com/post/1', 'blog_name': 'cabins', 'summary': 'Cabin',
             'tags': ['cabin'], 'photos': [{'original_size': {'url': 'https://64.media.tumblr.com/a.jpg', 'width': 500, 'height': 700}}]},
            {'type': 'text', 'post_url': 'https://cabins.tumblr.com/post/2'},
            {'type': 'photo', 'is_nsfw': True, 'post_url': 'https://cabins.tumblr.com/post/3',
             'photos': [{'original_size': {'url': 'https://64.media.tumblr.com/b.jpg'}}]}]}})
    assert sources.flickr_search('cabin', api_key='') == [] and sources.tumblr_search('cabin', api_key='') == []
    assert calls == []
    flickr = sources.flickr_search('cabin', 30, 30, api_key='k')
    assert parse_qs(urlsplit(calls[0]).query)['safe_search'] == ['1'] and parse_qs(urlsplit(calls[0]).query)['page'] == ['2']
    assert flickr[0]['source_url'] == 'https://www.flickr.com/photos/99@N01/123' and flickr[0]['license'] == 'CC BY 2.0'
    tumblr = sources.tumblr_search('cabin', api_key='k')
    assert [t['source_url'] for t in tumblr] == ['https://cabins.tumblr.com/post/1']
    settings(monkeypatch, tumblr_api_key='', flickr_api_key='')
    assert sources.enabled_sources(sources.source_settings()) == []


def test_search_interleaves_dedupes_and_survives_dead_sources(monkeypatch):
    settings(monkeypatch, web=True, reddit=True, bluesky=True, mastodon=True, source_timeout_s=5)
    web = [{'title': 'w1', 'source_url': 'https://blog.example/a', 'image_url': 'https://i.redd.it/nook1.jpg'},
           {'title': 'w2', 'source_url': 'https://blog.example/b', 'image_url': 'https://blog.example/b.jpg'}]
    monkeypatch.setattr(sources, 'web_search', lambda q, l, o: web)
    monkeypatch.setattr(sources, 'reddit_search', lambda q, l, o: [
        {'title': 'r1', 'source_url': 'https://www.reddit.com/r/a/1', 'image_url': 'https://i.redd.it/nook1.jpg'},
        {'title': 'r2', 'source_url': 'https://www.reddit.com/r/a/2', 'image_url': 'https://i.redd.it/nook2.jpg'}])
    monkeypatch.setattr(sources, 'bluesky_search', lambda q, l, o: (_ for _ in ()).throw(ValueError('down')))
    release = threading.Event()
    monkeypatch.setattr(sources, 'mastodon_search', lambda *a: release.wait(10) and [])
    real_wait = sources.wait
    monkeypatch.setattr(sources, 'wait', lambda fs, timeout: real_wait(fs, 0.3))
    started = time.monotonic()
    try:
        found = sources.search('cozy', 30)
    finally:
        release.set()
    assert time.monotonic() - started < 3
    assert [f['title'] for f in found] == ['w1', 'w2', 'r2']  # r1 shares w1's image.
    assert len(sources.search('cozy', 2)) == 2


def test_search_raises_only_when_every_source_fails(monkeypatch):
    settings(monkeypatch, web=True, reddit=True)
    monkeypatch.setattr(sources, 'web_search', lambda *a: (_ for _ in ()).throw(ValueError('down')))
    monkeypatch.setattr(sources, 'reddit_search', lambda *a: [])
    assert sources.search('cozy') == []
    monkeypatch.setattr(sources, 'reddit_search', lambda *a: (_ for _ in ()).throw(ValueError('down')))
    with pytest.raises(ValueError):
        sources.search('cozy')
    settings(monkeypatch)
    with pytest.raises(ValueError, match='No Mood Board'):
        sources.search('cozy')


def test_settings_api_validates_and_redacts(client):
    r = client.put('/api/settings/moodboard_sources', json={'reddit': False, 'flickr_api_key': 'secret',
                                                            'mastodon_instance': ' Pixelfed.Social '})
    assert r.status_code == 200, r.text
    assert r.json()['flickr_api_key'] == '********' and r.json()['mastodon_instance'] == 'pixelfed.social'
    assert client.get('/api/settings').json()['moodboard_sources']['flickr_api_key'] == '********'
    r = client.put('/api/settings/moodboard_sources', json={'flickr_api_key': '********', 'reddit': True})
    from bt.db import get_settings
    assert get_settings()['moodboard_sources']['flickr_api_key'] == 'secret'
    for bad in ({'reddit': 'yes'}, {'mastodon_instance': '127.0.0.1'}, {'source_timeout_s': 1}, {'pinterest': True}):
        assert client.put('/api/settings/moodboard_sources', json=bad).status_code == 400
