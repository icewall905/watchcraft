"""The Commons fun redesign: fun/kind judging, wildcards, top comments, elsewhere, velocity, mix, modes."""
import time

from sqlmodel import select

from test_curation import client  # noqa: F401
from test_commonroom import fake_platform, run_all, thread  # noqa: F401
from bt.commonroom import curate
from bt.commonroom.curate import Verdict, keeps
from bt.commonroom.models import CRPick, CRThread
from bt.db import session


def test_keep_rule_lets_delightful_near_misses_in_as_wildcards():
    assert keeps(Verdict(70, '', 0), 60, True) == (True, False)
    assert keeps(Verdict(45, '', 85), 60, True) == (True, True)
    assert keeps(Verdict(39, '', 99), 60, True) == (False, False)   # too far off-topic
    assert keeps(Verdict(55, '', 79), 60, True) == (False, False)   # not fun enough
    assert keeps(Verdict(45, '', 85), 60, False) == (False, False)  # wildcards off


def _judged(client, monkeypatch, child_friendly=False):
    monkeypatch.setattr(curate.ai, 'enabled', lambda: True)
    rows = [{'n': 1, 'score': 90, 'fun': 10, 'kind': 'news', 'why': 'On topic'},
            {'n': 2, 'score': 50, 'fun': 90, 'kind': 'humor', 'why': 'Too good to skip'},
            {'n': 3, 'score': 30, 'fun': 95, 'kind': 'BOGUS', 'why': 'Funny but off-topic'}]
    monkeypatch.setattr(curate.ai, '_call', lambda system, user, schema, **k: {'scores': rows})
    t = thread(client, strictness='balanced')
    with session() as s:
        thr = s.get(CRThread, t['id'])
        thr.discovered_at, thr.want_more, thr.child_friendly = time.time(), False, child_friendly
        s.add(thr)
        s.commit()
    run_all()
    with session() as s:
        return sorted(s.exec(select(CRPick)).all(), key=lambda p: -p.score)


def test_judge_stores_fun_kind_and_wildcard(client, monkeypatch):
    picks = _judged(client, monkeypatch)
    assert [(p.score, p.fun, p.kind, p.wildcard) for p in picks] == [(90, 10, 'news', False),
                                                                     (50, 90, 'humor', True)]


def test_child_friendly_thread_keeps_no_wildcards(client, monkeypatch):
    monkeypatch.setattr(curate.kids, 'check_posts', lambda ids: (set(ids), set()))
    picks = _judged(client, monkeypatch, child_friendly=True)
    assert [p.score for p in picks] == [90]


def test_prompt_asks_for_fun_and_kind():
    schema_item = curate.SCHEMA['properties']['scores']['items']
    assert {'fun', 'kind'} <= set(schema_item['required'])
    assert set(schema_item['properties']['kind']['enum']) == set(curate.KINDS)


# ---------- phase 2: the conversation

from bt.commonroom import social, store  # noqa: E402
from bt.commonroom.models import CRComments  # noqa: E402


def test_link_key_normalises_tracking_www_and_slash():
    a = social.link_key('http://www.Example.com/story/?utm_source=x&id=3')
    assert a == social.link_key('https://example.com/story?id=3')
    assert social.link_key('') == ''


def test_top_comments_skip_op_removed_and_bots_and_prefer_readable():
    tree = [{'author': 'op', 'body': 'thanks all', 'score': 999, 'children': []},
            {'author': 'x', 'body': '[deleted]', 'score': 500, 'children': []},
            {'author': 'AutoModerator', 'body': 'Rules reminder', 'score': 400, 'children': []},
            {'author': 'wall', 'body': 'w' * 1000, 'score': 100, 'children': []},
            {'author': 'wit', 'body': 'This is the best thing I have read all week', 'score': 90,
             'children': [{'author': 'deep', 'body': 'nested reply wins nothing', 'score': 9999, 'children': []}]},
            {'author': 'meh', 'body': 'ok sure fine whatever then', 'score': 1, 'children': []}]
    top = social.top_comments(tree, op='OP')
    assert [c['author'] for c in top] == ['wit', 'wall']
    assert top[1]['body'].endswith('…') and len(top[1]['body']) == social.TOP_CHARS + 1


def _pick(t, url, link='', comments=0, platform='fake', author='alice', **kw):
    [pid] = store.upsert_posts([{'platform': platform, 'community': 'c/one', 'url': url, 'title': url,
                                 'link_url': link, 'author': author, 'comments': comments,
                                 'published_at': time.time() - 60}])
    with session() as s:
        s.add(CRPick(profile_id=1, thread_id=t['id'], post_id=pid, score=80, picked_at=time.time() - 30,
                     published_at=time.time() - 60, **kw))
        s.commit()
    return pid


def _feed(client, **params):
    r = client.get('/api/hub/commonroom/feed', params={'boundary': 0, **params})
    assert r.status_code == 200, r.text
    return r.json()['posts']


def test_feed_shows_top_comments_and_folds_same_link(client):
    t = thread(client)
    a = _pick(t, 'https://fake.example/a', 'https://www.news.example/story/', comments=5)
    _pick(t, 'https://fake.example/b', 'https://news.example/story?utm_medium=x', comments=50)
    store.upsert_posts([{'platform': 'hn', 'community': 'HN', 'url': 'https://hn.example/1', 'title': 'Story',
                         'link_url': 'https://news.example/story', 'comments': 212}])
    with session() as s:
        s.add(CRComments(post_id=a, tree=[{'author': 'bob', 'body': 'Great find', 'score': 3}], fetched_at=time.time()))
        s.commit()
    posts = _feed(client)
    assert len(posts) == 1  # one card for the link: the livelier discussion
    card = posts[0]
    assert card['url'] == 'https://fake.example/b'
    assert [e['url'] for e in card['elsewhere']] == ['https://hn.example/1', 'https://fake.example/a']
    assert card['top'] == []
    c = client.get(f"/api/hub/commonroom/posts/{a}").json()
    assert c['top'][0]['author'] == 'bob' and len(c['elsewhere']) == 2


# ---------- phase 3: liveliness

def test_samples_and_velocity_prefer_recent_growth():
    now = 100_000.0
    m = social.sample({}, 10, 1, now - 3600)
    m = social.sample(m, 10, 1, now - 3500)       # unchanged and close: kept as one sample
    assert len(m['hist']) == 1
    m = social.sample(m, 110, 21, now)
    assert len(m['hist']) == 2
    # 100 score + 2 x 20 comments in one hour
    assert social.velocity(m, 110, 21, now - 86400, now) == 140
    # no history: lifetime rate, at least one hour
    assert social.velocity({}, 10, 0, now - 60, now) == 10
    assert social.velocity({}, 0, 0, now - 60, now) == 0
    assert social.sample({}, 0, 0, now) == {}  # feeds without numbers record nothing


def test_badges_are_relative_to_each_platform():
    rows = [('reddit', float(v)) for v in range(1, 101)] + [('hn', float(v) / 10) for v in range(1, 101)] + \
           [('tiny', 50.0)] * 5
    cuts = social.thresholds(rows)
    assert 'tiny' not in cuts  # too few peers to judge
    now = 1e6
    busy = {'score': 20, 'comments': 5}
    assert social.badges('reddit', 99, 0, now, cuts, **busy) == ['hot']
    assert social.badges('reddit', 85, 0, now, cuts, **busy) == ['rising']
    assert social.badges('hn', 9.9, 0, now, cuts, **busy) == ['hot']   # small numbers, but hot for HN
    assert social.badges('reddit', 9.9, now - 60, now, cuts, **busy) == ['fresh']


def test_feed_reports_velocity_and_badges(client):
    t = thread(client)
    _pick(t, 'https://fake.example/v', comments=10)
    [card] = _feed(client)
    assert card['velocity'] > 0 and 'fresh' in card['badges']


# ---------- phase 4: mix

def test_mix_breaks_kind_runs_caps_communities_spreads_wildcards():
    posts = [{'id': i, 'kind': 'news', 'community': 'r/a', 'wildcard': False} for i in range(6)] + \
            [{'id': 10 + i, 'kind': 'humor', 'community': f'r/{i}', 'wildcard': True} for i in range(2)] + \
            [{'id': 20 + i, 'kind': 'question', 'community': f'r/q{i}', 'wildcard': False} for i in range(6)]
    out = social.mix(posts)
    assert sorted(p['id'] for p in out) == sorted(p['id'] for p in posts)  # nothing dropped
    kinds = [p['kind'] for p in out]
    head = kinds[:12]
    assert not any(head[i] == head[i + 1] == head[i + 2] for i in range(len(head) - 2))
    assert sum(1 for p in out[:9] if p['community'] == 'r/a') <= social.COMMUNITY_CAP
    # Wildcards spread out when the page has enough else to show between them.
    kinds = ['news', 'question', 'showcase', 'deepdive']
    page = [{'id': 0, 'kind': 'humor', 'community': 'x', 'wildcard': True},
            {'id': 1, 'kind': 'oddity', 'community': 'y', 'wildcard': True}] + \
           [{'id': 2 + i, 'kind': kinds[i % 4], 'community': f'c{i}', 'wildcard': False} for i in range(10)]
    wild = [n for n, p in enumerate(social.mix(page)) if p['wildcard']]
    assert wild[0] == 0 and wild[1] - wild[0] >= social.WILD_GAP


# ---------- phase 5: browse modes

def test_feed_modes_kind_filter_and_section_alias(client):
    t = thread(client)
    a = _pick(t, 'https://fake.example/m1', comments=1, kind='humor', fun=90)
    b = _pick(t, 'https://fake.example/m2', comments=1, kind='news', fun=0, saved=True)
    with session() as s:  # an old pick: out of Today's day and Top's week
        old = s.exec(select(CRPick).where(CRPick.post_id == b)).one()
        old.published_at = time.time() - 10 * 86400
        s.add(old)
        s.commit()
    ids = lambda posts: [p['post_id'] for p in posts]  # noqa: E731
    r = client.get('/api/hub/commonroom/feed', params={'mode': 'today'}).json()
    assert ids(r['posts']) == [a] and r['end'] is True and r['has_more'] is False
    assert ids(_feed(client, mode='saved')) == [b]
    assert ids(_feed(client, mode='top')) == [a]
    assert set(ids(_feed(client, mode='hot'))) == {a}
    assert ids(_feed(client, mode='new', kind='news')) == [b]
    assert set(ids(_feed(client, section='new'))) == {a, b}  # older clients
    assert client.get('/api/hub/commonroom/feed', params={'mode': 'nope'}).status_code == 400
    assert client.get('/api/hub/commonroom/feed', params={'kind': 'nope'}).status_code == 400
    card = _feed(client, mode='saved')[0]
    assert card['kind'] == 'news' and card['saved'] is True and card['fun'] == 0


# ---------- live-data fixes: badge floors, comment refetch, re-judge

def test_badges_need_real_engagement_and_no_downvotes():
    cuts = {'hn': (0.01, 0.02)}
    now = 1e6
    assert social.badges('hn', 5, 0, now, cuts, score=-15, comments=3) == []      # downvoted
    assert social.badges('hn', 5, 0, now, cuts, score=1, comments=0) == []        # near-silent
    assert social.badges('hn', 5, 0, now, cuts, score=4, comments=1) == ['rising']
    assert social.badges('hn', 5, 0, now, cuts, score=10, comments=3) == ['hot']


def test_comments_refetched_when_empty_or_grown(client, monkeypatch):
    from bt.commonroom import assets
    t = thread(client)
    a = _pick(t, 'https://fake.example/c1', comments=5)
    b = _pick(t, 'https://fake.example/c2', comments=2)
    old = time.time() - 3 * 3600
    with session() as s:
        s.add(CRComments(post_id=a, tree=[], fetched_at=0, checked_at=old))
        s.add(CRComments(post_id=b, tree=[{'author': 'x', 'body': 'kept', 'score': 1}], fetched_at=old,
                         checked_at=old, count=2))
        s.commit()
    calls = []

    class Adapter:
        @staticmethod
        def comments(data):
            calls.append(data['id'])
            return [{'author': 'bob', 'body': 'Now there are replies', 'score': 4}]
    monkeypatch.setitem(assets.ADAPTERS, 'fake', Adapter)
    assert assets.sweep_comments() == 1 and calls == [a]  # b has not grown
    with session() as s:
        row = s.get(CRComments, a)
        assert row.tree and row.count == 5 and row.checked_at > old
    assert assets.sweep_comments() == 0  # backoff
    with session() as s:  # b gains comments; a failed refetch never wipes a stored tree
        from bt.commonroom.models import CRPost
        post = s.get(CRPost, b)
        post.comments = 9
        s.add(post)
        s.commit()
    Adapter.comments = staticmethod(lambda data: calls.append(data['id']) or [])
    assert assets.sweep_comments() == 1 and calls[-1] == b
    with session() as s:
        assert s.get(CRComments, b).tree[0]['body'] == 'kept'


def test_rejudge_fills_fun_and_kind_of_older_picks_once(client, monkeypatch):
    t = thread(client)
    a = _pick(t, 'https://fake.example/r1')
    b = _pick(t, 'https://fake.example/r2', kind='news', fun=5)
    monkeypatch.setattr(curate.ai, 'enabled', lambda: True)
    monkeypatch.setattr(curate, '_rejudge', {'at': 0.0, 'tried': set()})
    seen = []

    def judge(thread, lessons, batch):
        seen.append([p.id for p in batch])
        return {p.id: Verdict(10, 'ignored', 77, 'humor') for p in batch}
    monkeypatch.setattr(curate, '_judge', judge)
    assert curate.rejudge() == 1 and seen == [[a]]
    with session() as s:
        picks = {p.post_id: p for p in s.exec(select(CRPick)).all()}
    assert (picks[a].fun, picks[a].kind, picks[a].score) == (77, 'humor', 80)  # score untouched
    assert (picks[b].fun, picks[b].kind) == (5, 'news')
    assert curate.rejudge() == 0  # throttled
    assert curate.rejudge(time.time() + 120) == 0 and len(seen) == 1  # nothing left


def test_comment_endpoint_serves_stored_tree_and_refreshes_on_request(client, monkeypatch):
    from bt.commonroom import api as crapi
    from test_commonroom import FAKE
    t = thread(client)
    a = _pick(t, 'https://fake.example/e1', comments=3)
    calls = []
    monkeypatch.setattr(FAKE, 'comments', lambda data: calls.append(1) or [{'id': 'n', 'author': 'new', 'body': 'fresh',
                                                                          'score': 1, 'children': []}])
    old = time.time() - 86400
    with session() as s:
        s.add(CRComments(post_id=a, tree=[{'id': 'o', 'author': 'old', 'body': 'stored', 'score': 1, 'children': []}],
                         fetched_at=old, count=3))
        s.commit()
    r = client.get(f'/api/hub/commonroom/posts/{a}/comments').json()
    assert r['comments'][0]['body'] == 'stored' and r['supported'] is True and r['fetched_at'] == old
    assert calls == []  # a stored tree is served at once, however old: the sweep keeps it current
    r = client.get(f'/api/hub/commonroom/posts/{a}/comments', params={'refresh': 1}).json()
    assert r['comments'][0]['body'] == 'fresh' and calls == [1]
    client.get(f'/api/hub/commonroom/posts/{a}/comments', params={'refresh': 1})
    assert calls == [1]  # refresh is not honoured again within a couple of minutes
    monkeypatch.setattr(crapi.ADAPTERS['fake'], 'comments', None)
    assert client.get(f'/api/hub/commonroom/posts/{a}/comments').json()['supported'] is False


# ---------- scraped pages and Reddit mirrors

MIRROR = 'https://reddclips.com/r/interestingasfuck/1y2uzek'
MIRROR_HTML = (b'<html><head><meta property="og:description" content="Scrabble by u/Marasitamer12 in '
               b'r/interestingasfuck. 16,406 upvotes. Browse more on Reddclips."/><meta property="og:image" '
               b'content="https://preview.redd.it/s8tfz6vy973i1.png?width=320&amp;crop=smart"/></head></html>')


def test_reddit_ref_knows_reddit_and_mirrors_only():
    assert social.reddit_ref(MIRROR) == ('interestingasfuck', '1y2uzek')
    assert social.reddit_ref('https://old.reddit.com/r/homelab/comments/abc123/title/') == ('homelab', 'abc123')
    assert social.reddit_ref('https://www.reddit.com/r/homelab/') is None
    assert social.reddit_ref('https://blog.example/r/x/abcd12') is None


def test_scraped_pages_get_their_own_picture_and_mirrors_their_reddit_numbers(client, monkeypatch):
    from bt.commonroom import previews
    from bt.commonroom.models import CRPost
    t = thread(client)
    mirror = _pick(t, MIRROR, platform='web', author='')
    logo = _pick(t, 'https://site.example/story', platform='web')
    pages = {MIRROR: MIRROR_HTML,
             'https://site.example/story': b'<meta property="og:image" content="/static/logo.png">'}
    monkeypatch.setattr(previews.curation, 'fetch', lambda url, **k: pages[url])
    assert previews.sweep() == 1
    with session() as s:
        m, l = s.get(CRPost, mirror), s.get(CRPost, logo)
        assert m.media[0]['url'] == 'https://i.redd.it/s8tfz6vy973i1.png'  # full size, not the 320 px preview
        assert (m.author, m.score, m.meta['subreddit']) == ('Marasitamer12', 16406, 'interestingasfuck')
        assert l.media == [] and l.meta['preview_checked']  # a site logo is not a picture of the post


def test_mirror_comments_come_from_reddit_only_with_api_keys(client, monkeypatch):
    from bt.commonroom import assets, reddit_oauth
    t = thread(client)
    pid = _pick(t, MIRROR, platform='web')
    monkeypatch.setattr(reddit_oauth, 'configured', lambda: False)
    r = client.get(f'/api/hub/commonroom/posts/{pid}/comments').json()
    assert r['supported'] is False
    assert r['discussion'] == r['url'] == 'https://www.reddit.com/r/interestingasfuck/comments/1y2uzek/'
    asked = []
    monkeypatch.setattr(reddit_oauth, 'configured', lambda: True)
    from types import SimpleNamespace
    monkeypatch.setitem(assets.ADAPTERS, 'reddit', SimpleNamespace(comments=lambda data: asked.append(
        data['external_id']) or [{'id': 'r', 'author': 'a', 'body': 'wow', 'score': 9, 'children': []}]))
    r = client.get(f'/api/hub/commonroom/posts/{pid}/comments', params={'refresh': 1}).json()
    assert r['supported'] is True and r['comments'][0]['body'] == 'wow' and asked == ['1y2uzek']


# ---------- videos from scraped pages

import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

HLS = 'https://v.redd.it/abc123/HLSPlaylist.m3u8?a=1%2C2&v=1&f=sd'
VIDEO_HTML = ('<meta property="og:video" content="https://reddclips.com/embed/r/x/1abcd"/>'
              '<meta property="og:video:type" content="text/html"/>'
              f'<meta property="og:video" content="{HLS.replace("&", "&amp;")}"/>'
              '<meta property="og:video:width" content="720"/><meta property="og:video:height" content="1280"/>'
              '<meta property="og:image" content="https://preview.redd.it/poster.jpg?width=320"/>').encode()


def test_mirror_video_found_skipping_the_embed_page(client, monkeypatch):
    from bt.commonroom import previews
    from bt.commonroom.models import CRPost
    t = thread(client)
    pid = _pick(t, 'https://reddclips.com/r/x/1abcd', platform='web')
    monkeypatch.setattr(previews.curation, 'fetch', lambda url, **k: VIDEO_HTML)
    previews.sweep()
    with session() as s:
        [m] = s.get(CRPost, pid).media
    assert (m['kind'], m['url'], m['poster'], m['w'], m['h']) == ('video', HLS, 'https://i.redd.it/poster.jpg', 720, 1280)


def test_mirror_checked_before_videos_gets_one_more_look(client, monkeypatch):
    from bt.commonroom import previews
    from bt.commonroom.models import CRPost
    t = thread(client)
    pid = _pick(t, 'https://reddclips.com/r/x/1abcd', platform='web')
    with session() as s:
        p = s.get(CRPost, pid)
        p.media = [{'kind': 'image', 'url': 'https://i.redd.it/a.png', 'poster': '', 'w': 0, 'h': 0}]
        p.meta = {'preview_checked': 1}
        s.add(p)
        s.commit()
    monkeypatch.setattr(previews.curation, 'fetch', lambda url, **k: VIDEO_HTML)
    previews.sweep()
    with session() as s:
        p = s.get(CRPost, pid)
        assert p.media[0]['kind'] == 'video' and p.meta['video_checked']
    assert previews._due(10) == []


def test_only_known_video_hosts_and_files_are_saveable():
    from bt.commonroom.assets import video_saveable
    assert video_saveable(HLS) and video_saveable('https://files.example/clip.mp4')
    assert not video_saveable('https://evil.example/list.m3u8')        # ffmpeg never follows unknown playlists
    assert not video_saveable('http://files.example/clip.mp4')
    assert not video_saveable('https://reddclips.com/embed/r/x/1abcd')


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='needs ffmpeg')
def test_saved_video_is_served_with_ranges_and_listed_on_the_card(client, monkeypatch, tmp_path):
    from bt.commonroom import assets
    from bt.commonroom.models import CRPost
    clip = tmp_path / 'c.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=10', '-f', 'lavfi',
                    '-i', 'sine=frequency=440', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    '-shortest', str(clip)], check=True, capture_output=True)
    url = 'https://files.example/clip.mp4'
    monkeypatch.setattr(assets, 'MEDIA_DIR', tmp_path / 'media')
    monkeypatch.setattr(assets.curation, 'fetch', lambda u, **k: clip.read_bytes())
    t = thread(client)
    pid = _pick(t, 'https://site.example/v', platform='web')
    with session() as s:
        p = s.get(CRPost, pid)
        p.media = [{'kind': 'video', 'url': url, 'poster': '', 'w': 320, 'h': 240}]
        s.add(p)
        s.commit()
    assert assets.sweep_videos() == 1 and assets.video_path(url).is_file()
    assert assets.sweep_videos() == 0
    [card] = _feed(client)
    assert card['media'][0]['src'] == f'/api/hub/commonroom/posts/{pid}/video/0'
    r = client.get(card['media'][0]['src'], headers={'Range': 'bytes=0-99'})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers['content-type'] == 'video/mp4'


def test_a_video_that_cannot_be_saved_is_not_retried(client, monkeypatch):
    from bt.commonroom import assets
    from bt.commonroom.models import CRPost
    t = thread(client)
    pid = _pick(t, 'https://site.example/v2', platform='web')
    with session() as s:
        p = s.get(CRPost, pid)
        p.media = [{'kind': 'video', 'url': HLS, 'poster': '', 'w': 0, 'h': 0}]
        s.add(p)
        s.commit()
    calls = []
    monkeypatch.setattr(assets, 'save_video', lambda u: calls.append(u) or False)
    assert assets.sweep_videos() == 0 and assets.sweep_videos() == 0 and calls == [HLS]
    assert _feed(client)[0]['media'][0]['src'] == ''


def test_refused_reddit_playlist_falls_back_to_plain_files(monkeypatch, tmp_path):
    from bt.commonroom import assets
    monkeypatch.setattr(assets, 'MEDIA_DIR', tmp_path)
    tried = []

    def save_from(sources, net, out, tmp):
        tried.append(sources)
        if '.m3u8' in sources[0]:
            raise subprocess.CalledProcessError(1, 'ffprobe', stderr=b'403 Forbidden')
        return True
    monkeypatch.setattr(assets, '_save_from', save_from)
    monkeypatch.setattr(assets, '_exists', lambda u: u.endswith(('CMAF_480.mp4', 'CMAF_AUDIO_128.mp4')))
    assert assets.save_video(HLS)
    assert tried[-1] == ['https://v.redd.it/abc123/CMAF_480.mp4', 'https://v.redd.it/abc123/CMAF_AUDIO_128.mp4']
    monkeypatch.setattr(assets, '_exists', lambda u: False)
    assert assets.save_video(HLS) is False


def test_older_picked_videos_are_saved_too(client, monkeypatch):
    from bt.commonroom import assets
    from bt.commonroom.models import CRPost
    t = thread(client)
    old = _pick(t, 'https://site.example/old-video', platform='web')
    for n in range(5):
        _pick(t, f'https://site.example/newer-{n}', platform='web')
    with session() as s:
        p = s.get(CRPost, old)
        p.media = [{'kind': 'video', 'url': 'https://files.example/old.mp4', 'poster': '', 'w': 0, 'h': 0}]
        s.add(p)
        s.commit()
    monkeypatch.setattr(assets, '_picked_posts', lambda limit=300: [])  # beyond the picture sweep's window
    got = []
    monkeypatch.setattr(assets, 'save_video', lambda u: got.append(u) or False)
    assets.sweep_videos()
    assert got == ['https://files.example/old.mp4']
