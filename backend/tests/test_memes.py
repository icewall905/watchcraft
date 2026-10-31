"""Meme Radar: dedup identity, template grouping, counted trend labels, and API boundaries."""
import io
import os
import random
import tempfile
import time

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-memes-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, session
from bt.hub import capture, memes
from bt.hub.models import ContentItem, Curator, CuratorDiscovery, ItemState, MemeExample, MemeFormat
from bt.hub.providers import _mastodon_status, _mastodon_tags, mastodon
from bt.main import app
from bt.models import Profile

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


def _images():
    """Five near-identical images (small corner marks, re-encoded JPEGs) plus a distinct checkerboard."""
    from PIL import Image, ImageDraw
    rnd = random.Random(42)
    base = Image.new('RGB', (240, 240), 'white')
    dr = ImageDraw.Draw(base)
    for _ in range(60):
        x0, y0 = rnd.randint(0, 200), rnd.randint(0, 200)
        dr.rectangle((x0, y0, x0 + rnd.randint(10, 40), y0 + rnd.randint(10, 40)), fill=(rnd.randint(0, 255),) * 3)
    out = []
    for k in range(5):
        v = base.copy()
        mark = ImageDraw.Draw(v)
        mark.rectangle((k * 5, k * 5, k * 5 + 7, k * 5 + 7), fill=(255, 0, 0))
        buf = io.BytesIO(); v.save(buf, 'JPEG', quality=95)
        out.append(buf.getvalue())
    checker = Image.new('RGB', (240, 240))
    cd = ImageDraw.Draw(checker)
    for gx in range(15):
        for gy in range(15):
            cd.rectangle((gx * 16, gy * 16, gx * 16 + 15, gy * 16 + 15),
                         fill=(0, 0, 0) if (gx + gy) % 2 else (255, 255, 255))
    buf = io.BytesIO(); checker.save(buf, 'JPEG', quality=95)
    out.append(buf.getvalue())
    return out


def _seed_meme(url, title, image, community, first_seen=None):
    with session() as s:
        cur = s.exec(select(Curator).where(Curator.profile_id == 1)).first()
        if not cur:
            cur = Curator(profile_id=1, name='Meme Tracker', next_run=0)
            s.add(cur); s.flush()
        it = ContentItem(url=url, type='meme', title=title, summary=title, provider='mastodon',
                         meta={'community': community, 'language': 'en', 'platform': 'mastodon'})
        if first_seen:
            it.first_seen = first_seen
        s.add(it); s.flush()
        s.add(CuratorDiscovery(curator_id=cur.id, item_id=it.id, profile_id=1, query='memes', score=60))
        s.commit()
        if image:
            s.add(ItemState(profile_id=1, item_id=it.id))  # visible() for save_asset
            s.commit()
            capture.save_asset(1, it.id, 'image', 'https://cdn.test/x.jpg', image, 'image/jpeg')
        return it.id


def _analyze_all():
    with session() as s:
        n = memes.analyze_pending(s, limit=50)
        s.commit()
    return n


# ---------- provider parsing (offline fixtures)

def test_mastodon_status_mapping_skips_replies_and_keeps_observed_counts():
    status = {'visibility': 'public', 'url': 'https://m.test/@a/1', 'content': '<p>first line<br>second</p>', 'created_at': '2026-10-30T12:00:00.000Z',
              'account': {'acct': 'a@m.test', 'display_name': 'A'}, 'language': 'en', 'sensitive': True,
              'spoiler_text': 'watch out', 'replies_count': 1, 'reblogs_count': 2, 'favourites_count': 3,
              'tags': [{'name': 'linux'}, {'name': 'memes'}],
              'media_attachments': [{'type': 'image', 'mime_type': 'image/png', 'url': 'https://cdn.test/full.png',
                                     'preview_url': 'https://cdn.test/prev.png', 'description': 'alt text'}]}
    item = _mastodon_status(status, 'm.test')
    assert item and item['type'] == 'meme' and item['url'] == status['url']
    assert item['image'] == 'https://cdn.test/prev.png'
    assert item['meta']['community'] == '#linux' and item['meta']['language'] == 'en' and item['meta']['sensitive']
    assert item['meta']['observations'] == {'replies': 1, 'reblogs': 2, 'favourites': 3}
    reply = dict(status, in_reply_to_id='99'); boost = dict(status, reblog={'id': '7'})
    assert _mastodon_status(reply, 'm.test') is None and _mastodon_status(boost, 'm.test') is None


def test_mastodon_tags_from_query_only_reshapes_words():
    tags = _mastodon_tags('linux meme')
    assert tags[0] == 'linuxmeme' and 'linux' in tags and 'meme' in tags
    assert _mastodon_tags('!!') == []


def test_mastodon_provider_rejects_non_url():
    with pytest.raises(ValueError):
        mastodon('not a url', '')


# ---------- exact dedup, perceptual grouping, formats

def test_exact_media_duplicates_are_linked_not_relisted(client):
    img = _images()[0]
    a = _seed_meme('https://m.test/@a/1', 'same post', img, '#one')
    b = _seed_meme('https://m.test/@b/2', 'same post reposted', img, '#two')
    assert _analyze_all() == 2
    with session() as s:
        ea = s.get(MemeExample, a); eb = s.get(MemeExample, b)
        assert ea.media_sha and eb.media_sha == ea.media_sha and eb.dup_of == a
    keep = client.get('/api/hub/memes/items', headers=H).json()
    assert {i['id'] for i in keep['items']} == {a}
    history = client.get('/api/hub/memes/items', headers=H, params={'view': 'history'}).json()
    assert {i['id'] for i in history['items']} == {a, b}


def test_near_duplicates_form_a_template_group(client):
    variants = _images()
    ids = [_seed_meme(f'https://m.test/@v/{k}', f'variant {k}', img, f'#{c}')
           for k, (img, c) in enumerate(zip(variants[:3], ('one', 'two', 'three')))]
    odd = _seed_meme('https://m.test/@v/9', 'unrelated checkerboard', variants[5], '#nine')
    assert _analyze_all() == 4
    with session() as s:
        examples = {i: s.get(MemeExample, i) for i in ids + [odd]}
        grouped = [examples[i].format_id for i in ids]
        assert all(grouped) and len(set(grouped)) == 1
        fmt = s.get(MemeFormat, grouped[0])
        assert fmt.kind == 'visual' and fmt.example_count == 3 and fmt.community_count == 3
        assert examples[odd].format_id is None and examples[odd].phash  # kept apart, still hashed
    formats = client.get('/api/hub/memes/formats', headers=H).json()
    assert formats['total'] == 1 and formats['formats'][0]['example_count'] == 3
    detail = client.get(f"/api/hub/memes/formats/{grouped[0]}", headers=H).json()
    assert {i['id'] for i in detail['items']} == set(ids)
    similar = client.get(f'/api/hub/memes/items/{ids[0]}/similar', headers=H).json()
    found = {i['id'] for i in similar['items']}
    assert {ids[1], ids[2]} <= found and odd not in found


def test_text_memes_dedup_by_caption_and_define_a_text_format():
    a = _seed_meme('https://m.test/@t/1', 'Nobody:  Me at 3am:  config again', None, '#txt')
    b = _seed_meme('https://m.test/@t/2', 'Nobody!!! Me at 3am... config AGAIN', None, '#txt')
    c = _seed_meme('https://m.test/@t/3', 'a completely different caption here', None, '#txt')
    assert _analyze_all() == 3
    with session() as s:
        eb = s.get(MemeExample, b)
        assert eb.dup_of == a and eb.format_id
        fmt = s.get(MemeFormat, eb.format_id)
        assert fmt.kind == 'text'
        assert s.get(MemeExample, c).dup_of is None and s.get(MemeExample, c).format_id is None


# ---------- trend labels are counted, sparse stays honest

def test_label_state_is_a_pure_function_of_counts():
    now = time.time()
    fresh = memes._days([now - d * 60 for d in range(4)] + [now - (20 + d) * 86400 for d in range(2)], now)
    st, ev, sparse = memes.label_state(fresh, 6, 3, 22)
    assert st == 'emerging' and not sparse, ev
    st, _, _ = memes.label_state(fresh, 6, 1, 22)
    assert st == 'established'  # one community never implies global popularity
    st, ev, sparse = memes.label_state(fresh, 4, 4, 1)
    assert st == 'newly_observed' and sparse  # a growing-but-small sample stays cautious
    burst = memes._days([now - d * 60 for d in range(8)] + [now - (10 + d) * 86400 for d in range(8)], now)
    st, ev, _ = memes.label_state(burst, 16, 4, 20)
    assert st == 'trending', ev
    quiet = memes._days([now - (10 + d) * 86400 for d in range(4)], now)
    st, _, _ = memes.label_state(quiet, 4, 2, 20)
    assert st == 'fading'


def test_trends_counted_from_collected_examples_with_follow_and_hide(client):
    variants = _images()
    names = ('alpha', 'beta', 'gamma', 'delta', 'epsilon')
    ids = [_seed_meme(f'https://m.test/@g/{k}', f'grouped {k}', img, f'#{c}')
           for k, (img, c) in enumerate(zip(variants[:5], names))]
    _analyze_all()
    assert client.post('/api/hub/memes/sweep', headers=H).status_code == 200
    trends = client.get('/api/hub/memes/trends', headers=H).json()['trends']
    mine = [t for t in trends if t['target_kind'] == 'format']
    assert len(mine) == 1
    t = mine[0]
    assert t['state'] in ('emerging', 'trending'), t['evidence']
    assert t['metrics']['last7'] == 5 and t['metrics']['communities'] == 5
    assert any('independent communit' in e for e in t['evidence'])
    follow = client.post(f"/api/hub/memes/trends/{t['id']}/state", json={'followed': True, 'hidden': True}, headers=H).json()
    assert follow == {'followed': True, 'hidden': True}
    after = client.get('/api/hub/memes/trends', headers=H).json()['trends']
    assert not [x for x in after if x['id'] == t['id']]          # hidden by this profile
    shown = client.get('/api/hub/memes/trends', headers=H, params={'include_hidden': True}).json()['trends']
    assert [x for x in shown if x['id'] == t['id']][0]['followed'] is True
    communities = client.get('/api/hub/memes/facets', headers=H).json()['communities']
    assert {c['key'] for c in communities} >= set(f'#{n}' for n in names)


def test_saved_and_dismissed_views_use_item_state(client):
    variants = _images()
    a = _seed_meme('https://m.test/@s/1', 'saved one', variants[5], '#s')
    b = _seed_meme('https://m.test/@s/2', 'dismissed one', variants[2], '#s')
    _analyze_all()
    client.post(f'/api/hub/items/{a}/state', json={'saved': True}, headers=H)
    client.post(f'/api/hub/items/{b}/state', json={'dismissed': True}, headers=H)
    saved = client.get('/api/hub/memes/items', headers=H, params={'view': 'saved'}).json()
    assert {i['id'] for i in saved['items']} == {a}
    examples = client.get('/api/hub/memes/items', headers=H).json()
    assert {i['id'] for i in examples['items']} == {a}
    filtered = client.get('/api/hub/memes/items', headers=H, params={'community': '#s'}).json()
    assert filtered['total'] == 1


def test_explain_without_ai_is_refused_and_with_ai_persists_separately(client, monkeypatch):
    a = _seed_meme('https://m.test/@e/1', 'explain this', _images()[5], '#e')
    _analyze_all()
    from bt.hub import ai
    monkeypatch.setattr(ai, 'enabled', lambda: False)  # the shipped default has an llm base_url
    r = client.post(f'/api/hub/memes/items/{a}/explain', json={}, headers=H)
    assert r.status_code == 422
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(ai, 'explain_meme', lambda data, similar, image=None, mime='': {
        'what_shows': 'a cat', 'why_funny': 'absurd', 'referencing': 'unknown', 'origin': 'unknown',
        'why_now': 'unknown', 'uncertain': 'its age'})
    out = client.post(f'/api/hub/memes/items/{a}/explain', json={}, headers=H).json()
    assert out['ai'] and out['explanation']['origin'] == 'unknown'
    detail = client.get(f'/api/hub/items/{a}', headers=H).json()
    assert detail['enrichment']['meme_explain']['what_shows'] == 'a cat'
    assert detail['summary'] == 'explain this'  # source facts untouched by AI output
    calls = []
    monkeypatch.setattr(ai, 'explain_meme', lambda *a, **k: calls.append(1) or {})
    cached = client.post(f'/api/hub/memes/items/{a}/explain', json={}, headers=H).json()
    assert cached['explanation']['what_shows'] == 'a cat' and not calls  # second call returns the cached one


def test_meme_api_is_profile_scoped(client):
    _seed_meme('https://m.test/@p/1', 'profile one meme', _images()[5], '#p')
    _analyze_all()
    with session() as s:
        other = Profile(name='Other'); s.add(other); s.commit(); s.refresh(other)
    body = client.get('/api/hub/memes/items', headers={'X-Profile-Id': str(other.id)}).json()
    assert body['total'] == 0 and client.get('/api/hub/memes/items', headers=H).json()['total'] == 1


def test_child_profiles_blocked_from_meme_routes(client):
    _seed_meme('https://m.test/@p/2', 'household meme', _images()[4], '#p')
    _analyze_all()
    assert client.patch('/api/profiles/1', json={'pin': '4826'}).status_code == 200
    child = client.post('/api/profiles', json={'name': 'Kid', 'role': 'child'}).json()
    assert client.get('/api/hub/memes/items', headers={'X-Profile-Id': str(child['id'])}).status_code == 403
    assert client.get('/api/hub/memes/trends', headers={'X-Profile-Id': str(child['id'])}).status_code == 403


def test_meme_template_is_an_ordinary_curator(client):
    templates = client.get('/api/hub/curators/templates', headers=H).json()
    meme = next(t for t in templates if t['key'] == 'meme-radar')
    assert meme['provider_keys'] == ['mastodon'] and meme['content_types'] == ['meme']
    src = client.post('/api/hub/sources', json={'provider': 'mastodon', 'url': 'https://mastodon.world'}, headers=H).json()
    cur = client.post('/api/hub/curators', json={'name': meme['name'], 'queries': meme['queries'],
                                                 'source_ids': [src['id']], 'content_types': meme['content_types'],
                                                 'interval_hours': 12}, headers=H).json()
    assert cur['content_types'] == ['meme']
    assert client.delete(f"/api/hub/curators/{cur['id']}", headers=H).status_code == 200
    assert client.delete(f"/api/hub/sources/{src['id']}", headers=H).status_code == 200


def test_trend_window_and_weekly_baseline(client):
    now = time.time()
    for k, days in enumerate([0, 1, 20, 21, 22, 23, 40]):
        _seed_meme(f'https://m.test/@window/{k}', f'unique caption {k}', None, '#window', now - days * 86400)
    _analyze_all()
    client.post('/api/hub/memes/sweep', headers=H)
    t = next(t for t in client.get('/api/hub/memes/trends', headers=H).json()['trends'] if t['target_key'] == '#window')
    assert t['metrics']['last7'] == 2
    assert t['metrics']['samples'] == 6
    assert t['metrics']['baseline_week'] == 1.75
    assert sum(o['count'] for o in t['observations']) == 6


def test_explanation_receives_context_and_multiple_related_titles(client, monkeypatch):
    from bt.hub import ai
    ids = [_seed_meme(f'https://m.test/@context/{k}', f'caption {k}', img, '#context')
           for k, img in enumerate(_images()[:3])]
    _analyze_all()
    prompts = []
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(ai, '_call', lambda system, user, schema, **kw: prompts.append(user) or {'origin': 'unknown'})
    response = client.post(f'/api/hub/memes/items/{ids[0]}/explain', json={}, headers=H)
    assert response.status_code == 200, response.text
    assert all(value in prompts[0] for value in ['caption 0', 'caption 1', 'caption 2', '#context', 'similar_examples'])


def test_sensitive_metadata_reaches_feed(client):
    iid = _seed_meme('https://m.test/@safety/1', 'sensitive caption', _images()[0], '#safe')
    with session() as s:
        it = s.get(ContentItem, iid)
        it.meta = {**it.meta, 'sensitive': True, 'spoiler': 'Content warning'}
        s.add(it); s.commit()
    _analyze_all()
    meta = client.get('/api/hub/memes/items', headers=H).json()['items'][0]['meme']
    assert meta['sensitive'] and meta['spoiler'] == 'Content warning'


def test_duplicate_stays_visible_when_original_belongs_to_another_profile(client):
    img = _images()[0]
    a = _seed_meme('https://m.test/@dedup/1', 'original', img, '#one')
    b = _seed_meme('https://m.test/@dedup/2', 'repost', img, '#two')
    _analyze_all()
    with session() as s:
        other = Profile(name='Second'); s.add(other); s.flush()
        s.add(ItemState(profile_id=other.id, item_id=b)); s.commit()
        pid = other.id
    out = client.get('/api/hub/memes/items', headers={'X-Profile-Id': str(pid)}).json()
    assert [it['id'] for it in out['items']] == [b]
    assert client.get('/api/hub/memes/items', headers=H).json()['items'][0]['id'] == a
