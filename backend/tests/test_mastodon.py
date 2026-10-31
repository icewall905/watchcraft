"""Public Mastodon API response and endpoint contracts."""
import json

import pytest

from bt.hub.providers import _mastodon_status, mastodon


def status(**changes):
    # Standard fields from the documented Status and MediaAttachment entities.
    return {'id': '109510769373674155', 'visibility': 'public',
            'url': 'https://social.example/@alice/109510769373674155',
            'content': '<p>A funny clip <a rel="tag">#memes</a></p>',
            'created_at': '2022-12-14T20:53:54.000Z', 'reblog': None,
            'in_reply_to_id': None, 'account': {'acct': 'alice', 'display_name': 'Alice'},
            'tags': [{'name': 'memes'}], 'media_attachments': [
                {'type': 'image', 'url': 'https://cdn.example/image.jpg', 'preview_url': 'https://cdn.example/image-small.jpg'},
                {'type': 'gifv', 'url': 'https://cdn.example/clip.mp4', 'preview_url': 'https://cdn.example/poster.jpg', 'description': 'Looping clip'}],
            **changes}


def test_playable_media_and_poster_are_separate():
    item = _mastodon_status(status(), 'social.example')
    assert item['image'] == 'https://cdn.example/poster.jpg'
    assert item['meta']['media']['url'] == 'https://cdn.example/clip.mp4'
    assert item['meta']['media']['type'] == 'gifv'
    no_poster = status(media_attachments=[{'type': 'video', 'url': 'https://cdn.example/a.mp4'}])
    assert _mastodon_status(no_poster, 'social.example')['image'] == ''


@pytest.mark.parametrize('visibility', ['unlisted', 'private', 'direct', None])
def test_only_public_statuses(visibility):
    assert _mastodon_status(status(visibility=visibility), 'social.example') is None


@pytest.mark.parametrize('source', ['https://social.example/tags/memes', 'https://social.example/tag/memes',
                                   'https://social.example/api/v1/timelines/tag/memes'])
def test_explicit_hashtag_is_not_broadened(monkeypatch, source):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs['kinds'] == ('application/json',)
        return json.dumps([status(), status()]).encode()
    monkeypatch.setattr('bt.hub.providers.curation.fetch', fetch)
    items = mastodon(source, 'unrelated topic')
    assert calls == ['https://social.example/api/v1/timelines/tag/memes?limit=30']
    assert len(items) == 1
    assert items[0]['external_id'] == 'mstdn:social.example:109510769373674155'


def test_instance_query_and_public_timeline(monkeypatch):
    calls = []
    monkeypatch.setattr('bt.hub.providers.curation.fetch', lambda url, **kwargs: calls.append(url) or b'[]')
    mastodon('https://social.example', 'linux meme')
    assert calls == [f'https://social.example/api/v1/timelines/tag/{tag}?limit=30' for tag in ('linuxmeme', 'linux', 'meme')]
    calls.clear()
    mastodon('https://social.example')
    assert calls == ['https://social.example/api/v1/timelines/public?limit=30']


@pytest.mark.parametrize('url', ['http://social.example', 'https://user:secret@social.example',
                              'https://social.example:8443', 'https://social.example/@alice',
                              'https://social.example/tags/a%2Fb'])
def test_invalid_sources_never_fetch(monkeypatch, url):
    def fail(*args, **kwargs):
        pytest.fail('Invalid source reached network')
    monkeypatch.setattr('bt.hub.providers.curation.fetch', fail)
    with pytest.raises(ValueError):
        mastodon(url)


@pytest.mark.parametrize('code', [401, 403])
def test_login_required_errors_are_actionable(monkeypatch, code):
    def fetch(*args, **kwargs):
        raise ValueError(f'Source returned HTTP {code}.')
    monkeypatch.setattr('bt.hub.providers.curation.fetch', fetch)
    with pytest.raises(ValueError, match='requires login'):
        mastodon('https://social.example')
