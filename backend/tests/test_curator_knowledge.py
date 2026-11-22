"""Teachings show what was learned and why, rejections stick, the nightly weave builds versioned knowledge
that consumers read, and chat corrections change it only when accepted."""
import time

import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401  (isolated DB/API fixture)

from bt.agent import memory
from bt.db import session
from bt.hub.master import prefs
from bt.hub.models import ContentItem, HubLesson, HubPreferenceCore, ItemState, ItemVerdict, MoodBoard, MoodFeedback
from bt.knowledge import chat, collect, render, teachings, weave
from bt.knowledge.models import KnowledgeChat, ProfileKnowledge, Teaching
from bt.models import Feedback, Lesson, Profile, Video
from bt.music.models import MusicFavorite


class FakeModel:
    """Answers by schema: the weave's ops, its audit, the collectors, the hub learner and the chat."""

    def __init__(self, monkeypatch):
        self.ops = []
        self.audit = {'ok': True, 'issues': []}
        self.collect = {'teachings': []}
        self.lessons = {'lessons': []}
        self.chat = {'reply': 'Got it.', 'ops': []}
        self.calls = []
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
        monkeypatch.setattr('bt.hub.ai._call', self)
        monkeypatch.setattr('bt.hub.master.judge.video_busy', lambda: False)

    def __call__(self, system, user, schema, **kw):
        self.calls.append((system, user, kw))
        if schema is weave.OPS_SCHEMA:
            value = self.ops(user) if callable(self.ops) else self.ops
            return value
        if schema is weave.AUDIT_SCHEMA:
            return self.audit
        if schema is collect.SCHEMA:
            return self.collect(user) if callable(self.collect) else self.collect
        if schema is prefs.SCHEMA:
            return self.lessons
        if schema is chat.SCHEMA:
            if isinstance(self.chat, Exception):
                raise self.chat
            return self.chat(user) if callable(self.chat) else self.chat
        raise AssertionError('unexpected model call')


@pytest.fixture
def model(monkeypatch, client):  # noqa: F811
    return FakeModel(monkeypatch)


def pid() -> int:
    with session() as s:
        return s.exec(select(Profile)).first().id


def items(n=2, type='news', title='Harbour bridge council vote'):
    with session() as s:
        rows = [ContentItem(url=f'https://e.test/{time.time_ns()}-{i}', type=type, title=title, first_seen=100) for i in range(n)]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def hub_lesson(p, ids, text='More local harbour reporting', scope='news', polarity=1, **kw):
    with session() as s:
        for i in ids:
            if i > 0:
                s.add(ItemState(profile_id=p, item_id=i, saved=True, saved_at=200))
        lesson = HubLesson(profile_id=p, text=text, scope=scope, polarity=polarity, source_item_ids=ids, evidence=len(ids), **kw)
        s.add(lesson)
        s.commit()
        return lesson.id


def teaching_for(p, origin, origin_id):
    with session() as s:
        teachings.sync(s, p)
        s.commit()
        return s.exec(select(Teaching).where(Teaching.profile_id == p, Teaching.origin == origin,
                                             Teaching.origin_id == str(origin_id))).one()


def video_lesson(p, text='Avoid loud reaction videos', rating=-1, short=False):
    with session() as s:
        s.add(Video(id='abcdefghijk' if not short else 'shortshort1', title='LOUD reaction', is_short=short))
        fb = Feedback(profile_id=p, video_id='abcdefghijk' if not short else 'shortshort1', rating=rating, text='too loud', processed=True)
        s.add(fb)
        s.flush()
        le = Lesson(profile_id=p, text=text, source_feedback_ids=[fb.id])
        s.add(le)
        s.commit()
        return le.id, fb.id


# ---------- what and why

def test_teachings_mirror_lessons_with_signals_and_are_idempotent(client):  # noqa: F811
    p = pid()
    ids = items()
    with session() as s:
        s.add(ItemVerdict(profile_id=p, item_id=ids[1], overruled='promoted', overruled_at=300))
        s.commit()
    lid = hub_lesson(p, ids, reason='You saved two harbour stories.')
    le, fb = video_lesson(p, short=True)
    t = teaching_for(p, 'hub_lesson', lid)
    assert t.status == 'pending' and t.section == 'news' and t.surface == 'news'
    assert t.reason == 'You saved two harbour stories.'
    kinds = {sig['kind'] for sig in t.signals}
    assert {'save', 'overrule_up'} <= kinds and all(sig['title'] for sig in t.signals)
    v = teaching_for(p, 'lesson', le)
    assert v.surface == 'short' and v.section == 'shorts'
    assert v.signals[0]['kind'] == 'dislike' and v.signals[0]['note'] == 'too loud'
    with session() as s:
        teachings.sync(s, p)
        s.commit()
        assert len(s.exec(select(Teaching).where(Teaching.profile_id == p)).all()) == 2


def test_typed_curator_terms_and_hidden_languages_resolve_to_readable_signals(client):  # noqa: F811
    p = pid()
    from bt.hub.models import Curator
    with session() as s:
        cur = Curator(profile_id=p, name='Memes', tuned_down=['crypto'])
        profile = s.get(Profile, p)
        profile.prefs = {'hidden_meme_languages': ['de']}
        s.add_all([cur, profile])
        s.commit()
        marker = prefs._feedback_marker(cur.id, 'down', 'crypto')
        lang = prefs._language_marker('de')
    lid = hub_lesson(p, [marker, lang], text='Fewer crypto memes', scope='meme', polarity=-1)
    t = teaching_for(p, 'hub_lesson', lid)
    assert {sig['kind'] for sig in t.signals} == {'typed_down', 'hide_language'}


# ---------- rejection sticks

def test_rejected_hub_teaching_is_never_relearned_nor_paraphrased(model):
    p = pid()
    ids = items()
    lid = hub_lesson(p, ids)
    t = teaching_for(p, 'hub_lesson', lid)
    with session() as s:
        teachings.reject(s, p, t.id, '')
        s.commit()
        assert s.get(HubLesson, lid).active is False
    model.lessons = {'lessons': [
        {'text': 'More local harbour reporting', 'scope': 'news', 'polarity': 1, 'evidence_ids': ids, 'reason': 'x'},
        {'text': 'Enjoys city bridge politics', 'scope': 'news', 'polarity': 1, 'evidence_ids': ids, 'reason': 'x'}]}
    with session() as s:
        prefs.collect(s, p, at=1000)
        active = s.exec(select(HubLesson).where(HubLesson.profile_id == p, HubLesson.active == True)).all()  # noqa: E712
        assert active == []
    user = next(c[1] for c in model.calls)
    assert 'REJECTED BY THE USER' in user and 'More local harbour reporting' in user  # as escaped data


def test_deleting_a_lesson_through_older_screens_leaves_a_tombstone(client):  # noqa: F811
    p = pid()
    lid = hub_lesson(p, items())
    assert client.delete(f'/api/hub/curator/preferences/{lid}').status_code == 200
    le, fb = video_lesson(p)
    assert client.delete(f'/api/lessons/{le}').status_code == 200
    with session() as s:
        dead = teachings.rejected(s, p)
        assert {t.origin for t in dead} == {'hub_lesson', 'lesson'}
        assert teachings.blocked(dead, 'something else entirely', {f'fb:{fb}'})


def test_reject_with_correction_records_what_was_meant_and_restore_undoes(model, client):  # noqa: F811
    p = pid()
    lid = hub_lesson(p, items())
    t = teaching_for(p, 'hub_lesson', lid)
    model.chat = {'reply': 'I see: transport, not harbours.', 'ops': [
        {'op': 'add', 'section': 'news', 'sid': None, 'text': 'Local public transport reporting', 'polarity': 1}]}
    r = client.post(f'/api/curator/teachings/{t.id}/reject', json={'correction': 'I care about trams, not the harbour'})
    body = r.json()
    assert body['teaching']['status'] == 'rejected'
    assert body['correction']['user_authored'] and body['correction']['status'] == 'pending'
    assert body['message']['proposal_status'] == 'pending'
    # Applying the proposal folds the correction in now; it is not woven again tonight.
    applied = client.post(f"/api/curator/chat/{body['message']['id']}/accept").json()
    news = next(sec for sec in applied['sections'] if sec['key'] == 'news')
    assert any(st['text'] == 'Local public transport reporting' and st['locked'] for st in news['statements'])
    with session() as s:
        assert s.get(Teaching, body['correction']['id']).status == 'merged'
    restored = client.post(f'/api/curator/teachings/{t.id}/restore').json()
    assert restored['status'] == 'pending'
    with session() as s:
        assert s.get(HubLesson, lid).active is True


# ---------- new surfaces

def test_mood_board_reviews_become_board_scoped_teachings(model):
    p = pid()
    with session() as s:
        board = MoodBoard(profile_id=p, name='Cabin', interests='cosy cabins')
        s.add(board)
        s.flush()
        s.add_all([MoodFeedback(pin_id=1, board_id=board.id, profile_id=p, verdict='bad', title='Snowy steel loft'),
                   MoodFeedback(pin_id=2, board_id=board.id, profile_id=p, verdict='bad', title='Cold concrete'),
                   MoodFeedback(pin_id=3, board_id=board.id, profile_id=p, verdict='good', title='Fireplace')])
        s.commit()
        bid = board.id
    model.collect = {'teachings': [
        {'text': 'Warm wood, not cold industrial', 'polarity': -1, 'reason': 'Rejected two cold interiors', 'evidence_ids': [1, 2]},
        {'text': 'Only fireplaces', 'polarity': 1, 'reason': 'one pin', 'evidence_ids': [3]},
        {'text': 'Invented', 'polarity': 1, 'reason': 'x', 'evidence_ids': [99]}]}
    with session() as s:
        assert collect.collect_mood(s, p) == 1
        s.commit()
        t = s.exec(select(Teaching).where(Teaching.origin == 'mood')).one()
        assert t.board_id == bid and t.section == 'moodboards' and {sig['kind'] for sig in t.signals} == {'not_fit'}
        assert collect.collect_mood(s, p) == 0  # the cursor moved; nothing new to learn


def test_music_dislike_can_teach_alone_but_a_single_favourite_cannot(model):
    p = pid()
    with session() as s:
        s.add_all([MusicFavorite(profile_id=p, kind='dislike', ref='vid1', name='Screamo song'),
                   MusicFavorite(profile_id=p, kind='track', ref='vid2', name='Lofi beat')])
        s.commit()
    model.collect = {'teachings': [
        {'text': 'Less screamo', 'polarity': -1, 'reason': 'Disliked a screamo song', 'evidence_ids': [1]},
        {'text': 'Loves lofi', 'polarity': 1, 'reason': 'One favourite', 'evidence_ids': [2]}]}
    with session() as s:
        assert collect.collect_music(s, p) == 1
        s.commit()
        assert s.exec(select(Teaching.text).where(Teaching.origin == 'music')).all() == ['Less screamo']


# ---------- the weave

def seeded(model):
    p = pid()
    lid = hub_lesson(p, items())
    le, _fb = video_lesson(p)
    assert weave.weave(p)['status'] == 'unchanged'  # first run imports everything as version 1
    return p, lid, le


def test_first_weave_imports_existing_lessons_and_retires_them(model):
    p, lid, le = seeded(model)
    with session() as s:
        pk = render.current(s, p)
        assert pk.version == 1 and pk.cause == 'migration'
        texts = {st['text'] for sec in pk.sections for st in sec['statements']}
        assert texts == {'More local harbour reporting', 'Avoid loud reaction videos'}
        assert s.get(HubLesson, lid).active is False and s.get(Lesson, le).active is False
        assert {t.status for t in s.exec(select(Teaching)).all()} == {'merged'}


def test_nightly_weave_merges_new_teachings_with_provenance(model):
    p, *_ = seeded(model)
    new = hub_lesson(p, items(title='Tram line extension'), text='More tram network news')
    t = teaching_for(p, 'hub_lesson', new)
    model.ops = {'ops': [{'op': 'add', 'sid': None, 'text': 'Public transport expansions', 'polarity': 1,
                          'confidence': 'tentative', 'teaching_ids': [t.id]}],
                 'dispositions': [{'teaching_id': t.id, 'outcome': 'merged', 'note': ''}]}
    result = weave.weave(p)
    assert result['status'] == 'woven' and result['merged'] == 1
    with session() as s:
        pk = render.current(s, p)
        assert pk.version == 2 and pk.changes['added'][0]['text'] == 'Public transport expansions'
        st = next(st for sec in pk.sections for st in sec['statements'] if st['text'] == 'Public transport expansions')
        assert st['teaching_ids'] == [t.id] and st['confidence'] == 'tentative' and not st['locked']
        row = s.get(Teaching, t.id)
        assert row.status == 'merged' and row.merged_version_id == pk.id
        assert s.get(HubLesson, new).active is False
        assert 'Public transport expansions' in s.get(HubPreferenceCore, p).text


@pytest.mark.parametrize('problem', ['missing_disposition', 'locked_edit', 'audit', 'unknown_id', 'rejected_again'])
def test_unsafe_weave_proposals_leave_teachings_pending(model, problem):
    p, *_ = seeded(model)
    with session() as s:
        pk = render.current(s, p)
        locked = next(st for sec in pk.sections for st in sec['statements'] if st['text'] == 'More local harbour reporting')
        sec = next(sec for sec in pk.sections if any(x['sid'] == locked['sid'] for x in sec['statements']))
    # Make the imported statement the person's own.
    with session() as s:
        cur = render.current(s, p)
        sections = weave._normalized(cur.sections)
        sections = weave.apply_ops(sections, sec['key'], [{'op': 'refine', 'sid': locked['sid'], 'text': locked['text'],
                                                            'polarity': 1, 'teaching_ids': []}], {}, now=1, user_edit=True)
        weave.write_version(s, p, sections, 'edit', cur)
        s.commit()
    new = hub_lesson(p, items(), text='Less harbour reporting', polarity=-1)
    t = teaching_for(p, 'hub_lesson', new)
    op = {'op': 'add', 'sid': None, 'text': 'Fewer port stories', 'polarity': -1, 'confidence': 'tentative', 'teaching_ids': [t.id]}
    disp = [{'teaching_id': t.id, 'outcome': 'merged', 'note': ''}]
    if problem == 'missing_disposition':
        disp = []
    elif problem == 'locked_edit':
        op = {**op, 'op': 'refine', 'sid': locked['sid']}
    elif problem == 'audit':
        model.audit = {'ok': False, 'issues': ['reversed an earlier preference']}
    elif problem == 'unknown_id':
        op = {**op, 'teaching_ids': [t.id, 99999]}
    elif problem == 'rejected_again':
        with session() as s:
            s.add(Teaching(profile_id=p, section='news', polarity=-1, text='Fewer port stories', status='rejected',
                           origin='user', origin_id='x', rejected_at=1))
            s.commit()
    model.ops = {'ops': [op], 'dispositions': disp}
    assert weave.weave(p)['status'] == 'failed'
    with session() as s:
        assert s.get(Teaching, t.id).status == 'pending'
        assert s.get(HubLesson, new).active is True
        assert render.current(s, p).version == 2


def test_restoring_a_version_reopens_what_was_merged_after_it(model):
    p, *_ = seeded(model)
    new = hub_lesson(p, items(), text='More tram network news')
    t = teaching_for(p, 'hub_lesson', new)
    model.ops = {'ops': [{'op': 'add', 'sid': None, 'text': 'Trams', 'polarity': 1, 'confidence': 'tentative', 'teaching_ids': [t.id]}],
                 'dispositions': [{'teaching_id': t.id, 'outcome': 'merged', 'note': ''}]}
    weave.weave(p)
    with session() as s:
        first = s.exec(select(ProfileKnowledge).where(ProfileKnowledge.version == 1)).one()
        pk = weave.restore(s, p, first.id)
        s.commit()
        assert pk.version == 3 and pk.cause == 'undo' and pk.changes['removed'][0]['text'] == 'Trams'
        assert s.get(Teaching, t.id).status == 'pending' and s.get(HubLesson, new).active is True


# ---------- consumers

def test_consumers_read_the_knowledge(model):
    p, *_ = seeded(model)
    with session() as s:
        board = MoodBoard(profile_id=p, name='Cabin', interests='cabins')
        s.add(board)
        s.flush()
        cur = render.current(s, p)
        sections = weave.apply_ops(weave._normalized(cur.sections), 'moodboards', [
            {'op': 'add', 'sid': None, 'text': 'Warm wooden interiors', 'polarity': 1, 'teaching_ids': []}], {}, now=1, user_edit=True)
        weave.write_version(s, p, sections, 'edit', cur)
        s.commit()
        core, corrections = memory.select_memory(s, p, memory.active(s, p, 'video'), 'video')
        assert 'Avoid loud reaction videos' in core and 'harbour' not in core and corrections == []
        article_core, _ = memory.select_memory(s, p, [], 'article')
        assert 'harbour' in article_core
        nudges = prefs.nudges(s, p)
        assert any(n.text == 'More local harbour reporting' and n.scope == 'news' for n in nudges)
        from bt.hub.moodboards import feedback_lessons
        assert 'Warm wooden interiors' in feedback_lessons(s, board.id)
        from bt.hub.master.judge import reader
        assert 'More local harbour reporting' in reader(s, p, include_articles=False)


def test_without_knowledge_consumers_keep_the_old_paths(client):  # noqa: F811
    p = pid()
    video_lesson(p)
    with session() as s:
        core, corrections = memory.select_memory(s, p, memory.active(s, p, 'video'), 'video')
        assert core == '' and [le.text for le in corrections] == ['Avoid loud reaction videos']


# ---------- chat

def test_chat_proposal_applies_only_when_accepted_and_goes_stale(model, client):  # noqa: F811
    p, *_ = seeded(model)
    with session() as s:
        st = next(st for sec in render.current(s, p).sections for st in sec['statements']
                  if st['text'] == 'Avoid loud reaction videos')
    model.chat = {'reply': 'Loud is fine for music videos.', 'ops': [
        {'op': 'edit', 'section': 'videos', 'sid': st['sid'], 'text': 'Avoid loud reaction videos, except music', 'polarity': -1},
        {'op': 'remove', 'section': 'videos', 'sid': 'nope', 'text': None, 'polarity': 1}]}
    msgs = client.post('/api/curator/chat', json={'text': 'Loud music videos are fine'}).json()['messages']
    bot = msgs[1]
    assert bot['proposal_status'] == 'pending' and len(bot['proposal']['ops']) == 1  # unknown sid dropped
    assert client.get('/api/curator/knowledge').json()['version']['version'] == 1  # nothing applied yet
    second = client.post('/api/curator/chat', json={'text': 'and remove it'}).json()['messages'][1]
    applied = client.post(f"/api/curator/chat/{bot['id']}/accept").json()
    stmt = next(x for sec in applied['sections'] for x in sec['statements'] if x['sid'] == st['sid'])
    assert stmt['text'] == 'Avoid loud reaction videos, except music' and stmt['locked'] and stmt['confidence'] == 'explicit'
    assert applied['version']['cause'] == 'chat'
    assert client.post(f"/api/curator/chat/{second['id']}/accept").status_code == 409
    assert client.get('/api/curator/chat').json()['messages'][-1]['proposal_status'] == 'stale'


def test_chat_without_a_model_keeps_the_note_for_tonight(model, client):  # noqa: F811
    p = pid()
    model.chat = RuntimeError('timeout')
    bot = client.post('/api/curator/chat', json={'text': 'I never want horror'}).json()['messages'][1]
    assert bot['proposal_status'] == 'none'
    with session() as s:
        t = s.exec(select(Teaching).where(Teaching.origin == 'chat_note')).one()
        assert t.user_authored and t.status == 'pending' and t.text == 'I never want horror'


def test_rejecting_a_merged_teaching_offers_to_remove_its_statement(model, client):  # noqa: F811
    p, lid, _ = seeded(model)
    t = teaching_for(p, 'hub_lesson', lid)
    body = client.post(f'/api/curator/teachings/{t.id}/reject', json={}).json()
    assert body['message']['proposal']['ops'][0]['op'] == 'remove'
    applied = client.post(f"/api/curator/chat/{body['message']['id']}/accept").json()
    assert all(st['text'] != 'More local harbour reporting' for sec in applied['sections'] for st in sec['statements'])


# ---------- API boundaries

def test_another_profile_cannot_touch_my_knowledge(model, client):  # noqa: F811
    p, lid, _ = seeded(model)
    t = teaching_for(p, 'hub_lesson', lid)
    other = client.post('/api/profiles', json={'name': 'Other'}).json()['id']
    h = {'X-Profile-Id': str(other)}
    with session() as s:
        vid = render.current(s, p).id
        sid = render.current(s, p).sections[0]['statements'][0]['sid'] if render.current(s, p).sections[0]['statements'] else \
            next(st['sid'] for sec in render.current(s, p).sections for st in sec['statements'])
    assert client.post(f'/api/curator/teachings/{t.id}/reject', json={}, headers=h).status_code == 404
    assert client.get(f'/api/curator/knowledge/versions/{vid}', headers=h).status_code == 404
    assert client.post(f'/api/curator/knowledge/versions/{vid}/restore', headers=h).status_code == 404
    assert client.get(f'/api/curator/knowledge/statements/{sid}/why', headers=h).status_code == 404
    assert client.get('/api/curator/knowledge', headers=h).json()['version'] is None


def test_statement_edit_why_and_history_endpoints(model, client):  # noqa: F811
    p, lid, _ = seeded(model)
    k = client.get('/api/curator/knowledge').json()
    st = next(st for sec in k['sections'] for st in sec['statements'] if st['text'] == 'More local harbour reporting')
    why = client.get(f"/api/curator/knowledge/statements/{st['sid']}/why").json()
    assert why['teachings'][0]['signals'][0]['kind'] == 'save'
    edited = client.patch(f"/api/curator/knowledge/statements/{st['sid']}", json={'text': 'More harbour and ferry news'}).json()
    assert edited['version']['changes']['changed'][0]['after'] == 'More harbour and ferry news'
    client.patch(f"/api/curator/knowledge/statements/{st['sid']}", json={'remove': True})
    history = client.get('/api/curator/knowledge').json()['history']
    assert [h['cause'] for h in history] == ['edit', 'edit', 'migration']
    added = client.post('/api/curator/teachings', json={'text': 'No horror', 'section': 'videos', 'polarity': -1}).json()
    pending = client.get('/api/curator/teachings?status=pending').json()['teachings']
    assert [x['id'] for x in pending] == [added['id']] and pending[0]['user_authored']


# ---------- review regressions

def test_a_reused_lesson_id_is_mirrored_as_a_new_teaching(client):  # noqa: F811
    p = pid()
    lid = hub_lesson(p, items())
    assert client.delete(f'/api/hub/curator/preferences/{lid}').status_code == 200
    with session() as s:
        again = HubLesson(id=lid, profile_id=p, text='Fresh rule', scope='news', source_item_ids=[], created_at=time.time() + 5)
        s.add(again)
        s.commit()
    t = teaching_for(p, 'hub_lesson', lid)
    assert t.text == 'Fresh rule' and t.status == 'pending'
    with session() as s:
        dead = s.exec(select(Teaching).where(Teaching.status == 'rejected')).one()
        teachings.restore(s, p, dead.id)  # restoring the old tombstone must not touch the new lesson
        s.commit()
        assert s.get(HubLesson, lid).text == 'Fresh rule'


def test_scoped_video_rules_and_language_hides_are_not_woven(client):  # noqa: F811
    p = pid()
    with session() as s:
        s.add(Lesson(profile_id=p, text='Only gentle videos here', category_id=7))
        profile = s.get(Profile, p)
        profile.prefs = {'hidden_meme_languages': ['de']}
        s.add(profile)
        s.add(HubLesson(profile_id=p, text='Fewer memes in the de language', scope='meme', polarity=-1,
                        source_item_ids=[prefs._language_marker('de')]))
        s.commit()
        teachings.sync(s, p)
        s.commit()
        assert s.exec(select(Teaching)).all() == []


def test_undo_never_revives_a_rejected_teaching(model):
    p, *_ = seeded(model)
    new = hub_lesson(p, items(), text='More tram network news')
    t = teaching_for(p, 'hub_lesson', new)
    model.ops = {'ops': [{'op': 'add', 'sid': None, 'text': 'Trams', 'polarity': 1, 'confidence': 'tentative', 'teaching_ids': [t.id]}],
                 'dispositions': [{'teaching_id': t.id, 'outcome': 'merged', 'note': ''}]}
    weave.weave(p)
    with session() as s:
        teachings.reject(s, p, t.id)
        first = s.exec(select(ProfileKnowledge).where(ProfileKnowledge.version == 1)).one()
        weave.restore(s, p, first.id)
        s.commit()
        assert s.get(Teaching, t.id).status == 'rejected' and s.get(HubLesson, new).active is False


def test_mood_statements_cannot_span_boards(model):
    p, *_ = seeded(model)
    with session() as s:
        rows = [Teaching(profile_id=p, section='moodboards', surface='moodboard', text=f'b{b}', board_id=b,
                         origin='mood', origin_id=f'x{b}') for b in (1, 2)]
        s.add_all(rows)
        s.commit()
        ids = [r.id for r in rows]
    model.ops = {'ops': [{'op': 'add', 'sid': None, 'text': 'Warm tones everywhere', 'polarity': 1,
                          'confidence': 'tentative', 'teaching_ids': ids}],
                 'dispositions': [{'teaching_id': i, 'outcome': 'merged', 'note': ''} for i in ids]}
    assert weave.weave(p)['status'] == 'failed'


def _mood(p, board, n, prefix):
    with session() as s:
        rows = [Teaching(profile_id=p, section='moodboards', surface='moodboard', text=f'{prefix}{i}', board_id=board,
                         origin='mood', origin_id=f'{prefix}{i}') for i in range(n)]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def test_moodboards_are_woven_per_board_with_a_budget_per_board(model):
    p, *_ = seeded(model)
    long_text = 'x' * 290
    ids = {b: _mood(p, b, 5, f'b{b}-') for b in (1, 2)}
    seen_payloads = []

    def ops(user):
        import json as _json
        body = _json.loads(user.split('<data>')[1].split('</data>')[0])
        seen_payloads.append(body)
        tid = [x['id'] for x in body['teachings']]
        return {'ops': [{'op': 'add', 'sid': None, 'text': long_text, 'polarity': 1, 'confidence': 'tentative',
                         'teaching_ids': [i]} for i in tid],
                'dispositions': [{'teaching_id': i, 'outcome': 'merged', 'note': ''} for i in tid]}
    model.ops = ops
    out = weave.weave(p)
    # 5 x 290 = 1450 chars per board: fits per board though 2900 together exceeds the old shared 2500 budget
    assert out['status'] == 'woven' and out['merged'] == 10 and not out['failures']
    assert len(seen_payloads) == 2 and all(len(b['teachings']) == 5 for b in seen_payloads)
    with session() as s:
        sec = next(x for x in render.current(s, p).sections if x['key'] == 'moodboards')
        assert sum(len(st['text']) for st in sec['statements']) > weave.SECTION_BUDGET
        assert {t.status for t in s.exec(select(Teaching).where(Teaching.origin == 'mood')).all()} == {'merged'}


def test_one_board_cannot_exceed_its_own_budget_and_others_still_merge(model):
    p, *_ = seeded(model)
    big = _mood(p, 1, 9, 'big')   # 9 x 290 > 2500
    small = _mood(p, 2, 1, 'small')

    def ops(user):
        import json as _json
        body = _json.loads(user.split('<data>')[1].split('</data>')[0])
        tid = [x['id'] for x in body['teachings']]
        return {'ops': [{'op': 'add', 'sid': None, 'text': 'y' * 290, 'polarity': 1, 'confidence': 'tentative',
                         'teaching_ids': [i]} for i in tid],
                'dispositions': [{'teaching_id': i, 'outcome': 'merged', 'note': ''} for i in tid]}
    model.ops = ops
    out = weave.weave(p)
    # Retried in small pieces: the first five fit the board's budget, the rest stay pending.
    assert out['status'] == 'woven' and out['merged'] == 1 + weave.SMALL_BATCH and list(out['failures']) == ['moodboards:1']
    with session() as s:
        assert s.get(Teaching, small[0]).status == 'merged'
        states = [s.get(Teaching, i).status for i in big]
        assert states.count('merged') == weave.SMALL_BATCH and states.count('pending') == 9 - weave.SMALL_BATCH


def test_board_batch_cannot_touch_another_boards_statement(model):
    p, *_ = seeded(model)
    a = _mood(p, 1, 1, 'a')
    with session() as s:
        cur = render.current(s, p)
        sections = weave.apply_ops(weave._normalized(cur.sections), 'moodboards', [
            {'op': 'add', 'sid': None, 'text': 'Board two likes velvet', 'polarity': 1, 'confidence': 'tentative',
             'teaching_ids': []}], {}, now=time.time(), user_edit=True)
        for st in next(x for x in sections if x['key'] == 'moodboards')['statements']:
            st['board_id'] = 2
            other = st['sid']
        weave.write_version(s, p, sections, 'edit', cur)
        s.commit()
    model.ops = {'ops': [{'op': 'reinforce', 'sid': other, 'text': None, 'polarity': 1, 'confidence': 'tentative',
                          'teaching_ids': a}],
                 'dispositions': [{'teaching_id': a[0], 'outcome': 'merged', 'note': ''}]}
    assert weave.weave(p)['status'] == 'failed'


def test_missing_disposition_keeps_that_teaching_pending_not_the_batch(model):
    p, *_ = seeded(model)
    ids = _mood(p, 1, 2, 'm')
    model.ops = {'ops': [{'op': 'add', 'sid': None, 'text': 'Prefer velvet', 'polarity': 1, 'confidence': 'tentative',
                          'teaching_ids': [ids[0]]}],
                 'dispositions': [{'teaching_id': ids[0], 'outcome': 'merged', 'note': ''}]}
    out = weave.weave(p)
    assert out['status'] == 'woven' and out['merged'] == 1
    with session() as s:
        assert s.get(Teaching, ids[0]).status == 'merged' and s.get(Teaching, ids[1]).status == 'pending'


def test_deleting_a_profile_removes_its_knowledge(model, client):  # noqa: F811
    p, *_ = seeded(model)
    other = client.post('/api/profiles', json={'name': 'Other'}).json()['id']
    client.post('/api/curator/chat', json={'text': 'hello'}, headers={'X-Profile-Id': str(other)})
    hub_lesson(other, items())
    with session() as s:
        teachings.sync(s, other)
        s.commit()
    assert client.delete(f'/api/profiles/{other}').status_code in (200, 204)
    with session() as s:
        for model_ in (Teaching, ProfileKnowledge, KnowledgeChat):
            assert s.exec(select(model_).where(model_.profile_id == other)).all() == []


def test_rejecting_a_teaching_behind_a_mixed_statement_asks_the_curator_to_revise(model, client):  # noqa: F811
    p, lid, _ = seeded(model)
    t = teaching_for(p, 'hub_lesson', lid)
    extra = hub_lesson(p, items(), text='More ferry news')
    e = teaching_for(p, 'hub_lesson', extra)
    with session() as s:
        cur = render.current(s, p)
        st = next(st for sec in cur.sections for st in sec['statements'] if t.id in st['teaching_ids'])
        sections = weave.apply_ops(weave._normalized(cur.sections), 'news', [
            {'op': 'reinforce', 'sid': st['sid'], 'teaching_ids': [e.id]}], {e.id: e}, now=1)
        weave.write_version(s, p, sections, 'nightly', cur)
        s.commit()
    asked = []
    model.chat = lambda user: asked.append(user) or {'reply': 'Narrowed it.', 'ops': []}
    body = client.post(f'/api/curator/teachings/{t.id}/reject', json={}).json()
    assert body['message']['role'] == 'curator' and body['message']['text'] == 'Narrowed it.'
    assert 'More local harbour reporting' in asked[0] and 'Revise' in asked[0]


def test_empty_statement_patch_is_refused_and_unlock_allows_refinement(model, client):  # noqa: F811
    p, *_ = seeded(model)
    k = client.get('/api/curator/knowledge').json()
    st = next(st for sec in k['sections'] for st in sec['statements'])
    assert client.patch(f"/api/curator/knowledge/statements/{st['sid']}", json={}).status_code == 422
    client.patch(f"/api/curator/knowledge/statements/{st['sid']}", json={'text': 'Mine now'})
    out = client.patch(f"/api/curator/knowledge/statements/{st['sid']}", json={'locked': False}).json()
    row = next(x for sec in out['sections'] for x in sec['statements'] if x['sid'] == st['sid'])
    assert not row['locked'] and row['confidence'] == 'established'


def test_app_written_revision_prompts_are_never_kept_as_the_persons_words(model, client):  # noqa: F811
    p, lid, _ = seeded(model)
    t = teaching_for(p, 'hub_lesson', lid)
    extra = hub_lesson(p, items(), text='More ferry news')
    e = teaching_for(p, 'hub_lesson', extra)
    with session() as s:
        cur = render.current(s, p)
        st = next(st for sec in cur.sections for st in sec['statements'] if t.id in st['teaching_ids'])
        sections = weave.apply_ops(weave._normalized(cur.sections), 'news', [
            {'op': 'reinforce', 'sid': st['sid'], 'teaching_ids': [e.id]}], {e.id: e}, now=1)
        weave.write_version(s, p, sections, 'nightly', cur)
        s.commit()
    model.chat = RuntimeError('model down')
    body = client.post(f'/api/curator/teachings/{t.id}/reject', json={}).json()
    assert body['message']['role'] == 'curator'
    assert [m['role'] for m in client.get('/api/curator/chat').json()['messages']] == ['curator']
    with session() as s:
        assert s.exec(select(Teaching).where(Teaching.status == 'pending', Teaching.user_authored == True)).all() == []  # noqa: E712


def test_deleting_a_scoped_lesson_still_leaves_a_tombstone(client):  # noqa: F811
    p = pid()
    with session() as s:
        le = Lesson(profile_id=p, text='Only gentle videos here', category_id=7)
        s.add(le)
        s.commit()
        lid = le.id
    assert client.delete(f'/api/lessons/{lid}').status_code == 200
    with session() as s:
        assert [t.text for t in teachings.rejected(s, p)] == ['Only gentle videos here']


def test_failed_big_batch_is_retried_in_small_pieces_and_long_text_is_shortened(model):
    p, *_ = seeded(model)
    with session() as s:
        board = MoodBoard(profile_id=p, name='Whimsy', interests='whimsical clothes')
        s.add(board)
        s.flush()
        rows = [Teaching(profile_id=p, surface='moodboard', section='moodboards', text=f'Lesson {i}', board_id=board.id,
                         origin='mood', origin_id=f'b:{i}') for i in range(7)]
        s.add_all(rows)
        s.commit()
        ids = [t.id for t in rows]

    def ops(user):
        cited = [i for i in ids if f'"id": {i},' in user or f'"id": {i}}}' in user]
        if len(cited) > weave.SMALL_BATCH:
            raise ValueError('server reported truncated structured output')  # big batch fails, as on prod
        return {'ops': [{'op': 'add', 'sid': None, 'text': ('Wordy statement. ' * 30) + str(cited[0]), 'polarity': 1,
                         'confidence': 'tentative', 'teaching_ids': cited}],
                'dispositions': [{'teaching_id': i, 'outcome': 'merged', 'note': ''} for i in cited]}
    model.ops = ops
    result = weave.weave(p, collect=False)
    assert result['status'] == 'woven' and result['merged'] == 7 and not result['failures']
    with session() as s:
        sec = next(x for x in render.current(s, p).sections if x['key'] == 'moodboards')
        assert len(sec['statements']) == 2 and all(len(st['text']) <= weave.STATEMENT_MAX for st in sec['statements'])
        assert all(st['text'].endswith('.') for st in sec['statements'])
