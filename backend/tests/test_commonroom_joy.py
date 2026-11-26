"""The Commons learns what is fun: engagement from what the owner does, priors, source throttles, taste memo."""
import time

from sqlmodel import select

from test_curation import client  # noqa: F401
from test_commonroom import fake_platform, thread  # noqa: F401
from test_commonroom_fun import _pick
from test_master_api import other_profile
from bt.commonroom import curate, joy, social, teach
from bt.commonroom.curate import Verdict, keeps
from bt.commonroom.models import CREvent, CRFeedback, CRPick, CRPost, CRSourcePost, CRThreadSource
from bt.db import get_kv, session

NOW = 1_800_000_000.0


def _eng(verdict='', reasons=(), saved=False, opened=False, dwell=0, counts=None, seen=0.0):
    return joy.engagement(verdict, list(reasons), saved, opened, dwell, counts or {}, seen, NOW)


def test_engagement_reads_what_the_owner_did():
    assert _eng('down') == -1.0
    assert _eng('meh') == -0.6
    assert _eng('up') == 1.0
    assert _eng(saved=True) == 0.9
    assert _eng() == 0.0                                    # never seen: no evidence
    assert _eng(seen=NOW - 3 * 86400) == -0.15              # seen days ago, never touched: a quiet skip
    assert _eng(seen=NOW - 3600) == 0.0                     # seen just now: not yet a skip
    read = _eng(opened=True, dwell=90_000, counts={'comments': 2, 'out': 1})
    assert 0.9 < read <= 1.0                                # opened, read the thread, followed the link
    glance = _eng(opened=True, dwell=2_000)
    assert 0.2 < glance < 0.4
    assert _eng('up', reasons=['funny'], saved=True) == 1.0  # capped


def test_shrunk_priors_need_evidence():
    lifts = joy._shrunk({'humor': [1.0] * 20, 'news': [-0.15], '': [1.0]}, mean=0.0)
    assert lifts['humor'] > 0.6 and -0.02 < lifts['news'] < 0 and '' not in lifts
    pri = {'kind': {'humor': 0.5}, 'community': {'c/one': -0.2}, 'platform': {'fake': 0.2}, 'source': {'7': 0.1}}
    assert round(joy.predict(pri, 'humor', 'C/One', 'fake', [7]), 3) == 0.5
    assert joy.bonus({'kind': {'humor': 5.0}}, kind='humor') == joy.PRIOR_MAX
    assert joy.bonus({}, kind='humor') == 0


def test_keep_rule_uses_priors_and_throttles():
    v = Verdict(60, '', 50)                                  # joy 56: just under the line
    assert keeps(v, 60, True) == (False, False)
    assert keeps(v, 60, True, bonus=5) == (True, False)       # the owner loves this kind of post
    assert keeps(Verdict(80, '', 60), 60, True) == (True, False)
    assert keeps(Verdict(80, '', 60), 60, True, penalty=15) == (False, False)  # throttled source: higher bar


def test_throttle_levels_hold_between_steps():
    assert joy.throttle_level(5, -0.9, 0) == 0               # too little evidence
    assert joy.throttle_level(20, -0.25, 0) == 1
    assert joy.throttle_level(20, -0.6, 0) == 3
    assert joy.throttle_level(20, -0.1, 2) == 2              # in between: hold
    assert joy.throttle_level(20, 0.1, 2) == 0               # enjoyed again: back to full strength


def test_popularity_within_the_community():
    peers = [float(n) for n in range(100)]
    assert social.percentile(99, peers) == 0.99
    assert social.percentile(5, peers[:10]) is None          # too few peers
    assert social.percentile(0, [0.0] * 40) is None          # feeds without numbers
    assert social.popularity_text(0.97, 'r/x') == 'top 5% of r/x lately'
    assert social.popularity_text(0.5, 'r/x') == ''
    assert 'few people cared' in social.popularity_text(0.1, 'r/x')


def test_events_are_stored_for_own_picks_only(client):
    t = thread(client)
    pid = _pick(t, 'https://fake.example/e1')
    with session() as s:
        pick = s.exec(select(CRPick).where(CRPick.post_id == pid)).one()
    r = client.post('/api/hub/commonroom/events', json={'events': [
        {'pick_id': pick.id, 'kind': 'open'}, {'pick_id': pick.id, 'kind': 'dwell', 'value': 45_000},
        {'pick_id': pick.id, 'kind': 'dwell', 'value': 100}, {'pick_id': pick.id, 'kind': 'bogus'},
        {'pick_id': 99999, 'kind': 'open'}]})
    assert r.status_code == 200 and r.json()['stored'] == 2
    _, oh = other_profile(client)
    assert client.post('/api/hub/commonroom/events', json={'events': [{'pick_id': pick.id, 'kind': 'out'}]},
                       headers=oh).json()['stored'] == 0
    with session() as s:
        live = s.get(CRPick, pick.id)
        assert live.opened_at and live.dwell_ms == 45_000 and live.seen_at
        assert sorted(e.kind for e in s.exec(select(CREvent)).all()) == ['dwell', 'open']


def test_meh_hides_and_teaches_on_topic_but_boring(client):
    t = thread(client)
    pid = _pick(t, 'https://fake.example/meh')
    with session() as s:
        pick = s.exec(select(CRPick).where(CRPick.post_id == pid)).one()
    r = client.put(f'/api/hub/commonroom/picks/{pick.id}/feedback', json={'verdict': 'meh', 'reasons': ['boring']})
    assert r.status_code == 200
    with session() as s:
        assert s.get(CRPick, pick.id).hidden
        assert s.exec(select(CRFeedback)).one().reasons == ['boring']
        lines = teach.lessons(s, t['id'])
    assert lines and 'boring' in lines[0]
    assert client.get(f"/api/hub/commonroom/threads/{t['id']}/learned").json()['meh'] == 1


def test_sweep_scores_picks_learns_kinds_and_throttles_skipped_sources(client):
    t = thread(client)
    now = time.time()
    with session() as s:
        link = s.exec(select(CRThreadSource).where(CRThreadSource.thread_id == t['id'])).one()
        sid = link.source_id
    for n in range(20):  # twenty dull news posts from the one source, seen days ago and never touched
        pid = _pick(t, f'https://fake.example/n{n}', kind='news')
        with session() as s:
            s.add(CRSourcePost(source_id=sid, post_id=pid))
            k = s.exec(select(CRPick).where(CRPick.post_id == pid)).one()
            k.seen_at = now - 3 * 86400
            s.add(k)
            s.commit()
    for n in range(3):
        _pick(t, f'https://fake.example/h{n}', kind='humor', saved=True, seen_at=now - 3600)
    joy._last['sweep'] = 0
    assert joy.sweep(now) == 1
    pri = get_kv('commonroom:joy:1', {})
    assert pri['kind']['humor'] > 0 > pri['kind']['news']
    with session() as s:
        link = s.get(CRThreadSource, link.id)
        assert (link.shown, link.liked, link.skipped) == (20, 0, 20)
        assert link.throttle == 0                             # -0.15 each: skipped, but not yet throttled
        for k in s.exec(select(CRPick).where(CRPick.kind == 'news')).all()[:12]:
            s.add(CRFeedback(profile_id=1, thread_id=t['id'], post_id=k.post_id, verdict='meh'))
        s.commit()
    joy._last['sweep'] = 0
    joy.sweep(now + 1)
    with session() as s:
        link = s.get(CRThreadSource, link.id)
        assert link.throttle >= 1 and 'skipped' in link.throttle_why
        assert joy.throttles(s, t['id']) == {sid: link.throttle}
    src = client.get(f"/api/hub/commonroom/threads/{t['id']}/sources").json()['sources'][0]
    assert src['throttle'] >= 1 and src['skipped'] == 20
    r = client.patch(f"/api/hub/commonroom/threads/{t['id']}/sources/{link.id}", json={'throttle': 0})
    assert r.status_code == 200 and r.json()['throttle'] == 0 and r.json()['enabled']
    joy._last['sweep'] = 0
    joy.sweep(now + 2)
    with session() as s:
        assert s.get(CRThreadSource, link.id).throttle == 0  # the owner's undo sticks


def test_judge_reads_taste_memo_owner_words_and_kind_lifts(client, monkeypatch):
    t = thread(client)
    for n in range(6):
        _pick(t, f'https://fake.example/g{n}', kind='showcase', engaged=0.9)
        _pick(t, f'https://fake.example/b{n}', kind='news', engaged=-0.6)
    monkeypatch.setattr(joy, 'MEMO_MIN', 4)
    seen = {}

    def fake(system, user, schema, **k):
        seen['user'] = user
        return {'rules': ['Loves build logs with photos', 'Skips vendor press releases']}
    from bt.hub import ai
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(ai, '_call', fake)
    assert joy.distill(1, force=True) == ['Loves build logs with photos', 'Skips vendor press releases']
    assert '"enjoyed"' in seen['user'] and '"skipped"' in seen['user']
    assert client.put('/api/hub/commonroom/taste', json={'note': 'I like snark'}).status_code == 200
    from bt.db import save_setting
    save_setting('commonroom:joy:1', {'kind': {'showcase': 0.3, 'news': -0.2}})
    with session() as s:
        from bt.commonroom.models import CRThread
        text = teach.prompt(s, s.get(CRThread, t['id']))
    assert 'I like snark' in text and 'Loves build logs' in text
    assert 'loves showcase' in text and 'mostly skips news' in text
    learned = client.get(f"/api/hub/commonroom/threads/{t['id']}/learned").json()
    assert learned['taste'][0].startswith('Loves') and learned['owner_note'] == 'I like snark'


def test_judge_gets_popularity_within_community(client, monkeypatch):
    from bt.commonroom import store
    t = thread(client)
    now = time.time()
    store.upsert_posts([{'platform': 'fake', 'community': 'c/one', 'url': f'https://fake.example/p{n}',
                         'title': f'p{n}', 'score': n, 'published_at': now - 3600} for n in range(30)])
    with session() as s:
        posts = list(s.exec(select(CRPost)).all())
        pop = curate._popularity(s, posts, now)
    top = max(posts, key=lambda p: p.score)
    assert pop[top.id] >= 0.95
    monkeypatch.setattr(curate.ai, '_call', lambda system, user, schema, **k: {'scores': []} if
                        seen.update(user=user, system=system) is None else {})
    seen = {}
    with session() as s:
        from bt.commonroom.models import CRThread
        thr = s.get(CRThread, t['id'])
    curate._judge(thr, '', [top], pop)
    assert 'top 5% of c/one lately' in seen['user']
    assert 'bored person scrolling' in seen['system']
