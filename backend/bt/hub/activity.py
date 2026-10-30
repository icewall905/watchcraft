"""Real, profile-owned curator milestones in the existing agent-event store."""
from datetime import timezone

from sqlalchemy import text
from sqlmodel import select

from ..access import hub_allowed
from ..db import session
from ..models import AgentEvent
from .models import Curator

KIND = 'hub_curator'


def emit(curator, run_id: int, step: str, message: str, **details):
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(Curator, curator.id)
        if not row or row.profile_id != curator.profile_id or not hub_allowed(s, curator.profile_id):
            return
        s.add(AgentEvent(kind=KIND, profile_id=curator.profile_id, message=message[:400],
                         payload={'curator_id': curator.id, 'run_id': run_id, 'step': step, **details}))
        s.flush()
        old = s.exec(select(AgentEvent).where(AgentEvent.kind == KIND, AgentEvent.profile_id == curator.profile_id,
                         AgentEvent.payload['curator_id'].as_integer() == curator.id)
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
             'detail': e.payload.get('error', '')} for e in reversed(events)]
