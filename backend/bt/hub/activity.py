"""Real, profile-owned curator and briefing milestones in the existing agent-event store."""
from datetime import timezone

from sqlalchemy import text
from sqlmodel import select

from ..access import hub_allowed
from ..db import session
from ..models import AgentEvent
from .models import Briefing, Curator, MoodBoard

KIND = 'hub_curator'


def emit(curator, run_id: int, step: str, message: str, **details):
    _emit(curator, Curator, 'curator_id', run_id, step, message, details)


def emit_briefing(briefing, run_id: str, step: str, message: str, **details):
    _emit(briefing, Briefing, 'briefing_id', run_id, step, message, details)



def emit_board(board, run_id: str, step: str, message: str, **details):
    def current(row):
        if row.revision != getattr(board, 'revision', None):
            return False
        if step == 'queued':
            return row.last_status == 'queued'
        if step in ('complete', 'failed'):
            return not row.lease and row.last_run == getattr(board, 'last_run', None)
        return row.lease == run_id
    _emit(board, MoodBoard, 'board_id', run_id, step, message, details, guard=current)


def board_recent(s, pid: int, board_id: int) -> list[dict]:
    """Latest real visual-curation run, isolated from curator IDs in the same event store."""
    last = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == pid,
                 AgentEvent.payload['board_id'].as_integer() == board_id)
                 .order_by(AgentEvent.id.desc()).limit(1)).first()
    if not last:
        return []
    events = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == pid,
                   AgentEvent.payload['board_id'].as_integer() == board_id,
                   AgentEvent.payload['run_id'].as_string() == str(last.payload['run_id']))
                   .order_by(AgentEvent.id.desc()).limit(12)).all()
    return [{'id': e.id, 'step': e.payload.get('step', ''), 'message': e.message,
             'at': e.ts.replace(tzinfo=timezone.utc).timestamp(), 'detail': e.payload.get('error', ''),
             'added': e.payload.get('added')} for e in reversed(events)]

def _emit(owner, model, owner_key, run_id, step, message, details, guard=None):
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(model, owner.id)
        if not row or row.profile_id != owner.profile_id or not hub_allowed(s, owner.profile_id):
            return
        if guard is not None and not guard(row):
            return
        s.add(AgentEvent(kind=KIND, profile_id=owner.profile_id, message=message[:400],
                         payload={owner_key: owner.id, 'run_id': run_id, 'step': step, **details}))
        s.flush()
        old = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == owner.profile_id,
                         AgentEvent.payload[owner_key].as_integer() == owner.id)
                     .order_by(AgentEvent.id.desc()).offset(80)).all()
        for event in old:
            s.delete(event)
        s.commit()


def recent(s, pid: int, curator_id: int, run_id: int) -> list[dict]:
    events = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == pid,
                         AgentEvent.payload['curator_id'].as_integer() == curator_id,
                         AgentEvent.payload['run_id'].as_integer() == run_id)
                    .order_by(AgentEvent.id.desc()).limit(12)).all()
    return [{'id': e.id, 'step': e.payload.get('step', ''), 'message': e.message,
             'at': e.ts.replace(tzinfo=timezone.utc).timestamp(),
             'detail': e.payload.get('error', ''),
             'source_id': e.payload.get('source_id')} for e in reversed(events)]


def briefing_recent(s, pid: int, briefing_ids: list[int]) -> list[dict]:
    last = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == pid,
                 AgentEvent.payload['briefing_id'].as_integer().in_(briefing_ids))
                 .order_by(AgentEvent.id.desc()).limit(1)).first()
    if not last:
        return []
    events = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == pid,
                 AgentEvent.payload['briefing_id'].as_integer() == last.payload['briefing_id'],
                 AgentEvent.payload['run_id'].as_string() == last.payload['run_id'])
                 .order_by(AgentEvent.id.desc()).limit(12)).all()
    return [{'id': e.id, 'step': e.payload.get('step', ''), 'message': e.message,
             'at': e.ts.replace(tzinfo=timezone.utc).timestamp(), 'detail': e.payload.get('error', '')}
            for e in reversed(events)]
