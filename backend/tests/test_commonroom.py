"""Common Room: threads, harvest + backfill ladder, curation, New/Deep feed, teaching, kid gate."""
import time

import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from test_master_api import other_profile
from bt.commonroom import api as cr_api, assets, curate, discover, harvest, kids, scheduler, store
from bt.commonroom.adapters import Adapter, Page
from bt.commonroom.models import CRCandidate, CRPick, CRPost, CRSource, CRThread
from bt.db import session
from bt.models import Profile

CALLS = []


def post(n, sort='new', **kw):
    return {'platform': 'fake', 'community': 'c/one', 'external_id': f'{sort}{n}',
            'url': f'https://fake.example/p/{sort}/{n}', 'title': f'Post {sort} {n}', 'body': f'Body {n}',
            'author': 'alice', 'score': 10, 'comments': 2, 'published_at': time.time() - n * 60, **kw}


def fetch(locator, sort, cursor):
    CALLS.append((locator, sort, cursor))
    if sort == 'new':
        return Page([post(i) for i in range(3)], '')
    if cursor == '':
        return Page([post(i, sort) for i in range(10, 12)], 'p2')
    if cursor == 'p2':
        return Page([post(i, sort) for i in range(20, 22)], '')
    return Page([], '')


def parse(loc):
    if not loc.startswith('c/'):
        raise ValueError('Use c/name.')
    return loc


FAKE = Adapter(key='fake', label='Fake', parse=parse, fetch=fetch, sorts=('new', 'top_week'),
               comments=lambda p: [{'id': '1', 'author': 'bob', 'body': 'hi', 'score': 1, 'published_at': 0,
                                    'children': [{'id': '2', 'author': 'eve', 'body': 'yo', 'score': 0,
                                                  'published_at': 0, 'children': []}]}])


@pytest.fixture(autouse=True)
def fake_platform(monkeypatch):
    CALLS.clear()
    for mod in (harvest, discover, cr_api, assets):
        monkeypatch.setattr(mod, 'ADAPTERS', {'fake': FAKE})
    monkeypatch.setattr(curate.semantic, 'available', lambda: (False, 'off'))
    scheduler._pruned[0] = time.time()
    monkeypatch.setattr(scheduler, 'MORE_GAP_S', -1)
    yield


def thread(client, headers=None, **kw):
    r = client.post('/api/hub/commonroom/threads', json={'name': 'Homelab', 'interests': 'self-hosting',
                                                         'strictness': 'loose', **kw}, headers=headers or {})
    assert r.status_code == 200, r.text
    t = r.json()
    r = client.post(f"/api/hub/commonroom/threads/{t['id']}/sources", json={'platform': 'fake', 'locator': 'c/one'},
                    headers=headers or {})
    assert r.status_code == 200, r.text
    return t


def run_all():
    while scheduler.dispatch_due():
        pass


def test_thread_harvests_curates_and_feeds_new_then_deep(client):
    t = thread(client)
    assert client.post(f"/api/hub/commonroom/threads/{t['id']}/sources",
                       json={'platform': 'fake', 'locator': 'bad'}).status_code == 400
    run_all()
    with session() as s:
        assert len(s.exec(select(CRPick)).all()) >= 3
    b = client.post('/api/hub/commonroom/visit', json={'thread': 'all'}).json()['boundary']
    new = client.get('/api/hub/commonroom/feed', params={'section': 'new', 'boundary': b}).json()
    assert {p['title'] for p in new['posts']} >= {'Post new 0', 'Post new 1', 'Post new 2'}
    assert new['posts'][0]['thread_name'] == 'Homelab'
    deep = client.get('/api/hub/commonroom/feed', params={'section': 'deep', 'boundary': b}).json()
    # The fake source is walked to its end by now: the thread rests instead of re-running.
    assert deep['has_more'] is False and deep['building'] is False
    with session() as s:
        src = s.exec(select(CRSource)).first()
        src.exhausted = False
        s.add(src)
        s.commit()
    deep = client.get('/api/hub/commonroom/feed', params={'section': 'deep', 'boundary': b}).json()
    assert deep['building'] is True
    with session() as s:
        assert s.get(CRThread, t['id']).want_more is True
    assert client.get('/api/hub/counts').json()['commonroom'] == 0  # visiting caught up


def test_backfill_walks_pages_then_ladder_then_stops(client):
    sid = store.ensure_source('fake', 'c/one')
    for _ in range(4):
        with session() as s:
            src = s.get(CRSource, sid)
            s.expunge(src)
        harvest.backfill(src)
    assert [c for c in CALLS] == [('c/one', 'new', ''), ('c/one', 'top_week', ''), ('c/one', 'top_week', 'p2')]
    with session() as s:
        assert s.get(CRSource, sid).exhausted is True
    assert len(CALLS) == 3  # an exhausted source is never fetched again


def test_found_while_scrolling_joins_end_of_deep(client):
    t = thread(client)
    with session() as s:
        thr = s.get(CRThread, t['id'])
        thr.want_more = False  # first run: fresh posts only
        s.add(thr)
        s.commit()
    run_all()
    b = client.post('/api/hub/commonroom/visit', json={'thread': str(t['id'])}).json()['boundary']
    # Pretend everything so far was picked before the boundary (older pool).
    with session() as s:
        for p in s.exec(select(CRPick)).all():
            p.picked_at = b - 10
            s.add(p)
        s.commit()
    first = client.get('/api/hub/commonroom/feed', params={'thread': t['id'], 'section': 'deep'}).json()
    titles = [p['title'] for p in first['posts']]
    with session() as s:
        thr = s.get(CRThread, t['id'])
        thr.want_more, thr.next_run = True, time.time() + 999
        s.add(thr)
        s.commit()
    time.sleep(0.01)
    run_all()  # backfill finds older posts while the user scrolls
    again = client.get('/api/hub/commonroom/feed', params={'thread': t['id'], 'section': 'deep'}).json()
    later = [p['title'] for p in again['posts']]
    assert later[:len(titles)] == titles and len(later) > len(titles)
    assert any(x.startswith('Post top_week') for x in later[len(titles):])


def test_feedback_hides_teaches_and_blocks(client):
    t = thread(client)
    run_all()
    client.post('/api/hub/commonroom/visit', json={'thread': 'all'})
    posts = client.get('/api/hub/commonroom/feed', params={'section': 'new', 'boundary': 0}).json()['posts']
    target = posts[0]
    r = client.put(f"/api/hub/commonroom/picks/{target['pick_id']}/feedback",
                   json={'verdict': 'down', 'reasons': ['off-topic', 'nonsense'], 'note': 'not homelab'})
    assert r.status_code == 200
    learned = client.get(f"/api/hub/commonroom/threads/{t['id']}/learned").json()
    assert learned['down'] == 1 and 'not homelab' in learned['lessons'][0] and 'nonsense' not in learned['lessons'][0]
    with session() as s:
        thr = s.get(CRThread, t['id'])
        from bt.commonroom import teach
        assert 'not homelab' in teach.prompt(s, thr)
    assert client.post('/api/hub/commonroom/blocks', json={'kind': 'author', 'value': 'Alice'}).status_code == 200
    with session() as s:
        assert all(p.hidden for p in s.exec(select(CRPick)).all())


def test_comments_are_fetched_and_cached(client):
    thread(client)
    run_all()
    pid = client.get('/api/hub/commonroom/feed', params={'section': 'new', 'boundary': 0}).json()['posts'][0]['post_id']
    c = client.get(f'/api/hub/commonroom/posts/{pid}/comments').json()
    assert c['comments'][0]['children'][0]['author'] == 'eve'
    assert client.get(f'/api/hub/commonroom/posts/{pid}').json()['body'].startswith('Body')


def test_other_profiles_cannot_see_or_touch_threads(client):
    t = thread(client)
    run_all()
    _, oh = other_profile(client)
    assert client.get('/api/hub/commonroom/threads', headers=oh).json()['threads'] == []
    assert client.patch(f"/api/hub/commonroom/threads/{t['id']}", json={'name': 'x'}, headers=oh).status_code == 404
    assert client.get('/api/hub/commonroom/feed', params={'boundary': 0}, headers=oh).json()['posts'] == []
    pick = client.get('/api/hub/commonroom/feed', params={'boundary': 0}).json()['posts'][0]
    assert client.get(f"/api/hub/commonroom/posts/{pick['post_id']}/comments", headers=oh).status_code == 404
    assert client.put(f"/api/hub/commonroom/picks/{pick['pick_id']}", json={'saved': True}, headers=oh).status_code == 404


def _child(client):
    with session() as s:
        kid = Profile(name='Kid', role='child')
        s.add(kid)
        s.commit()
        s.refresh(kid)
        return kid.id, {'X-Profile-Id': str(kid.id)}


def test_child_thread_fails_closed_without_a_safety_model(client, monkeypatch):
    monkeypatch.setitem(curate.THRESHOLD, 'strict', 0)  # everything passes the judge: only the kid gate decides
    kid, kh = _child(client)
    t = thread(client, headers=kh, strictness='loose')
    assert t['child_friendly'] is True and t['strictness'] == 'strict'
    run_all()
    assert client.get('/api/hub/commonroom/feed', params={'boundary': 0}, headers=kh).json()['posts'] == []
    with session() as s:
        assert not any(p.kid_ok == 1 for p in s.exec(select(CRPost)).all())
        # No model answer: nothing approved, and nothing rejected for good either (judged again later).
        assert not any(c.status == 'kept' for c in s.exec(select(CRCandidate)).all())
        assert all(p.kid_ok is None for p in s.exec(select(CRPost)).all())


def test_child_sees_only_kid_checked_posts_and_comments(client, monkeypatch):
    kid, kh = _child(client)
    monkeypatch.setattr(kids.ai, 'enabled', lambda: True)  # bt.hub.ai: shared by curate and kids
    monkeypatch.setattr(kids.kidsafe, '_vision', lambda: False)

    def judge(system, user, schema, **kw):
        if 'scores' in schema['properties']:  # curator: everything fits
            return {'scores': [{'n': i, 'score': 90, 'why': 'fits'} for i in range(1, 16)]}
        # Kid check: approve item 1 of every batch only.
        return {'verdicts': [{'n': 1, 'kid_safe': True, 'reason': ''}, {'n': 2, 'kid_safe': False, 'reason': 'no'}]}
    monkeypatch.setattr(kids.ai, '_call', judge)
    monkeypatch.setitem(curate.THRESHOLD, 'strict', 0)
    t = thread(client, headers=kh)
    with session() as s:
        thr = s.get(CRThread, t['id'])
        thr.want_more = False
        s.add(thr)
        s.commit()
    run_all()
    posts = client.get('/api/hub/commonroom/feed', params={'boundary': 0}, headers=kh).json()['posts']
    assert len(posts) == 1 and posts[0]['media'] == []
    c = client.get(f"/api/hub/commonroom/posts/{posts[0]['post_id']}/comments", headers=kh).json()['comments']
    assert c and c[0]['author'] == 'bob' and c[0]['children'] == []  # second comment judged unsafe: branch dropped
    assert client.get(f"/api/hub/commonroom/posts/{posts[0]['post_id']}/media/0", headers=kh).status_code == 404


def test_judge_threshold_and_rejections_are_remembered(client, monkeypatch):
    monkeypatch.setattr(curate.ai, 'enabled', lambda: True)
    calls = []

    def judge(system, user, schema, **kw):
        calls.append(user)
        return {'scores': [{'n': 1, 'score': 90, 'why': 'Great fit'}, {'n': 2, 'score': 10, 'why': 'meh'}]}
    monkeypatch.setattr(curate.ai, '_call', judge)
    t = thread(client, strictness='balanced')
    with session() as s:
        thr = s.get(CRThread, t['id'])
        thr.discovered_at, thr.want_more = time.time(), False
        s.add(thr)
        s.commit()
    run_all()
    with session() as s:
        picks = s.exec(select(CRPick)).all()
        assert len(picks) == 1 and picks[0].why == 'Great fit'
        statuses = sorted(c.status for c in s.exec(select(CRCandidate)).all())
        assert statuses.count('kept') == 1 and statuses.count('rejected') >= 2
    n = len(calls)
    with session() as s:
        thr = s.get(CRThread, t['id'])
        thr.next_run = 0
        s.add(thr)
        s.commit()
    run_all()
    assert len(calls) == n  # nothing re-judged


def test_delete_profile_removes_threads(client):
    t = thread(client)
    run_all()
    with session() as s:
        cr_api.delete_profile(s, 1)
        s.commit()
        assert s.get(CRThread, t['id']) is None
        assert s.exec(select(CRPick)).all() == []


def test_failed_run_does_not_spin_and_dry_deep_rests(client, monkeypatch):
    t = thread(client)
    monkeypatch.setattr(curate, 'run', lambda thr, lease: (_ for _ in ()).throw(RuntimeError('boom')))
    assert scheduler.dispatch_due() is True
    assert scheduler.dispatch_due() is False  # error backoff holds even though the thread wanted more
    with session() as s:
        thr = s.get(CRThread, t['id'])
        assert thr.want_more is False and thr.next_run > time.time() + 200
        sid = s.exec(select(CRSource)).first().id
        src = s.get(CRSource, sid)
        src.exhausted = True
        thr.last_run = time.time()
        s.add(src)
        s.add(thr)
        s.commit()
    deep = client.get('/api/hub/commonroom/feed', params={'thread': t['id'], 'section': 'deep', 'boundary': 0}).json()
    assert deep['building'] is False
    with session() as s:
        assert s.get(CRThread, t['id']).want_more is False


def test_child_post_body_is_cut_to_what_was_judged(client, monkeypatch):
    kid, kh = _child(client)
    monkeypatch.setattr(kids.ai, 'enabled', lambda: True)
    monkeypatch.setattr(kids.kidsafe, '_vision', lambda: False)
    monkeypatch.setattr(kids.ai, '_call', lambda system, user, schema, **kw: (
        {'scores': [{'n': i, 'score': 90, 'why': ''} for i in range(1, 16)]} if 'scores' in schema['properties']
        else {'verdicts': [{'n': i, 'kid_safe': True, 'reason': ''} for i in range(1, 9)]}))
    long = 'x' * 9000
    monkeypatch.setattr(FAKE, 'fetch', lambda loc, sort, cur: Page([post(1, body=long)] if sort == 'new' else [], ''))
    t = thread(client, headers=kh)
    run_all()
    p = client.get('/api/hub/commonroom/feed', params={'boundary': 0}, headers=kh).json()['posts'][0]
    assert len(client.get(f"/api/hub/commonroom/posts/{p['post_id']}", headers=kh).json()['body']) == kids.BODY_SEEN


def test_failing_source_counts_as_done_for_dry_deep(client):
    t = thread(client)
    run_all()
    with session() as s:
        src = s.exec(select(CRSource)).first()
        src.exhausted, src.failures = False, 5
        s.add(src)
        thr = s.get(CRThread, t['id'])
        thr.want_more, thr.last_run = False, time.time()
        s.add(thr)
        s.commit()
    deep = client.get('/api/hub/commonroom/feed', params={'thread': t['id'], 'section': 'deep', 'boundary': 0}).json()
    assert deep['building'] is False
    with session() as s:
        assert s.get(CRThread, t['id']).want_more is False


def test_kid_comment_cut_fits_what_the_judge_reads():
    from bt.hub import ai
    seen = ai._data([{'type': 'comment', 'title': '', 'body': 'y' * 1500}])
    assert 'y' * 1500 in seen


def test_thread_new_counts(client):
    t = thread(client)
    run_all()
    th = client.get('/api/hub/commonroom/threads').json()['threads'][0]
    assert th['new_count'] == th['pick_count'] > 0 and th['source_count'] == 1
    client.post('/api/hub/commonroom/visit', json={'thread': str(t['id'])})
    assert client.get('/api/hub/commonroom/threads').json()['threads'][0]['new_count'] == 0


def test_visit_accepts_numeric_thread_id_like_the_page_sends(client):
    t = thread(client)
    r = client.post('/api/hub/commonroom/visit', json={'thread': t['id']})
    assert r.status_code == 200, r.text
    assert client.post('/api/hub/commonroom/visit', json={'thread': 9999}).status_code == 404


def test_link_posts_get_the_linked_pages_preview_picture(client, monkeypatch):
    from bt.commonroom import previews
    monkeypatch.setattr(FAKE, 'fetch', lambda loc, sort, cur: Page(
        [post(1, link_url='https://blog.example/a'), post(2, link_url='https://news.ycombinator.com/x')]
        if sort == 'new' else [], ''))
    thread(client)
    run_all()
    fetched = []

    def page(url, kinds=None, limit=None):
        fetched.append(url)
        return b'<html><head><meta property="og:image" content="/img/cover.jpg"></head></html>'
    monkeypatch.setattr(previews.curation, 'fetch', page)
    assert previews.sweep() == 1
    assert fetched == ['https://blog.example/a']  # aggregator pages are never fetched
    assert previews.sweep() == 0 and len(fetched) == 1  # tried once only
    posts = client.get('/api/hub/commonroom/feed', params={'boundary': 0}).json()['posts']
    with_pic = [p for p in posts if p['media']]
    assert len(with_pic) == 1 and with_pic[0]['media'][0]['url'].endswith('/media/0')
    with session() as s:
        assert s.get(CRPost, with_pic[0]['post_id']).media[0]['url'] == 'https://blog.example/img/cover.jpg'


def test_feed_thumbnails_are_replaced_by_the_full_picture(client, monkeypatch):
    from bt.commonroom import previews
    thumb = [{'kind': 'image', 'url': 'https://b.thumbs.redditmedia.com/t.jpg', 'poster': '', 'w': 0, 'h': 0, 'from': 'thumb'}]
    monkeypatch.setattr(FAKE, 'fetch', lambda loc, sort, cur: Page(
        [post(1, link_url='https://i.redd.it/full.png', media=thumb)] if sort == 'new' else [], ''))
    thread(client)
    run_all()
    monkeypatch.setattr(previews.curation, 'fetch', lambda *a, **k: pytest.fail('a direct image needs no page fetch'))
    assert previews.sweep() == 1
    with session() as s:
        p = s.exec(select(CRPost)).first()
        assert p.media[0]['url'] == 'https://i.redd.it/full.png'



def test_rss_entries_use_the_full_picture_not_the_feed_thumbnail():
    from bt.commonroom.adapters.reddit import rss_image
    assert rss_image('https://i.redd.it/a.png', '') == 'https://i.redd.it/a.png'
    html = '<a href="x"><img src="https://preview.redd.it/b.jpg?width=640&amp;crop=smart&amp;s=1" alt="t" /></a>'
    assert rss_image('https://example.com/article', html) == 'https://preview.redd.it/b.jpg?width=640&crop=smart&s=1'
    assert rss_image('', '<img src="https://b.thumbs.redditmedia.com/t.jpg">') == ''


def test_curated_posts_work_offline_pictures_thumbnails_and_comments(client, monkeypatch, tmp_path):
    """Once picked, a post's pictures (full + preview) and comments are stored locally and served without WAN."""
    import io
    from PIL import Image
    from bt.commonroom import assets
    monkeypatch.setattr(assets, 'MEDIA_DIR', tmp_path)
    buf = io.BytesIO()
    Image.new('RGB', (1600, 1200), 'red').save(buf, format='PNG')
    big = buf.getvalue()
    monkeypatch.setattr(FAKE, 'fetch', lambda loc, sort, cur: Page(
        [post(1, media=[{'kind': 'image', 'url': 'https://img.example/a.png', 'poster': '', 'w': 0, 'h': 0}])]
        if sort == 'new' else [], ''))
    monkeypatch.setattr(FAKE, 'comments', lambda data: [{'id': 'c1', 'author': 'bob', 'body': 'hi', 'score': 1,
                                                          'published_at': 0, 'replies': []}])
    thread(client)
    run_all()
    monkeypatch.setattr(assets.curation, 'fetch', lambda *a, **k: big)
    assert assets.sweep() == {'images': 1, 'comments': 1}

    def offline(*a, **k):
        raise OSError('no network')
    monkeypatch.setattr(assets.curation, 'fetch', offline)
    monkeypatch.setattr(FAKE, 'comments', offline)
    p = client.get('/api/hub/commonroom/feed', params={'boundary': 0}).json()['posts'][0]
    full = client.get(p['media'][0]['url'])
    thumb = client.get(p['media'][0]['thumb'])
    assert full.status_code == 200 and full.content == big
    assert thumb.status_code == 200 and thumb.headers['content-type'] == 'image/webp' and len(thumb.content) < len(big)
    c = client.get(f"/api/hub/commonroom/posts/{p['post_id']}/comments").json()
    assert c['comments'] and c['comments'][0]['body'] == 'hi'
    # Unreferenced files go; referenced ones stay.
    (tmp_path / 'zz').mkdir()
    (tmp_path / 'zz' / 'orphan.full').write_bytes(b'x')
    assert assets.gc(min_age_s=0) == 1 and client.get(p['media'][0]['url']).status_code == 200


def test_pictures_the_source_no_longer_serves_are_left_out(client, monkeypatch, tmp_path):
    monkeypatch.setattr(assets, 'MEDIA_DIR', tmp_path)
    monkeypatch.setattr(FAKE, 'fetch', lambda loc, sort, cur: Page(
        [post(1, media=[{'kind': 'image', 'url': 'https://img.example/gone.png', 'poster': '', 'w': 0, 'h': 0},
                        {'kind': 'image', 'url': 'https://img.example/ok.png', 'poster': '', 'w': 0, 'h': 0}])]
        if sort == 'new' else [], ''))
    thread(client)
    run_all()
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (8, 8)).save(buf, format='PNG')

    def fetch(url, **k):
        if 'gone' in url:
            raise OSError('HTTP 404')
        return buf.getvalue()
    monkeypatch.setattr(assets.curation, 'fetch', fetch)
    assets.sweep_images()
    media = client.get('/api/hub/commonroom/feed', params={'boundary': 0}).json()['posts'][0]['media']
    assert [m['url'].rsplit('/', 1)[1] for m in media] == ['1']  # index of ok.png kept stable
