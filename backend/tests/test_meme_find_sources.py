"""Meme Tracker 'Find sources', Reddit reads through the Common Room keys, and clearing a run problem."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import curation, db
from bt.commonroom import reddit_oauth
from bt.commonroom.adapters import reddit as cr_reddit
from bt.db import init_db
from bt.hub import ai, meme_sources, providers
from bt.main import app

H = {'X-Profile-Id': '1'}


@pytest.fixture
def client():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield TestClient(app, raise_server_exceptions=True)
    SQLModel.metadata.drop_all(db.engine)
    init_db()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(reddit_oauth, 'get_settings', lambda: {'commonroom': {'reddit_client_id': 'c', 'reddit_secret': 's'}})
    monkeypatch.setattr(reddit_oauth, '_request_token', lambda c, s: {'access_token': 'tok', 'expires_in': 86400})
    monkeypatch.setattr(reddit_oauth.time, 'sleep', lambda s: None)
    reddit_oauth.reset()
    yield
    reddit_oauth.reset()


def test_anonymous_reddit_block_says_to_add_keys(monkeypatch):
    def blocked(url, **kw):
        raise curation.FetchError(404)
    monkeypatch.setattr(reddit_oauth, 'get_settings', lambda: {})
    monkeypatch.setattr(curation, 'fetch', blocked)
    with pytest.raises(ValueError, match='Reddit API keys'):
        providers.reddit('r/worldnews')
    with pytest.raises(ValueError, match='Reddit API keys'):
        providers.reddit_memes('r/memes')


def test_reddit_reads_through_the_common_room_keys(keys, monkeypatch):
    asked = []

    def listing(url, headers=None, **kw):
        asked.append(url)
        assert headers['Authorization'] == 'bearer tok'
        return {'data': {'children': [
            {'data': {'title': 'Big story', 'url': 'https://news.example/a', 'permalink': '/r/worldnews/comments/1/x/', 'created_utc': 1.0}},
            {'data': {'title': 'Self', 'url': 'https://www.reddit.com/r/worldnews/comments/2/', 'is_self': True}},
            {'data': {'title': 'Funny', 'url': 'https://i.redd.it/a.png', 'permalink': '/r/worldnews/comments/3/f/', 'author': 'bob'}},
            {'data': {'title': 'Lewd', 'url': 'https://i.redd.it/b.png', 'permalink': '/r/worldnews/comments/4/', 'over_18': True}},
        ]}}
    monkeypatch.setattr(cr_reddit, 'get_json', listing)
    monkeypatch.setattr(curation, 'fetch', lambda *a, **k: pytest.fail('anonymous RSS must not be read'))
    news = providers.reddit('https://www.reddit.com/r/worldnews')
    assert [n['url'] for n in news] == ['https://news.example/a']
    assert news[0]['meta']['discussion_url'] == 'https://www.reddit.com/r/worldnews/comments/1/x/'
    assert asked[0].startswith('https://oauth.reddit.com/r/worldnews/top?') and 't=day' in asked[0]
    memes = providers.reddit_memes('https://www.reddit.com/r/worldnews/new/')
    assert [m['image'] for m in memes] == ['https://i.redd.it/a.png'] and memes[0]['author'] == 'bob'
    assert asked[1].startswith('https://oauth.reddit.com/r/worldnews/new?')


def test_reddit_is_offered_to_the_ai_only_with_keys(keys, monkeypatch):
    assert 'reddit_memes' in meme_sources.platforms()
    reddit_oauth.reset()
    monkeypatch.setattr(reddit_oauth, 'get_settings', lambda: {})
    assert 'reddit_memes' not in meme_sources.platforms()


def _meme_curator(client, **kw):
    return client.post('/api/hub/curators', json={'name': 'Meme Tracker', 'queries': ['memes'], 'content_types': ['meme'],
                                                  'interval_hours': 12, **kw}, headers=H).json()


def test_find_sources_adds_only_communities_that_return_memes(client, monkeypatch):
    cur = _meme_curator(client)
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(ai, '_call', lambda *a, **k: {'sources': [
        {'platform': 'lemmy', 'locator': '!memes@lemmy.world', 'name': 'Lemmy memes'},
        {'platform': 'lemmy', 'locator': '!ghost@lemmy.world'},
        {'platform': 'mastodon', 'locator': 'https://mastodon.social/tags/memes'},
        {'platform': 'tiktok', 'locator': 'https://www.tiktok.com/@x'},  # not a meme platform
    ]})

    def validate(provider, url):
        if 'ghost' in url:
            raise ValueError('No image memes there right now.')
        return 3
    monkeypatch.setattr(meme_sources, 'validate', validate)
    r = client.post(f'/api/hub/curators/{cur["id"]}/find-sources', headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [a['provider'] for a in body['added']] == ['lemmy', 'mastodon']
    assert [k['url'] for k in body['skipped']] == ['!ghost@lemmy.world']
    detail = client.get(f'/api/hub/curators/{cur["id"]}', headers=H).json()
    ids = {a['id'] for a in body['added']}
    assert ids <= set(detail['source_ids'])
    lemmy = next(s for s in client.get('/api/hub/sources', headers=H).json() if s['provider'] == 'lemmy')
    assert lemmy['config'].get('item_type') == 'meme'
    # A second pass does not add the same communities again.
    again = client.post(f'/api/hub/curators/{cur["id"]}/find-sources', headers=H).json()
    assert again['added'] == []


def test_find_sources_is_for_meme_curators_and_needs_ai(client, monkeypatch):
    news = client.post('/api/hub/curators', json={'name': 'News', 'queries': ['x'], 'interval_hours': 12}, headers=H).json()
    assert client.post(f'/api/hub/curators/{news["id"]}/find-sources', headers=H).status_code == 422
    cur = _meme_curator(client)
    monkeypatch.setattr(ai, 'enabled', lambda: False)
    r = client.post(f'/api/hub/curators/{cur["id"]}/find-sources', headers=H)
    assert r.status_code == 422 and 'AI' in r.json()['detail']


def test_clear_error_resets_the_last_run_problem(client):
    from bt.db import session
    from bt.hub.models import Curator
    cur = _meme_curator(client)
    with session() as s:
        c = s.get(Curator, cur['id']); c.error = 'Source returned HTTP 404.'; s.add(c); s.commit()
    r = client.post(f'/api/hub/curators/{cur["id"]}/clear-error', headers=H)
    assert r.status_code == 200 and r.json()['error'] == ''
