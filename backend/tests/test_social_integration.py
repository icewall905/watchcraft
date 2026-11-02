"""Social settings, public poll reuse and local capture integration."""
from test_curation import client  # isolated DB/API fixture
from bt.hub import providers, social_fetch, capture
from bt import curation
from bt.db import session
from bt.hub.models import ContentItem, ItemState, CaptureJob
import pytest


def test_host_retry_after_blocks_extra_requests(monkeypatch):
    social_fetch._hosts.clear()
    calls = []
    def fail(url, **kwargs):
        calls.append(url)
        raise curation.FetchError(429, '120')
    monkeypatch.setattr(curation, 'fetch', fail)
    with pytest.raises(curation.FetchError):
        social_fetch.fetch('https://social.example/api/a')
    with pytest.raises(ValueError, match='requested a pause'):
        social_fetch.fetch('https://social.example/api/b')
    assert len(calls) == 1
    social_fetch._hosts.clear()


def test_community_settings_are_validated_and_owned(client, monkeypatch):
    src = client.post('/api/hub/sources', json={'provider': 'lemmy', 'url': '!selfhosted@lemmy.world'}).json()
    assert src['url'] == 'https://lemmy.world/c/selfhosted' and src['configurable']
    monkeypatch.setattr(providers.PROVIDERS['lemmy'], 'fetch', lambda url, q, config: [
        {'url': 'https://lemmy.world/post/1', 'title': 'Local servers', 'type': config.get('item_type', 'news')}])
    path = f"/api/hub/sources/{src['id']}/watcher"
    assert client.put(path, json={'config': {'limit': 31}}).status_code == 422
    assert client.put(path, json={'config': {'allow_rss': 'yes'}}).status_code == 422
    response = client.put(path, json={'config': {'sort': 'TopDay', 'item_type': 'meme', 'limit': 10}}).json()
    assert response['saved']
    assert client.get(path).json()['config'] == {'sort': 'TopDay', 'item_type': 'meme', 'limit': 10}
    other = client.post('/api/profiles', json={'name': 'Other'}).json()
    assert client.get(path, headers={'X-Profile-Id': str(other['id'])}).status_code == 404
    assert client.put(path, json={'config': {}}, headers={'X-Profile-Id': str(other['id'])}).status_code == 404


def test_social_capture_preserves_api_body(client, monkeypatch):
    monkeypatch.setattr(capture.ai, 'enabled', lambda: False)
    def no_fetch(*args, **kwargs):
        pytest.fail('Social body must not be replaced with the discussion HTML')
    monkeypatch.setattr(curation, 'fetch', no_fetch)
    with session() as s:
        item = ContentItem(url='https://lemmy.world/post/1', provider='lemmy', type='news', title='Servers',
                           body='A public discussion.', meta={'social_post': True})
        s.add(item); s.flush(); iid = item.id
        s.add(ItemState(profile_id=1, item_id=iid))
        s.add(CaptureJob(profile_id=1, item_id=iid, status='running', attempts=1, lease=123))
        s.commit()
    capture.run(1, iid, 123)
    with session() as s:
        assert s.get(ContentItem, iid).body == 'A public discussion.'
        assert s.get(CaptureJob, (1, iid)).status == 'complete'


def test_mastodon_trends_reuses_legacy_display_url_capture(client):
    from bt.hub import engine
    from sqlmodel import select
    legacy = 'https://origin.example/@alice/7'
    canonical = 'https://origin.example/users/alice/statuses/7'
    with session() as s:
        item = ContentItem(url=legacy, provider='mastodon', title='A post')
        s.add(item); s.flush(); iid = item.id
        found = engine.upsert(s, {'url': canonical, 'title': 'A post',
            'meta': {'display_url': legacy, 'origin_uri': canonical}}, 'mastodon_trends')
        assert found.id == iid
        assert len(s.exec(select(ContentItem)).all()) == 1
