"""Humor eligibility and stable keyset scrolling against captured fixture images."""
import time
from sqlmodel import select
from test_memes import H, _analyze_all, _images, _seed_meme, client  # noqa: F401
from bt.db import session
from bt.hub.models import ContentItem, MemeExample


def _unduplicate():
    with session() as s:
        for ex in s.exec(select(MemeExample)).all():
            ex.dup_of = None
            s.add(ex)
        s.commit()


def test_clean_feed_excludes_news_promos_text_and_uncaptured_preserves_history(client):
    image = _images()[0]
    funny = _seed_meme('https://m.test/1', 'A simple pun', image, 'r/me_irl')
    news = _seed_meme('https://m.test/2', 'Home Assistant tutorial', image, '#homeassistant')
    promo = _seed_meme('https://m.test/3', 'Comedy show', image, '#humor')
    text = _seed_meme('https://m.test/4', 'Text #memes', None, '#memes')
    pending = _seed_meme('https://m.test/5', 'Uncaptured', None, '#memes')
    _analyze_all()
    _unduplicate()
    with session() as s:
        for iid in (news, promo):
            it = s.get(ContentItem, iid)
            it.meta = {**it.meta, 'tags': []}
            if iid == promo: it.body = 'Weitere Informationen: https://kabarett.test/events/show'
            s.add(it)
        it = s.get(ContentItem, pending)
        it.image = 'https://cdn.test/pending.jpg'
        s.add(it)
        s.commit()
    feed = client.get('/api/hub/memes/items', headers=H).json()
    assert feed['total'] == 1 and [x['id'] for x in feed['items']] == [funny]
    history = client.get('/api/hub/memes/items', headers=H, params={'view': 'history'}).json()
    assert {x['id'] for x in history['items']} == {funny, news, promo, text, pending}
    client.post(f'/api/hub/items/{news}/state', headers=H, json={'saved': True})
    assert client.get('/api/hub/memes/items', headers=H, params={'view': 'saved'}).json()['items'][0]['id'] == news


def test_cursor_survives_hiding_and_new_arrivals(client):
    image, now = _images()[0], time.time()
    ids = [_seed_meme(f'https://m.test/page/{i}', f'Funny {i}', image, '#memes', first_seen=now-i) for i in range(5)]
    _analyze_all()
    _unduplicate()
    first = client.get('/api/hub/memes/items', headers=H, params={'limit': 2}).json()
    assert [x['id'] for x in first['items']] == ids[:2]
    assert first['has_more'] and first['next_cursor']
    client.post(f'/api/hub/items/{ids[0]}/state', headers=H, json={'dismissed': True})
    new = _seed_meme('https://m.test/page/new', 'New arrival', _images()[5], '#memes', first_seen=now+1)
    _analyze_all()
    second = client.get('/api/hub/memes/items', headers=H, params={'limit': 2, 'cursor': first['next_cursor']}).json()
    assert [x['id'] for x in second['items']] == ids[2:4]
    third = client.get('/api/hub/memes/items', headers=H, params={'limit': 2, 'cursor': second['next_cursor']}).json()
    assert [x['id'] for x in third['items']] == ids[4:]
    assert not third['has_more'] and third['next_cursor'] is None
    assert new not in [x['id'] for x in second['items']+third['items']]
    for cursor in ('bad','nan:1','-1:2','1:0','1:2:3'):
        assert client.get('/api/hub/memes/items', headers=H, params={'cursor':cursor}).status_code == 400


def test_refresh_only_runs_owned_enabled_humor_collectors_and_respects_cooldown(client, monkeypatch):
    from bt.hub import engine
    from bt.hub.models import Curator
    from bt.models import Profile
    called = []
    monkeypatch.setattr(engine, 'start', lambda cid: called.append(cid))
    with session() as s:
        other = Profile(name='Other')
        s.add(other); s.flush()
        humor = Curator(profile_id=1, name='Meme Tracker', content_types=['meme'], queries=['memes'])
        recent = Curator(profile_id=1, name='Funny', content_types=['meme'], last_run=time.time())
        busy = Curator(profile_id=1, name='Jokes', content_types=['meme'], last_status='running', next_run=time.time()+600)
        for c in (humor, recent, busy, Curator(profile_id=1, name='Memes disabled', content_types=['meme'], enabled=False),
                  Curator(profile_id=other.id, name='Memes', content_types=['meme']),
                  Curator(profile_id=1, name='Video Moments', content_types=['meme'], queries=['news today'])):
            s.add(c)
        s.commit(); s.refresh(humor)
        cid = humor.id
    result = client.post('/api/hub/memes/refresh', headers=H).json()
    assert (result['started'],result['running'],result['recent']) == (1,1,1)
    assert called == [cid]
