"""Profile-owned watch plans, scheduled observations and durable updates."""
import asyncio
import hashlib
import json
import logging
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as Input
from sqlalchemy import delete, update
from sqlmodel import Field, SQLModel, select

from ..access import hub_allowed, require_grown_up
from ..db import session
from ..urls import canonical
from . import ai, providers, watchers

log = logging.getLogger(__name__)
router = APIRouter(prefix='/hub/watcher', tags=['The Watcher'])


class Plan(BaseModel):
    mode: Literal['page_watch', 'rss', 'github_releases', 'web_search', 'bing_news'] = 'page_watch'
    source: str = Input(min_length=1, max_length=2000)
    selector: str = Input(default='', max_length=300)
    terms: list[str] = Input(default_factory=list, max_length=8)


class DraftRequest(BaseModel):
    goal: str = Input(min_length=3, max_length=1000)


class WatchRequest(DraftRequest):
    plan: Plan
    interval: int = Input(ge=1, le=525600, strict=True)


class WatchPatch(BaseModel):
    enabled: bool


class PersonalWatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    goal: str
    plan_json: str
    interval: int
    enabled: bool = True
    next_check: float = Field(index=True)
    last_check: float | None = None
    error: str = ''
    checkpoint: str = ''
    claim: str = ''
    lease_until: float = 0


class WatchEvent(SQLModel, table=True):
    # Notification cursors must stay monotonic after users delete their newest updates.
    __table_args__ = {'sqlite_autoincrement': True}

    id: int | None = Field(default=None, primary_key=True)
    watch_id: int = Field(index=True)
    profile_id: int = Field(index=True)
    text: str
    url: str
    created_at: float
    unread: bool = True


PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'mode': {'type': 'string', 'enum': ['page_watch', 'rss', 'github_releases', 'web_search', 'bing_news']},
        'source': {'type': 'string'},
        'selector': {'type': 'string'},
        'terms': {'type': 'array', 'items': {'type': 'string'}},
        'question': {'type': 'string'},
    },
    'required': ['mode', 'source', 'selector', 'terms', 'question'],
}
LOOKUP_MODES = {'web_search', 'bing_news'}


def validate_plan(plan):
    plan.source = plan.source.strip()
    plan.terms = list(dict.fromkeys(term.strip() for term in plan.terms if term.strip()))
    if not plan.source or any(len(term) > 200 for term in plan.terms):
        raise ValueError('Enter a source and use conditions of at most 200 characters.')
    if plan.mode in LOOKUP_MODES:
        if not ai.enabled() or not plan.terms:
            raise ValueError('Search checks need Hub AI and at least one condition.')
        available, reason = providers.PROVIDERS[plan.mode].availability()
        if not available:
            raise ValueError(reason)
    else:
        plan.source = canonical(plan.source)
    if plan.mode == 'github_releases' and not plan.source.startswith('https://github.com/'):
        raise ValueError('Use a GitHub repository URL.')
    if plan.mode == 'page_watch':
        plan.selector = watchers.normalize('page_watch', {'content_selector': plan.selector}).get('content_selector', '')
    else:
        plan.selector = ''
    return plan


@router.post('/draft')
def draft(body: DraftRequest, pid: int = Depends(require_grown_up)):
    if not ai.enabled():
        return {'question': 'Hub AI is unavailable. Enter the check settings below.'}
    try:
        result = ai._call(
            'Design a read-only recurring check using only these providers: page_watch for public HTML status text, '
            'rss for a known public feed, github_releases for a known GitHub repository, web_search for configured '
            'web search, and bing_news for public news. For searches, source is a search query and each terms entry '
            'is an independent factual condition that can be confirmed by a result. For feeds, terms are literal '
            'title substrings joined by OR. For page_watch use a selector only if the user supplied it. '
            'For parcel tracking ask for the public carrier tracking page URL if one was not supplied. '
            'Never put tracking IDs in search queries or guess a carrier URL. If a required detail is missing, '
            'ask a short question and leave source empty. Otherwise leave question empty. '
            'Never invent an official feed URL. For named public product releases prefer news search if no URL '
            'was supplied. When several products are requested, give each its own complete release condition.',
            f'<data>{ai._esc(body.goal)}</data>', PLAN_SCHEMA, timeout_s=25, role='watcher_setup')
        if result.get('question'):
            return {'question': str(result['question'])[:500]}
        return {'plan': validate_plan(Plan.model_validate(result))}
    except ValueError:
        return {'question': 'The suggested check needs more detail. Enter the source and condition below.'}
    except Exception:
        log.warning('Watcher planning failed')
        raise HTTPException(503, 'Could not prepare a check. Try again or enter the settings below.')


def completed(plan, checkpoint):
    return plan.mode in LOOKUP_MODES and bool(checkpoint) and len(json.loads(checkpoint)) == len(plan.terms)


def view(watch):
    plan = Plan.model_validate_json(watch.plan_json)
    return {**watch.model_dump(exclude={'plan_json', 'checkpoint', 'claim', 'lease_until'}),
            'plan': plan, 'completed': completed(plan, watch.checkpoint)}


@router.get('')
def listing(pid: int = Depends(require_grown_up)):
    with session() as s:
        return [view(w) for w in s.exec(select(PersonalWatch).where(PersonalWatch.profile_id == pid)
                                        .order_by(PersonalWatch.id.desc())).all()]


@router.post('')
def create(body: WatchRequest, pid: int = Depends(require_grown_up)):
    try:
        plan = validate_plan(body.plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if len(body.goal.strip()) < 3:
        raise HTTPException(422, 'Describe what you want to watch.')
    with session() as s:
        # SQLite serializes creation so simultaneous requests respect the per-profile limit.
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        count = s.exec(select(PersonalWatch.id).where(PersonalWatch.profile_id == pid).limit(30)).all()
        if len(count) >= 30:
            raise HTTPException(409, 'Remove an existing watch before adding another.')
        watch = PersonalWatch(profile_id=pid, goal=body.goal.strip(), plan_json=plan.model_dump_json(),
                              interval=body.interval, next_check=time.time())
        s.add(watch)
        s.commit()
        s.refresh(watch)
        return view(watch)


@router.get('/updates')
def events(unread: bool = False, kind: Literal['all', 'watch', 'test'] = 'all', pid: int = Depends(require_grown_up)):
    with session() as s:
        stmt = select(WatchEvent).where(WatchEvent.profile_id == pid)
        if kind == 'watch':
            stmt = stmt.where(WatchEvent.watch_id != 0)
        elif kind == 'test':
            stmt = stmt.where(WatchEvent.watch_id == 0)
        if unread:
            stmt = stmt.where(WatchEvent.unread == True)  # noqa: E712
        return s.exec(stmt.order_by(WatchEvent.id.desc()).limit(50)).all()


@router.post('/updates/test')
def test_notification(pid: int = Depends(require_grown_up)):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        recent = s.exec(select(WatchEvent.id).where(
            WatchEvent.profile_id == pid, WatchEvent.watch_id == 0, WatchEvent.created_at > now - 30
        ).limit(1)).first()
        if recent is not None:
            raise HTTPException(429, 'Wait 30 seconds between test notifications.')
        # Zero is reserved for updates created without a scheduled watch.
        event = WatchEvent(watch_id=0, profile_id=pid, text='This is a test notification from The Watcher.',
                           url='', created_at=now)
        s.add(event)
        s.commit()
        s.refresh(event)
        return event


@router.post('/updates/{event_id}/read')
def read_event(event_id: int, pid: int = Depends(require_grown_up)):
    with session() as s:
        result = s.execute(update(WatchEvent).where(WatchEvent.id == event_id, WatchEvent.profile_id == pid)
                           .values(unread=False))
        if not result.rowcount:
            raise HTTPException(404, 'Update not found.')
        s.commit()
    return {'ok': True}


@router.patch('/watches/{watch_id}')
def patch(watch_id: int, body: WatchPatch, pid: int = Depends(require_grown_up)):
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        watch = s.get(PersonalWatch, watch_id)
        if watch and watch.profile_id == pid and body.enabled and completed(
            Plan.model_validate_json(watch.plan_json), watch.checkpoint
        ):
            raise HTTPException(409, 'All conditions for this watch have been met.')
        result = s.execute(update(PersonalWatch).where(PersonalWatch.id == watch_id, PersonalWatch.profile_id == pid)
                           .values(enabled=body.enabled, next_check=time.time(), claim='', lease_until=0))
        if not result.rowcount:
            raise HTTPException(404, 'Watch not found.')
        s.commit()
    return {'ok': True}


@router.delete('/watches/{watch_id}')
def remove(watch_id: int, pid: int = Depends(require_grown_up)):
    with session() as s:
        result = s.execute(delete(PersonalWatch).where(PersonalWatch.id == watch_id, PersonalWatch.profile_id == pid))
        if not result.rowcount:
            raise HTTPException(404, 'Watch not found.')
        s.execute(delete(WatchEvent).where(WatchEvent.watch_id == watch_id, WatchEvent.profile_id == pid))
        s.commit()
    return {'ok': True}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def evidence_url(item):
    try:
        return canonical(item['url'])
    except (KeyError, ValueError):
        return ''


def search_matches(plan, rows, seen):
    pending = [{'condition': i + 1, 'requirement': term} for i, term in enumerate(plan.terms) if str(i + 1) not in seen]
    if not pending or not rows:
        return []
    schema = {'type': 'object', 'properties': {'matches': {'type': 'array', 'maxItems': 8, 'items': {
        'type': 'object', 'properties': {'condition': {'type': 'integer'}, 'item': {'type': 'integer'}},
        'required': ['condition', 'item']}}}, 'required': ['matches']}
    result = ai._call(
        'Select a search result only when its title or snippet explicitly confirms that the condition already '
        'happened. Reject rumors, predictions, announcements of future events, mismatched versions and ambiguous '
        'results. Return the condition number and result number. Return an empty array when there is no evidence.',
        f'Requirements: <data>{ai._esc(json.dumps(pending))}</data>\nResults: {ai._data(rows)}',
        schema, timeout_s=25, role='watcher_check')
    allowed = {entry['condition'] for entry in pending}
    matches = []
    entries = result.get('matches')
    if not isinstance(entries, list):
        raise ValueError('Watcher returned an invalid result.')
    for entry in entries[:8]:
        if not isinstance(entry, dict):
            continue
        condition, item = entry.get('condition'), entry.get('item')
        if type(condition) is int and condition in allowed and type(item) is int and 1 <= item <= len(rows):
            row = rows[item - 1]
            if url := evidence_url(row):
                matches.append((str(condition), row['title'][:500], url))
                allowed.remove(condition)
    return matches


def observe(watch):
    plan = Plan.model_validate_json(watch.plan_json)
    if plan.mode == 'page_watch':
        item = providers.page_watch(plan.source, config={'content_selector': plan.selector} if plan.selector else {})[0]
        text = '\n'.join(line for raw in item['body'][:20_000].splitlines() if (line := ' '.join(raw.split())))
        if not text:
            raise ValueError('Watch source was empty.')
        if not watch.checkpoint or text == watch.checkpoint:
            return text, []
        diff = watchers.diff(watch.checkpoint, text, limit=50)
        added = ' '.join(op['text'] for op in diff['ops'] if op['op'] == 'added')
        summary = added or 'Text was removed from the watched page.'
        return text, [(summary[:500], evidence_url(item))]

    seen = set(json.loads(watch.checkpoint or '[]'))
    search = plan.mode in LOOKUP_MODES
    if search:
        if len(seen) == len(plan.terms):
            return watch.checkpoint, []
        rows = providers.PROVIDERS[plan.mode].run('', plan.source)[:20]
        matches = search_matches(plan, rows, seen)
    else:
        rows = providers.PROVIDERS[plan.mode].run(plan.source)
        matches = [(digest(url), row['title'][:500], url) for row in rows if (url := evidence_url(row))
                   and (not plan.terms or any(term.casefold() in row['title'].casefold() for term in plan.terms))]
    updates = []
    for key, title, url in matches:
        if key not in seen and (search or watch.checkpoint):
            updates.append((title, url))
        seen.add(key)
    return json.dumps(sorted(seen)), updates


def check_one():
    now = time.time()
    claim = uuid4().hex
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        watch = s.exec(select(PersonalWatch).where(PersonalWatch.enabled == True,  # noqa: E712
                                                   PersonalWatch.next_check <= now, PersonalWatch.lease_until <= now)
                       .order_by(PersonalWatch.next_check).limit(1)).first()
        if not watch:
            return False
        if not hub_allowed(s, watch.profile_id):
            watch.enabled = False
            s.add(watch)
            s.commit()
            return True
        watch.claim, watch.lease_until = claim, now + 300
        s.add(watch)
        s.commit()
        s.refresh(watch)

    error = ''
    try:
        checkpoint, updates = observe(watch)
    except Exception as exc:
        log.info('Watcher %s check failed (%s)', watch.id, type(exc).__name__)
        checkpoint, updates = watch.checkpoint, []
        error = 'The check failed. Verify that the source and any configured services are reachable.'
    finished = time.time()
    with session() as s:
        result = s.execute(update(PersonalWatch).where(PersonalWatch.id == watch.id, PersonalWatch.claim == claim)
                           .values(claim='', lease_until=0, last_check=finished, next_check=finished + watch.interval * 60,
                                   checkpoint=checkpoint, error=error, enabled=not completed(
                                       Plan.model_validate_json(watch.plan_json), checkpoint)))
        if result.rowcount:
            for text, url in updates:
                s.add(WatchEvent(watch_id=watch.id, profile_id=watch.profile_id, text=text, url=url, created_at=finished))
        s.commit()
    return True


async def _worker():
    while True:
        try:
            worked = await asyncio.to_thread(check_one)
        except Exception:
            log.exception('Watcher scheduler failed')
            worked = False
        await asyncio.sleep(0 if worked else 5)


async def scheduler():
    # Bound slow provider and model calls while allowing other watches to stay on schedule.
    await asyncio.gather(*(_worker() for _ in range(4)))
