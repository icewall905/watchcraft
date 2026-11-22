"""The 1,000-image limit counts only shown pictures; each board's "What it learned" page shows, edits, chats about and
compiles the curator's learned taste into one brief that the curator then reads."""
import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401  (isolated DB/API fixture)

from bt.db import session
from bt.hub import board_brief, moodboards as mb
from bt.hub.models import MoodAsset, MoodBoard, MoodBoardBrief, MoodFeedback, MoodPin
from bt.knowledge import render, weave
from bt.knowledge.models import Teaching


class Model:
    def __init__(self):
        self.brief = {'brief': 'SUBJECT: whimsical clothes, accessories and jewellery.\nMORE OF: playful prints.', 'dropped': []}
        self.chat = {'reply': 'Got it.', 'ops': []}
        self.calls = []

    def __call__(self, system, user, schema, **kw):
        self.calls.append((system, user, kw.get('role')))
        if schema is board_brief.BRIEF_SCHEMA:
            return self.brief(user) if callable(self.brief) else self.brief
        if schema is board_brief.CHAT_SCHEMA:
            if isinstance(self.chat, Exception):
                raise self.chat
            return self.chat
        return {}


@pytest.fixture
def model(client, monkeypatch):  # noqa: F811
    m = Model()
    monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
    monkeypatch.setattr('bt.hub.ai._call', m)
    return m


def make(client, name='Whimsy', interests='whimsical clothes, accessories and jewellery'):  # noqa: F811
    r = client.post('/api/hub/boards', json={'name': name, 'interests': interests})
    assert r.status_code == 200, r.text
    return r.json()['board']


def pins(bid, n, hidden=False):
    with session() as s:
        board = s.get(MoodBoard, bid)
        ids = []
        for i in range(n):
            pin = MoodPin(board_id=bid, profile_id=board.profile_id, title=f'p{i}', hidden=hidden,
                          source_url=f'https://example.org/{"h" if hidden else "v"}{bid}-{i}', image_url='')
            s.add(pin)
            s.flush()
            s.add(MoodAsset(pin_id=pin.id, mime='image/png', data=b'x'))
            ids.append(pin.id)
        s.commit()
        return ids


def seed_statements(pid, rows):
    """rows: (text, polarity, board_id)"""
    with session() as s:
        if render.current(s, pid) is None:
            weave.seed(s, pid)
            s.commit()
        pk = render.current(s, pid)
        sections = weave._normalized(pk.sections)
        sec = next(x for x in sections if x['key'] == 'moodboards')
        for text, polarity, bid in rows:
            sec['statements'].append({'sid': weave.sid(), 'text': text, 'polarity': polarity, 'confidence': 'established',
                                      'locked': False, 'teaching_ids': [], 'board_id': bid, 'since': 1, 'updated': 1})
        weave.write_version(s, pid, sections, 'edit', pk)
        s.commit()
        return [st['sid'] for st in sec['statements'][-len(rows):]]


def test_hidden_pins_do_not_count_toward_limit_and_stale_error_clears(client, monkeypatch):  # noqa: F811
    board = make(client)
    pins(board['id'], 3)
    pins(board['id'], 5, hidden=True)
    with session() as s:
        assert mb.stored(s, board['id']) == 3
        b = s.get(MoodBoard, board['id'])
        b.error = mb.LIMIT_ERROR
        s.add(b)
        s.commit()
    monkeypatch.setattr(mb, 'MAX_PINS', 4)
    detail = client.get(f'/api/hub/boards/{board["id"]}').json()['board']
    assert detail['error'] == ''  # 3 shown of 4: the old message no longer applies


def test_trim_hidden_keeps_newest_pictures_and_links(client):  # noqa: F811
    board = make(client)
    hidden = pins(board['id'], 6, hidden=True)
    with session() as s:
        assert mb.trim_hidden(s, board['id'], keep=2) == 4
        s.commit()
        kept = set(s.exec(select(MoodAsset.pin_id).where(MoodAsset.pin_id.in_(hidden))).all())
        assert kept == set(hidden[-2:])
        assert len(s.exec(select(MoodPin).where(MoodPin.board_id == board['id'])).all()) == 6


def test_overview_scopes_statements_and_edits_them(client, model):  # noqa: F811
    a, b = make(client), make(client, 'Nursery', 'crochet nursery decor')
    pid = a['profile_id']
    own, shared, other = seed_statements(pid, [('Playful brooches', 1, a['id']), ('Soft pastel palettes', 1, None),
                                               ('Crochet baby blankets', 1, b['id'])])
    data = client.get(f'/api/hub/boards/{a["id"]}/learned').json()
    assert {(r['text'], r['scope']) for r in data['statements']} == {('Playful brooches', 'board'), ('Soft pastel palettes', 'all')}
    index = client.get('/api/hub/boards/learned').json()
    assert [r['text'] for r in index['shared']] == ['Soft pastel palettes']
    assert {r['name'] for r in index['boards']} == {'Whimsy', 'Nursery'}

    # Shared statement moved off this board stays on the other board only.
    r = client.patch(f'/api/hub/boards/{a["id"]}/learned/statements/{shared}', json={'scope': 'off'})
    assert r.status_code == 200, r.text
    assert [x['text'] for x in r.json()['statements']] == ['Playful brooches']
    nursery = client.get(f'/api/hub/boards/{b["id"]}/learned').json()['statements']
    assert {x['text'] for x in nursery} == {'Crochet baby blankets', 'Soft pastel palettes'}

    r = client.patch(f'/api/hub/boards/{a["id"]}/learned/statements/{own}', json={'text': 'Playful enamel brooches'})
    assert [(x['text'], x['confidence']) for x in r.json()['statements']] == [('Playful enamel brooches', 'explicit')]
    r = client.post(f'/api/hub/boards/{a["id"]}/learned/statements', json={'text': 'No nursery items', 'polarity': -1})
    assert {(x['text'], x['polarity'], x['scope']) for x in r.json()['statements']} >= {('No nursery items', -1, 'board')}
    # Another board's statement cannot be touched from here.
    assert client.patch(f'/api/hub/boards/{a["id"]}/learned/statements/{other}', json={'remove': True}).status_code == 404


def test_removed_statement_rejects_its_teachings(client, model):  # noqa: F811
    board = make(client)
    pid = board['profile_id']
    with session() as s:
        t = Teaching(profile_id=pid, surface='moodboard', section='moodboards', text='Crochet', status='merged', board_id=board['id'])
        s.add(t)
        s.commit()
        tid = t.id
    (sid,) = seed_statements(pid, [('Crochet', 1, board['id'])])
    with session() as s:
        pk = render.current(s, pid)
        sections = weave._normalized(pk.sections)
        next(st for sec in sections for st in sec['statements'] if st['sid'] == sid)['teaching_ids'] = [tid]
        weave.write_version(s, pid, sections, 'edit', pk)
        s.commit()
    assert client.patch(f'/api/hub/boards/{board["id"]}/learned/statements/{sid}', json={'remove': True}).status_code == 200
    with session() as s:
        assert s.get(Teaching, tid).status == 'rejected'


def test_refine_compiles_brief_flags_off_topic_and_feeds_curator(client, model):  # noqa: F811
    board = make(client)
    pid = board['profile_id']
    good, off = seed_statements(pid, [('Playful prints', 1, board['id']), ('Crochet nursery blankets', 1, board['id'])])
    model.brief = {'brief': 'SUBJECT: whimsical clothes.\nMORE OF: playful prints.', 'dropped': [{'sid': off, 'why': 'nursery decor is not clothing'}]}
    r = client.post(f'/api/hub/boards/{board["id"]}/learned/refine')
    assert r.status_code == 200, r.text
    brief = r.json()['brief']
    assert brief['state'] == 'fresh' and 'playful prints' in brief['text']
    assert [d['sid'] for d in brief['dropped']] == [off]
    assert model.calls[-1][2] == 'board_brief'
    # The absorbed item left the list; the flagged one stays for the owner; the brief is current.
    assert [x['sid'] for x in r.json()['statements']] == [off] and brief['absorbed'] == 1
    with session() as s:
        assert render.current(s, pid).cause == 'brief'
    with session() as s:
        b = s.get(MoodBoard, board['id'])
        lessons = mb.feedback_lessons(s, board['id'])
        assert 'BOARD BRIEF' in lessons and 'playful prints' in lessons
        assert 'NEWER LEARNINGS' not in lessons
    # Removing the flagged statement leaves nothing outside the brief; a new learning makes it stale and the
    # curator reads it next to the brief until the next compile.
    data = client.patch(f'/api/hub/boards/{board["id"]}/learned/statements/{off}', json={'remove': True}).json()
    assert data['statements'] == [] and data['brief']['dropped'] == []
    client.post(f'/api/hub/boards/{board["id"]}/learned/statements', json={'text': 'Tiny hats', 'polarity': 1})
    data = client.get(f'/api/hub/boards/{board["id"]}/learned').json()
    assert data['brief']['state'] == 'stale'
    with session() as s:
        assert 'NEWER LEARNINGS' in mb.feedback_lessons(s, board['id'])


def test_shared_and_edited_statements_are_not_absorbed(client, model, monkeypatch):  # noqa: F811
    board = make(client)
    pid = board['profile_id']
    own, shared, edited = seed_statements(pid, [('Playful prints', 1, board['id']), ('Pastels', 1, None),
                                                ('Glitter', 1, board['id'])])
    real = board_brief._absorb

    def edit_midway(s, *a, **kw):  # the owner edits one item while the model writes the brief
        pk = render.current(s, pid)
        sections = weave._normalized(pk.sections)
        next(st for sec in sections for st in sec['statements'] if st['sid'] == edited)['text'] = 'Fine glitter'
        weave.write_version(s, pid, sections, 'edit', pk)
        s.flush()
        return real(s, *a, **kw)
    monkeypatch.setattr(board_brief, '_absorb', edit_midway)
    r = client.post(f'/api/hub/boards/{board["id"]}/learned/refine').json()
    assert {x['text'] for x in r['statements']} == {'Pastels', 'Fine glitter'}
    assert r['brief']['state'] == 'fresh'
    # Next night: nothing changed, nothing to do; a new item makes it due again and the old brief is the base.
    assert board_brief.nightly(pid)['due'] == 0
    seed_statements(pid, [('Rainbow', 1, board['id'])])
    seen = []
    model.brief = lambda user: seen.append(user) or {'brief': 'SUBJECT: whimsy. MORE OF: rainbow.', 'dropped': []}
    monkeypatch.setattr(board_brief, '_absorb', real)
    board_brief.nightly(pid)
    assert 'playful prints' in seen[0] and 'Rainbow' in seen[0]
    with session() as s:
        left = [st['text'] for st in board_brief.statements(board_brief._sections(s, pid)[1], board['id'])]
    assert sorted(left) == ['Pastels']


def test_owner_edits_brief_and_nightly_recompiles_only_changed(client, model):  # noqa: F811
    a, b = make(client), make(client, 'Empty', 'anything')
    pid = a['profile_id']
    seed_statements(pid, [('Playful prints', 1, a['id'])])
    assert board_brief.nightly(pid) == {'compiled': 1, 'failed': 0, 'due': 1}  # b has nothing learned: no brief
    assert board_brief.nightly(pid)['due'] == 0
    r = client.put(f'/api/hub/boards/{a["id"]}/learned/brief', json={'text': 'SUBJECT: my own words.'})
    assert r.json()['brief']['text'] == 'SUBJECT: my own words.' and r.json()['brief']['state'] == 'fresh'
    seed_statements(pid, [('Glitter', 1, a['id'])])
    seen = []
    model.brief = lambda user: seen.append(user) or {'brief': 'SUBJECT: my own words. MORE OF: glitter.', 'dropped': []}
    board_brief.nightly(pid)
    assert '"edited_by_owner": true' in seen[0] and 'my own words' in seen[0]
    with session() as s:
        assert 'glitter' in s.get(MoodBoardBrief, a['id']).text
    assert client.put(f'/api/hub/boards/{a["id"]}/learned/brief', json={'text': ''}).json()['brief']['state'] == 'none'


def test_chat_proposes_and_applies_only_on_accept(client, model):  # noqa: F811
    board = make(client)
    pid = board['profile_id']
    (sid,) = seed_statements(pid, [('Crochet nursery items', 1, board['id'])])
    model.chat = {'reply': 'I will stop looking for nursery things.', 'ops': [
        {'op': 'remove', 'sid': sid, 'text': None, 'polarity': 1},
        {'op': 'add', 'sid': None, 'text': 'No nursery or baby items', 'polarity': -1},
        {'op': 'edit', 'sid': 'unknown', 'text': 'ignored', 'polarity': 1}]}
    r = client.post(f'/api/hub/boards/{board["id"]}/learned/chat', json={'text': 'Stop the crochet nursery stuff'})
    assert r.status_code == 200, r.text
    bot = r.json()['messages'][-1]
    assert bot['proposal_status'] == 'pending' and len(bot['proposal']['ops']) == 2
    assert [x['text'] for x in r.json()['statements']] == ['Crochet nursery items']  # nothing changed yet
    r = client.post(f'/api/hub/boards/{board["id"]}/learned/chat/{bot["id"]}/accept')
    assert r.status_code == 200, r.text
    assert [(x['text'], x['polarity']) for x in r.json()['statements']] == [('No nursery or baby items', -1)]
    assert client.post(f'/api/hub/boards/{board["id"]}/learned/chat/{bot["id"]}/accept').status_code == 404


def test_chat_failure_keeps_the_correction_as_a_rule(client, model):  # noqa: F811
    board = make(client)
    model.chat = RuntimeError('down')
    r = client.post(f'/api/hub/boards/{board["id"]}/learned/chat', json={'text': 'Only wearable things'})
    assert [x['text'] for x in r.json()['statements']] == ['Only wearable things']


def test_learned_pages_are_owner_only(client, model):  # noqa: F811
    board = make(client)
    with session() as s:
        b = s.get(MoodBoard, board['id'])
        b.profile_id = b.profile_id + 999
        s.add(b)
        s.commit()
    assert client.get(f'/api/hub/boards/{board["id"]}/learned').status_code == 404
    assert client.post(f'/api/hub/boards/{board["id"]}/learned/refine').status_code == 404


def test_planner_never_sees_review_examples(client, model, monkeypatch):  # noqa: F811
    board = make(client)
    with session() as s:
        b = s.get(MoodBoard, board['id'])
        pin = MoodPin(board_id=b.id, profile_id=b.profile_id, title='Crochet baby blanket', source_url='https://e.org/1', image_url='')
        s.add(pin)
        s.flush()
        s.add(MoodFeedback(pin_id=pin.id, board_id=b.id, profile_id=b.profile_id, verdict='good', title='Crochet baby blanket'))
        s.commit()
        text = board_brief.planner_text(s, b)
        assert 'Crochet baby blanket' not in text
        assert 'Crochet baby blanket' in mb.feedback_lessons(s, b.id)
