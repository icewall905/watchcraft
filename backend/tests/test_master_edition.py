"""Master curator P3: the daily edition and the nightly pass."""
import os
import tempfile
import time

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-master-edition-'))

import pytest
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, save_setting, session
from bt.hub import ai, engine, slots
from bt.hub.master import edition, judge, schedule
from bt.hub.models import Alert, ContentItem, Curator, Edition, ItemVerdict, MasterQueue, Story, StoryItem

_n = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    monkeypatch.setattr(ai, 'enabled', lambda: False)
    yield


def featured(s, type='news', final=70.0, story=None, publisher='', pid=1, tier='feature'):
    n = next(_n)
    it = ContentItem(url=f'https://example.org/{n}', type=type, title=f'Item {n}', publisher=publisher or f'pub{n}')
    s.add(it); s.flush()
    if story is not None:
        s.add(StoryItem(item_id=it.id, story_id=story))
    s.add(ItemVerdict(profile_id=pid, item_id=it.id, tier=tier, final=final))
    s.flush()
    return it.id


def story(s):
    st = Story(title='x'); s.add(st); s.flush()
    return st.id


def section(ed, key):
    return next((sec for sec in ed.sections if sec['key'] == key), {'entries': []})


def ids(ed, key):
    return [e['item_id'] for e in section(ed, key).get('entries', [])]


def test_an_edition_puts_the_best_story_first_and_sorts_the_rest():
    with session() as s:
        stories = [story(s) for _ in range(3)]
        lead = featured(s, final=90, story=stories[0])
        second = featured(s, final=80, story=stories[1])
        same_story = featured(s, final=79, story=stories[1])
        read = featured(s, type='project', final=75)
        meme = featured(s, type='meme', final=72)
        featured(s, final=99, tier='keep')
        s.add(Alert(profile_id=1, item_id=read, title='x'))
        s.commit()
        ed = edition.patch(s, 1)
        assert ids(ed, 'lead') == [lead]
        assert ids(ed, 'stories') == [second]  # one entry per story
        assert ids(ed, 'reads') == [read] and ids(ed, 'memes') == [meme]
        assert section(ed, 'alerts')['count'] == 1
        assert same_story not in [e for sec in ed.sections for e in ids(ed, sec['key'])]
        assert ed.day == edition.today()


def test_what_the_reader_saw_stays_put_and_the_rest_can_change():
    with session() as s:
        memes = [featured(s, type='meme', final=80 - i) for i in range(8)]
        ed = edition.patch(s, 1)
        assert ids(ed, 'memes') == memes
        ed.shown_item_ids = memes[:3]
        s.add(ed); s.commit()
        better = featured(s, type='meme', final=99)
        ed = edition.patch(s, 1)
        assert ids(ed, 'memes') == memes[:3] + [better] + memes[3:7]
        # Dismissing something already seen still takes it off the page.
        v = s.get(ItemVerdict, (1, memes[1])); v.tier = 'reject'; s.add(v); s.commit()
        ed = edition.patch(s, 1)
        assert memes[1] not in ids(ed, 'memes') and ids(ed, 'memes')[:2] == [memes[0], memes[2]]
        assert s.exec(select(Edition)).all() == [ed]


def test_story_sources_count_only_what_this_profile_can_see():
    with session() as s:
        st = story(s)
        featured(s, final=90, story=st, publisher='A')
        featured(s, final=50, story=st, publisher='B', tier='keep')
        featured(s, final=50, story=st, publisher='C', tier='reject')
        featured(s, final=50, story=st, publisher='D', pid=2)
        s.commit()
        assert section(edition.patch(s, 1), 'lead')['entries'][0]['sources'] == 2


def test_morning_prose_is_cited_and_optional(monkeypatch):
    calls = []
    def cited(items, instruction, words):
        calls.append(len(items))
        return 'A calm day [1].'
    monkeypatch.setattr(ai, '_cited', cited)
    with session() as s:
        st = story(s)
        lead = featured(s, final=90, story=st, publisher='A')
        featured(s, final=60, story=st, publisher='B', tier='keep')
        featured(s, type='project', final=70)
        s.commit()
        ed = edition.patch(s, 1)
        assert edition.write(s, 1, ed) == {'intro': False, 'summaries': 0}  # no model
        monkeypatch.setattr(ai, 'enabled', lambda: True)
        assert edition.write(s, 1, ed, busy=lambda: True) == {'intro': False, 'summaries': 0}
        assert edition.write(s, 1, ed) == {'intro': True, 'summaries': 1}
        ed = s.exec(select(Edition)).one()
        assert ed.intro == 'A calm day [1].' and ed.intro_refs[0] == lead
        assert section(ed, 'lead')['entries'][0]['summary'] == 'A calm day [1].'
        assert edition.write(s, 1, ed) == {'intro': False, 'summaries': 0}  # written once a day
        assert calls == [2, 2]
        # Later patches keep the summary.
        ed = edition.patch(s, 1)
        assert section(ed, 'lead')['entries'][0]['summary'] == 'A calm day [1].'


# ---------- nightly

def test_the_nightly_window_follows_the_setting():
    save_setting('memory', {'timezone': 'UTC'})
    save_setting('hub_master', {'time': '04:30'})
    day = time.mktime((2026, 11, 3, 0, 0, 0, 0, 0, 0)) - time.timezone  # midnight UTC
    assert schedule.nightly_day(day + 4 * 3600) is None
    assert schedule.nightly_day(day + 5 * 3600) == '2026-11-03'
    assert schedule.nightly_day(day + 9 * 3600) is None
    save_setting('hub_master', {'time': '04:30', 'enabled': False})
    assert schedule.nightly_day(day + 5 * 3600) is None


def test_night_pass_reads_everything_recent_and_runs_once(monkeypatch):
    seen = {}
    monkeypatch.setattr(schedule, 'nightly_day', lambda now=None: '2026-11-03')
    monkeypatch.setattr(judge, 'judge', lambda s, pid, **kw: seen.update(kw) or {'judged': 0, 'skipped': 0, 'stopped': ''})
    with session() as s:
        s.add(Curator(profile_id=1, name='News')); s.commit()
    assert schedule.nightly_due() == [1]
    claimed = schedule.claim_now(1)
    out = schedule.night_pass(1, claimed, '2026-11-03')
    assert seen == {'limit': judge.NIGHT_LIMIT, 'band': False} and 'failed' not in out
    assert schedule.nightly_due() == []
    with session() as s:
        assert s.exec(select(Edition)).one().profile_id == 1


def test_the_scheduler_starts_night_passes_and_gives_back_unused_leases(monkeypatch):
    monkeypatch.setattr(schedule, 'nightly_day', lambda now=None: '2026-11-03')
    monkeypatch.setattr(slots, 'free', lambda: 2)
    spawned = []
    monkeypatch.setattr(slots, 'spawn', lambda key, kind, ref, pid, label, fn, *a: spawned.append((key, fn.__name__)) or False)
    with session() as s:
        s.add(Curator(profile_id=1, name='News', enabled=False)); s.commit()
    engine.dispatch_due()
    assert spawned[0] == ('master-1', 'night_pass')
    assert schedule.claim(1, time.time()) is not None  # spawn failed, so the lease was released


def test_a_light_pass_and_a_night_pass_share_one_lease():
    schedule.mark(1, time.time() - 3600)
    assert schedule.claim(1) is not None
    assert schedule.claim_now(1) is None


def test_the_nightly_time_setting_is_validated():
    from fastapi.testclient import TestClient
    from bt.main import app
    with TestClient(app) as c:
        assert c.put('/api/settings/hub_master', json={'time': '25:00'}).status_code == 400
        assert c.put('/api/settings/hub_master', json={'enabled': 'yes'}).status_code == 400
        assert c.put('/api/settings/hub_master', json={'time': '05:15', 'enabled': False}).status_code == 200
        assert c.get('/api/settings').json()['hub_master'] == {'time': '05:15', 'enabled': False}


# ---------- review regressions

def test_prose_and_source_counts_are_really_saved(monkeypatch):
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(ai, '_cited', lambda items, instruction, words: 'Said [1].')
    with session() as s:
        st = story(s)
        lead = featured(s, final=90, story=st, publisher='A')
        featured(s, final=60, story=st, publisher='B', tier='keep')
        s.commit()
        edition.write(s, 1, edition.patch(s, 1))
    with session() as s:  # a fresh session, not the identity map
        entry = section(s.exec(select(Edition)).one(), 'lead')['entries'][0]
        assert entry['summary'] == 'Said [1].' and entry['summary_refs'][0] == lead and entry['sources'] == 2
        featured(s, final=50, story=st, publisher='C', tier='keep')
        s.commit()
        edition.patch(s, 1)
    with session() as s:
        entry = section(s.exec(select(Edition)).one(), 'lead')['entries'][0]
        assert entry['sources'] == 3 and entry['summary_refs'][0] == lead


def test_a_story_keeps_its_summary_when_it_moves_up_to_lead():
    with session() as s:
        a, b = story(s), story(s)
        lead = featured(s, final=90, story=a)
        second = featured(s, final=80, story=b)
        ed = edition.patch(s, 1)
        sections = [dict(sec) for sec in ed.sections]
        sections[1] = {**sections[1], 'entries': [{**sections[1]['entries'][0], 'summary': 'S [1].', 'summary_refs': [second]}]}
        ed.sections = sections; s.add(ed); s.commit()
        v = s.get(ItemVerdict, (1, lead)); v.tier = 'reject'; s.add(v); s.commit()
        entry = section(edition.patch(s, 1), 'lead')['entries'][0]
        assert entry['item_id'] == second and entry['summary'] == 'S [1].' and entry['summary_refs'] == [second]


def test_a_demoted_item_leaves_even_after_it_was_seen():
    with session() as s:
        m = featured(s, type='meme')
        ed = edition.patch(s, 1); ed.shown_item_ids = [m]; s.add(ed); s.commit()
        v = s.get(ItemVerdict, (1, m)); v.tier, v.overruled = 'set_aside', 'demoted'; s.add(v); s.commit()
        assert ids(edition.patch(s, 1), 'memes') == []


def test_the_nightly_tick_leaves_a_running_pass_alone():
    claimed = schedule.claim_now(1)
    with session() as s:
        row = s.get(MasterQueue, 1)
        assert row.lease_until - time.time() > schedule.LEASE  # long enough for a full night's reading
        gen = row.gen
    assert schedule.claim_now(1) is None
    with session() as s:
        assert s.get(MasterQueue, 1).gen == gen == claimed
