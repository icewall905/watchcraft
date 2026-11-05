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
from bt.hub.models import Alert, ContentItem, Curator, CuratorDiscovery, Edition, ItemVerdict, MasterQueue, Story, StoryItem

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
    s.add(CuratorDiscovery(curator_id=1, item_id=it.id, profile_id=pid))
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
        keeper = featured(s, final=65, tier='keep')
        s.add(Alert(profile_id=1, item_id=read, title='x'))
        s.commit()
        ed = edition.patch(s, 1)
        assert ids(ed, 'lead') == [lead]
        assert ids(ed, 'stories') == [second, keeper]  # one entry per story; keep fills after features
        assert ids(ed, 'reads') == [read] and ids(ed, 'memes') == [meme]
        assert section(ed, 'alerts')['count'] == 1
        assert same_story not in [e for sec in ed.sections for e in ids(ed, sec['key'])]
        assert ed.day == edition.today()


def test_today_fills_a_larger_edition_from_featured_then_keeper_pool():
    with session() as s:
        # Candidates comfortably exceed the old 20-entry total and each section's
        # featured subset is smaller than its new capacity.
        stories = [story(s) for _ in range(36)]
        story_ids = [featured(s, story=st, final=90 + i if i < 20 else 60 + i, tier='feature' if i < 20 else 'keep')
                     for i, st in enumerate(stories)]
        read_ids = [featured(s, type='project', final=90 + i if i < 12 else 60 + i, tier='feature' if i < 12 else 'keep')
                    for i in range(28)]
        meme_ids = [featured(s, type='meme', final=90 + i if i < 8 else 60 + i, tier='feature' if i < 8 else 'keep')
                    for i in range(15)]
        s.commit()

        ed = edition.patch(s, 1)
        assert len(ids(ed, 'lead')) == 1
        assert len(ids(ed, 'stories')) == 29
        assert len(ids(ed, 'reads')) == 20
        assert len(ids(ed, 'memes')) == 10
        selected = [*ids(ed, 'lead'), *ids(ed, 'stories')]
        assert set(selected[:20]) == set(story_ids[:20])
        assert len(set(selected)) == 30
        assert set(ids(ed, 'reads')[:12]) == set(read_ids[:12])
        assert set(ids(ed, 'memes')[:8]) == set(meme_ids[:8])


def test_existing_today_edition_expands_on_patch_without_moving_seen_items():
    with session() as s:
        featured_memes = [featured(s, type='meme', final=80 - i) for i in range(3)]
        ed = edition.patch(s, 1)
        ed.shown_item_ids = [featured_memes[1]]
        s.add(ed)
        for i in range(10):
            featured(s, type='meme', final=99 - i, tier='keep')
        s.commit()

        patched = edition.patch(s, 1)
        selected = ids(patched, 'memes')
        assert len(selected) == 10
        assert selected[0] == featured_memes[1]


def test_patch_refreshes_delivery_state_loaded_by_an_older_session():
    with session() as stale:
        item = featured(stale, type='meme')
        ed = edition.patch(stale, 1)
        with session() as current:
            latest = current.exec(select(Edition).where(Edition.profile_id == 1)).one()
            latest.shown_item_ids = [item]
            current.add(latest)
            current.commit()

        patched = edition.patch(stale, 1)
        assert patched.shown_item_ids == [item]


def test_a_fresh_keeper_can_outrank_a_stale_feature():
    now = time.time()
    with session() as s:
        old = featured(s, type='project', final=90, tier='feature')
        fresh = featured(s, type='project', final=85, tier='keep')
        item = s.get(ContentItem, old)
        item.published_at = now - 5 * 86400
        s.add(item)
        s.commit()

        assert ids(edition.patch(s, 1, now), 'reads')[0] == fresh


def test_unclustered_news_is_still_presented_as_a_story():
    with session() as s:
        item = featured(s, type='article', tier='keep')
        ed = edition.patch(s, 1)
        assert ids(ed, 'lead') == [item]
        assert item not in ids(ed, 'reads')


def test_what_the_reader_saw_stays_put_and_the_rest_can_change():
    with session() as s:
        memes = [featured(s, type='meme', final=80 - i) for i in range(8)]
        ed = edition.patch(s, 1)
        assert ids(ed, 'memes') == memes
        ed.shown_item_ids = memes[:3]
        s.add(ed); s.commit()
        better = featured(s, type='meme', final=99)
        ed = edition.patch(s, 1)
        assert ids(ed, 'memes') == memes[:3] + [better] + memes[3:]
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
    assert schedule.nightly_day(day + 9 * 3600) == '2026-11-03'  # catch up after a busy morning
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


def test_patching_drops_a_summary_when_one_of_its_sources_is_no_longer_visible(monkeypatch):
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(ai, '_cited', lambda items, instruction, words: 'Grounded summary [1].')
    with session() as s:
        st = story(s)
        lead = featured(s, final=90, story=st, publisher='A')
        member = featured(s, final=60, story=st, publisher='B', tier='keep')
        edition.write(s, 1, edition.patch(s, 1))
        entry = section(s.exec(select(Edition)).one(), 'lead')['entries'][0]
        assert entry['summary_refs'] == [lead, member]

        verdict = s.get(ItemVerdict, (1, member))
        verdict.tier = 'set_aside'
        s.add(verdict)
        s.commit()

        entry = section(edition.patch(s, 1), 'lead')['entries'][0]
        assert 'summary' not in entry and 'summary_refs' not in entry


def test_slow_prose_write_preserves_a_concurrently_expanded_delivered_edition(monkeypatch):
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    with session() as s:
        st = story(s)
        lead = featured(s, final=90, story=st, publisher='A')
        featured(s, final=60, story=st, publisher='B', tier='keep')
        original = edition.patch(s, 1)
        generated = False

        def cited(items, instruction, words):
            nonlocal generated
            if not generated:
                generated = True
                with session() as concurrent:
                    new_story = story(concurrent)
                    new_item = featured(concurrent, final=99, story=new_story)
                    current = concurrent.exec(select(Edition).where(Edition.profile_id == 1)).one()
                    current.shown_item_ids = [lead]
                    concurrent.add(current)
                    concurrent.commit()
                    edition.patch(concurrent, 1)
                    assert new_item in [item for sec in concurrent.get(Edition, current.id).sections
                                        for item in [entry['item_id'] for entry in sec.get('entries', [])]]
            return 'Cited summary [1].'

        monkeypatch.setattr(ai, '_cited', cited)
        edition.write(s, 1, original)
        latest = s.exec(select(Edition).where(Edition.profile_id == 1)).one()
        assert latest.shown_item_ids == [lead]
        assert any(entry['item_id'] != lead for sec in latest.sections
                   for entry in sec.get('entries', []))


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


def test_new_day_rotates_delivered_items_but_keeps_unseen_candidates(monkeypatch):
    now = time.time()
    monkeypatch.setattr(edition, 'today', lambda timestamp=None: '2026-11-03' if (timestamp or now) < now + 86400 else '2026-11-04')
    with session() as s:
        delivered = featured(s, type='project', final=99)
        unseen = featured(s, type='project', final=90)
        yesterday = edition.patch(s, 1, now)
        yesterday.shown_item_ids = [delivered]
        s.add(yesterday); s.commit()
        fresh = featured(s, type='project', final=95)
        tomorrow = edition.patch(s, 1, now + 86400)
        assert ids(tomorrow, 'reads') == [fresh, unseen]
        # Another profile's viewing history must not consume this profile's finds.
        s.add(Edition(profile_id=2, day='2026-11-03', shown_item_ids=[fresh]))
        s.commit()
        assert ids(edition.patch(s, 1, now + 86400), 'reads') == [fresh, unseen]


@pytest.mark.parametrize('state', [{'read_at': 1}, {'dismissed': True}, {'archived': True}])
def test_reader_actions_remove_even_pinned_entries(state):
    from bt.hub.models import ItemState
    with session() as s:
        item = featured(s, type='project')
        ed = edition.patch(s, 1)
        ed.shown_item_ids = [item]
        s.add(ed); s.commit()
        s.add(ItemState(profile_id=1, item_id=item, **state)); s.commit()
        assert ids(edition.patch(s, 1), 'reads') == []


def test_freshness_breaks_historical_score_dominance_and_expires_stale_news():
    now = time.time()
    with session() as s:
        old = featured(s, final=99, story=story(s))
        fresh = featured(s, final=85, story=story(s))
        expired = featured(s, final=100, story=story(s))
        evergreen = featured(s, type='project', final=80)
        for iid, age in [(old, 5), (expired, 8), (evergreen, 20)]:
            item = s.get(ContentItem, iid)
            item.published_at = now - age * 86400
            s.add(item)
        s.commit()
        ed = edition.patch(s, 1, now)
        assert ids(ed, 'lead') == [fresh]
        assert ids(ed, 'stories') == [old]
        assert ids(ed, 'reads') == [evergreen]


def test_reader_promotion_can_restore_an_old_previously_delivered_item():
    with session() as s:
        item = featured(s, type='project')
        row = s.get(ContentItem, item); row.published_at = time.time() - 100 * 86400
        s.add(row)
        s.add(Edition(profile_id=1, day='2000-01-01', shown_item_ids=[item]))
        verdict = s.get(ItemVerdict, (1, item)); verdict.overruled = 'promoted'; s.add(verdict)
        s.commit()
        assert ids(edition.patch(s, 1), 'reads') == [item]


@pytest.mark.parametrize('promoted', [False, True])
def test_patch_repairs_already_pinned_previous_day_duplicates(promoted):
    with session() as s:
        old = featured(s, type='project', final=99)
        unseen = featured(s, type='project', final=80)
        current = edition.patch(s, 1)
        current.shown_item_ids = [old]
        current.intro, current.intro_refs = 'Yesterday again [1].', [old]
        s.add(current)
        s.add(Edition(profile_id=1, day='2000-01-01', shown_item_ids=[old],
                      sections=[{'key': 'reads', 'entries': [{'item_id': old}, {'item_id': unseen}]}]))
        if promoted:
            verdict = s.get(ItemVerdict, (1, old)); verdict.overruled = 'promoted'; s.add(verdict)
        s.commit()
        patched = edition.patch(s, 1)
        assert ids(patched, 'reads') == ([old, unseen] if promoted else [unseen])
        assert patched.intro == ('Yesterday again [1].' if promoted else '')
