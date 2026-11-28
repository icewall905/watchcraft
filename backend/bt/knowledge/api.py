"""Settings → Curator: what the curator knows, what it learned and why, rejections, history and the correction chat."""
from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as Input
from sqlmodel import col, select

from ..access import profile_id, require_admin
from ..db import session
from . import chat, render, teachings, weave
from .models import SECTIONS, ProfileKnowledge, Teaching

router = APIRouter(prefix='/curator', tags=['Curator knowledge'])
SECTION_PATTERN = '^(' + '|'.join(SECTIONS) + ')$'


def _changed(pid: int) -> None:
    """Consumers that cache taste re-read it: hub rejudging and article preferences."""
    try:
        from ..hub.master import schedule
        schedule.mark(pid)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..hub import article_preferences
        article_preferences.changed(pid)
    except Exception:  # noqa: BLE001
        pass


def _version(pk: ProfileKnowledge | None) -> dict | None:
    if pk is None:
        return None
    return {'id': pk.id, 'version': pk.version, 'cause': pk.cause, 'created_at': pk.created_at, 'changes': pk.changes}


@router.get('/knowledge')
def knowledge(pid: int = Depends(profile_id)):
    with session() as s:
        pk = render.current(s, pid)
        counts = Counter(s.exec(select(Teaching.surface).where(Teaching.profile_id == pid, Teaching.status == 'pending')).all())
        return {'version': _version(pk), 'sections': weave._normalized(pk.sections if pk else []),
                'history': weave.history(s, pid), 'pending': dict(counts)}


@router.get('/knowledge/versions/{vid}')
def version(vid: int, pid: int = Depends(profile_id)):
    with session() as s:
        pk = s.get(ProfileKnowledge, vid)
        if not pk or pk.profile_id != pid:
            raise HTTPException(404, 'Version not found')
        return {**_version(pk), 'sections': weave._normalized(pk.sections)}


@router.post('/knowledge/versions/{vid}/restore')
def restore(vid: int, pid: int = Depends(profile_id)):
    with session() as s:
        try:
            pk = weave.restore(s, pid, vid)
        except LookupError:
            raise HTTPException(404, 'Version not found')
        s.commit()
        out = _version(pk)
    _changed(pid)
    return out


class StatementPatch(BaseModel):
    text: str | None = Input(default=None, min_length=3, max_length=300)
    polarity: Literal[1, -1] | None = None
    locked: bool | None = None
    remove: bool = False


@router.patch('/knowledge/statements/{sid}')
def edit_statement(sid: str, body: StatementPatch, pid: int = Depends(profile_id)):
    with session() as s:
        current = render.current(s, pid)
        sections = weave._normalized(current.sections if current else [])
        st = chat._statement(sections, sid)
        if current is None or st is None:
            raise HTTPException(404, 'Statement not found')
        key = chat._section_of(sections, sid)
        if not body.remove and body.text is None and body.polarity is None and body.locked is None:
            raise HTTPException(422, 'Nothing to change')
        if body.remove:
            sections = weave.apply_ops(sections, key, [{'op': 'remove', 'sid': sid, 'teaching_ids': []}], {},
                                       now=time.time(), user_edit=True)
        else:
            if body.text is not None or body.polarity is not None:
                sections = weave.apply_ops(sections, key, [{
                    'op': 'refine', 'sid': sid, 'text': body.text if body.text is not None else st['text'],
                    'polarity': body.polarity if body.polarity is not None else st.get('polarity', 1),
                    'confidence': 'explicit', 'teaching_ids': []}], {}, now=time.time(), user_edit=True)
            if body.locked is not None:
                target = chat._statement(sections, sid)
                target['locked'] = body.locked
                if not body.locked and target.get('confidence') == 'explicit':
                    target['confidence'] = 'established'  # unlocked: the nightly weave may refine it again
        pk = weave.write_version(s, pid, sections, 'edit', current)
        s.commit()
        out = {'version': _version(pk), 'sections': weave._normalized(pk.sections)}
    _changed(pid)
    return out


@router.get('/knowledge/statements/{sid}/why')
def why(sid: str, pid: int = Depends(profile_id)):
    with session() as s:
        pk = render.current(s, pid)
        st = chat._statement(weave._normalized(pk.sections), sid) if pk else None
        if st is None:
            raise HTTPException(404, 'Statement not found')
        ids = st.get('teaching_ids') or []
        rows = s.exec(select(Teaching).where(Teaching.profile_id == pid, Teaching.id.in_(ids))).all() if ids else []
        return {'statement': st, 'teachings': [teachings.serialize(t) for t in sorted(rows, key=lambda t: -t.created_at)]}


@router.get('/teachings')
def list_teachings(status: Literal['pending', 'merged', 'rejected', 'superseded'] = 'pending', surface: str = '',
                   limit: int = 50, offset: int = 0, pid: int = Depends(profile_id)):
    with session() as s:
        if status == 'pending':
            teachings.sync(s, pid)
            s.commit()
        q = select(Teaching).where(Teaching.profile_id == pid, Teaching.status == status)
        if surface:
            q = q.where(Teaching.surface == surface)
        order = col(Teaching.rejected_at).desc() if status == 'rejected' else col(Teaching.created_at).desc()
        rows = s.exec(q.order_by(order).offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
        return {'teachings': [teachings.serialize(t) for t in rows]}


class TeachingIn(BaseModel):
    text: str = Input(min_length=3, max_length=300)
    section: str = Input(default='overall', pattern=SECTION_PATTERN)
    polarity: Literal[1, -1] = 1


@router.post('/teachings')
def add_teaching(body: TeachingIn, pid: int = Depends(profile_id)):
    with session() as s:
        row = teachings.add(s, pid, body.text, body.section, body.polarity)
        s.commit()
        return teachings.serialize(row)


class RejectIn(BaseModel):
    correction: str = Input(default='', max_length=600)


@router.post('/teachings/{tid}/reject')
def reject(tid: int, body: RejectIn, pid: int = Depends(profile_id)):
    with session() as s:
        try:
            result = teachings.reject(s, pid, tid, body.correction)
        except LookupError:
            raise HTTPException(404, 'Teaching not found')
        followup = None
        if result['correction'] is not None or result['was'] == 'merged':
            followup = chat.rejection_followup(s, pid, result['teaching'], result['correction'])
        if result['teaching'].origin == 'hub_lesson':
            from ..hub.master.prefs import rebuild_core
            rebuild_core(s, pid)
        s.commit()
        out = {'teaching': teachings.serialize(result['teaching']),
               'correction': teachings.serialize(result['correction']) if result['correction'] else None,
               'message': chat.serialize(followup[1]) if followup and followup[0] == 'message' else None}
    if followup and followup[0] in ('ask', 'revise'):
        # The model is asked after the rejection is committed, with no database lock held.
        out['message'] = chat.send(pid, followup[1], correction_teaching_id=followup[2],
                                   generated=followup[0] == 'revise')[1]
    _changed(pid)
    return out


@router.post('/teachings/{tid}/restore')
def restore_teaching(tid: int, pid: int = Depends(profile_id)):
    with session() as s:
        try:
            row = teachings.restore(s, pid, tid)
        except LookupError:
            raise HTTPException(404, 'Teaching not found')
        if row.origin == 'hub_lesson':
            from ..hub.master.prefs import rebuild_core
            rebuild_core(s, pid)
        s.commit()
        out = teachings.serialize(row)
    _changed(pid)
    return out


@router.get('/chat')
def get_chat(pid: int = Depends(profile_id)):
    with session() as s:
        return {'messages': [chat.serialize(m) for m in chat.thread(s, pid)]}


class ChatIn(BaseModel):
    text: str = Input(min_length=1, max_length=2000)


@router.post('/chat')
def post_chat(body: ChatIn, pid: int = Depends(profile_id)):
    user, bot = chat.send(pid, body.text)
    return {'messages': [user, bot]}


@router.post('/chat/{mid}/accept')
def accept(mid: int, pid: int = Depends(profile_id)):
    with session() as s:
        try:
            m, pk = chat.accept(s, pid, mid)
        except LookupError:
            raise HTTPException(404, 'Message not found')
        except chat.Stale as e:
            s.commit()
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        s.commit()
        out = {'message': chat.serialize(m), 'version': _version(pk), 'sections': weave._normalized(pk.sections)}
    _changed(pid)
    return out


@router.post('/chat/{mid}/reject')
def reject_proposal(mid: int, pid: int = Depends(profile_id)):
    with session() as s:
        try:
            m = chat.reject(s, pid, mid)
        except LookupError:
            raise HTTPException(404, 'Message not found')
        s.commit()
        return chat.serialize(m)


@router.post('/knowledge/weave')
def weave_now(pid: int = Depends(profile_id), _admin: int = Depends(require_admin)):
    """Run tonight's weave now for this profile, in the background."""
    def run():
        try:
            weave.weave(pid)
        finally:
            _changed(pid)
    threading.Thread(target=run, name=f'knowledge-weave-{pid}', daemon=True).start()
    return {'started': True}
