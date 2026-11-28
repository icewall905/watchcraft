"""Mood Board work is visible as real, profile-owned Agent Activity."""
import time
from types import SimpleNamespace

from sqlmodel import select
from test_curation import client  # noqa: F401
from bt.db import session
from bt.models import AgentEvent
from bt.hub import activity
from bt.hub.models import MoodBoard, MoodPin


def make(client, **body):
    response = client.post('/api/hub/boards', json={'name': 'Heart dresses', 'interests': 'dresses', **body})
    assert response.status_code == 200
    return response.json()['board']


def test_board_activity_state_counts_timeline_and_profile_isolation(client):
    board = make(client)
    with session() as s:
        row = s.get(MoodBoard, board['id'])
        row.lease, row.last_status, row.build_started, row.last_run = 'run1', 'collecting', time.time(), time.time()
        s.add(row)
        s.add(MoodPin(board_id=row.id, profile_id=row.profile_id, title='Heart dress', source_url='https://example.com/1', image_url=''))
        s.add(MoodPin(board_id=row.id, profile_id=row.profile_id, title='Hidden', source_url='https://example.com/2', image_url='', hidden=True))
        s.commit()
        activity.emit_board(row, 'run1', 'judge', 'Inspecting the captured images.')
    data = client.get('/api/hub/team').json()
    card = next(a for a in data['agents'] if a['kind'] == 'moodboard')
    assert card['key'] == f'moodboard-{board["id"]}'
    assert card['status'] == 'running' and card['since'] and card['last_run']
    assert card['pin_count'] == card['found_today'] == 1
    assert card['doing'] == 'Inspecting the captured images.'
    assert card['timeline'][0]['step'] == 'judge'
    assert data['running'] == 0  # does not invent a shared LLM slot allocation
    other = client.post('/api/profiles', json={'name': 'Other'}).json()
    theirs = client.get('/api/hub/team', headers={'X-Profile-Id': str(other['id'])}).json()
    assert not any(a['kind'] == 'moodboard' for a in theirs['agents'])
    assert not theirs['journal']
    with session() as s:
        row = s.get(MoodBoard, board['id'])
        row.lease, row.build_started, row.last_status = '', 0, 'ok'
        s.add(row)
        s.commit()
        activity.emit_board(row, 'run1', 'complete', 'Collected one matching image.', added=1)
    data = client.get('/api/hub/team').json()
    card = next(a for a in data['agents'] if a['kind'] == 'moodboard')
    assert card['status'] == 'idle'
    journal = next(e for e in data['journal'] if e['kind'] == 'moodboard_run')
    assert journal['new_items'] == 1 and journal['title'] == 'Collected one matching image.'


def test_board_activity_is_bounded_and_cannot_impersonate_another_owner(client):
    board = make(client)
    with session() as s:
        row = s.get(MoodBoard, board['id'])
        for n in range(85):
            row.lease = f'run{n}'
            s.add(row)
            s.commit()
            activity.emit_board(row, f'run{n}', 'search', f'Search {n}')
        events = s.exec(select(AgentEvent).where(AgentEvent.kind == activity.KIND)).all()
        assert len(events) == 80
        assert len(activity.board_recent(s, row.profile_id, row.id)) == 1
        forged = SimpleNamespace(id=row.id, profile_id=row.profile_id + 123)
        activity.emit_board(forged, 'forged', 'complete', 'Not allowed')
        assert not s.exec(select(AgentEvent).where(AgentEvent.message == 'Not allowed')).all()


def test_board_queued_paused_failure_and_interrupted_states(client):
    board = make(client, enabled=False)
    with session() as s:
        row = s.get(MoodBoard, board['id'])
        for status, lease, started, error, expected in [
            ('', '', 0, '', 'paused'), ('queued', '', 0, '', 'waiting'),
            ('error', '', 0, 'Vision unavailable', 'attention'),
            ('collecting', 'lost', time.time() - 2000, '', 'attention'),
        ]:
            row.last_status, row.lease, row.build_started, row.error = status, lease, started, error
            s.add(row)
            s.commit()
            card = next(a for a in client.get('/api/hub/team').json()['agents'] if a['kind'] == 'moodboard')
            assert card['status'] == expected


def test_cancelled_board_run_cannot_emit_late_milestones(client):
    board = make(client)
    with session() as s:
        row = s.get(MoodBoard, board['id'])
        row.lease, row.last_run = 'current', time.time()
        s.add(row)
        s.commit()
        original = SimpleNamespace(id=row.id, profile_id=row.profile_id, revision=row.revision, last_run=row.last_run)
        activity.emit_board(original, 'wrong-lease', 'judge', 'Wrong run')
        activity.emit_board(original, 'current', 'judge', 'Current run')
        row.revision += 1
        row.lease = ''
        s.add(row)
        s.commit()
        activity.emit_board(original, 'current', 'complete', 'Cancelled completion')
        activity.emit_board(original, 'current', 'search', 'Cancelled search')
        events = s.exec(select(AgentEvent).where(AgentEvent.kind == activity.KIND)).all()
        assert [e.message for e in events if e.payload.get('step') != 'queued'] == ['Current run']
