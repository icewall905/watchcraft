"""Whole Pinterest board imports, a small slice per scheduler tick, through ``pinterest_guard``.

Each tick does at most one guarded Pinterest page request or one spaced image capture, then hands
the slot back. Progress (board id, feed bookmark, pins done) is durable on the MoodImport row, so a
budget pause, block, captcha or restart only delays the job: it waits (``next_at``) and resumes where
it stopped. Only a board Pinterest will not show at all ends in an error.
"""
import asyncio
import logging
import random
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlalchemy import func

from ..access import require_hub, hub_allowed
from ..db import session
from .. import curation
from . import moodboard_sources, moodboards, pinterest_guard
from .models import MoodImport, MoodBoard, MoodPin, MoodAsset

router = APIRouter(prefix='/hub/boards', tags=['Mood boards'])
log = logging.getLogger(__name__)
BOARD = 'https://www.pinterest.com/resource/BoardResource/get/'
BOARD_FEED = 'https://www.pinterest.com/resource/BoardFeedResource/get/'
ACTIVE = ('queued', 'importing', 'waiting')
IMAGE_GAP = (6.0, 15.0)     # seconds between image captures of one import
BACKOFF, MAX_BACKOFF = 900, 86_400
ITEM_TRIES = 4              # an image that keeps failing is skipped after this many spaced tries
MAX_FAILURES = 8            # page errors in a row (not blocks) before the import gives up
LEASE_S = 900


def board_url(value):
    try:
        p = urlsplit(value.strip())
        if (p.scheme != 'https' or p.hostname not in ('pinterest.com', 'www.pinterest.com')
                or p.username or p.password or p.port not in (None, 443)):
            raise ValueError()
        parts = p.path.strip('/').removesuffix('.rss').split('/')
        if len(parts) != 2 or any(not re.fullmatch(r'[\w-]{1,100}', v, re.ASCII) for v in parts):
            raise ValueError()
        if parts[0].lower() in ('pin', 'ideas', 'search', 'settings', 'business', 'login') or parts[1].lower() in ('feed', '_saved', '_created'):
            raise ValueError()
        return 'https://www.pinterest.com/' + '/'.join(parts)
    except (ValueError, AttributeError):
        raise ValueError('Paste a public Pinterest board link: https://www.pinterest.com/username/board-name/') from None


def pin_url(value):
    try:
        p = urlsplit(value)
        if (p.scheme == 'https' and p.hostname in ('pinterest.com', 'www.pinterest.com')
                and not p.username and not p.password and p.port in (None, 443)
                and re.fullmatch(r'/pin/[A-Za-z0-9_-]{1,150}/?', p.path)):
            return 'https://www.pinterest.com' + p.path.rstrip('/') + '/'
    except (ValueError, TypeError):
        pass
    return ''


def board_meta(url):
    """(board id, pin count) of a public board. ValueError when Pinterest will not show it."""
    user, slug = urlsplit(board_url(url)).path.strip('/').split('/')
    data, _ = moodboard_sources.pinterest_resource(
        BOARD, f'/{user}/{slug}/', {'username': user, 'slug': slug, 'field_set_key': 'detailed'},
        origin='import', refusal_blocks=False, what='board')
    ref = str(data.get('id') or '') if isinstance(data, dict) else ''
    if not re.fullmatch(r'\d{1,25}', ref):
        raise ValueError('Pinterest did not return a public board')
    count = data.get('pin_count')
    return ref, count if isinstance(count, int) and 0 <= count < 1_000_000 else 0


def board_page(url, ref, cursor=''):
    """One guarded board-feed page -> (pins, next bookmark or '')."""
    user, slug = urlsplit(board_url(url)).path.strip('/').split('/')
    options = {'board_id': ref, 'board_url': f'/{user}/{slug}/', 'page_size': 25,
               'field_set_key': 'react_grid_pin', 'bookmarks': [cursor] if cursor else []}
    data, following = moodboard_sources.pinterest_resource(BOARD_FEED, f'/{user}/{slug}/', options,
                                                           origin='import', refusal_blocks=False, what='board')
    items = [dict(source_url=v['source_url'], image_url=v['image_url'], title=(v.get('title') or 'Pinterest image')[:500])
             for v in moodboard_sources.parse_pinterest(data, 50)]
    return items, following


def payload(job):
    data = job.model_dump(include={'id', 'board_id', 'url', 'status', 'imported', 'skipped', 'existing', 'total', 'error', 'note', 'created_at'})
    data['done'] = job.imported + job.skipped + job.existing
    data['next_at'] = int(job.next_at) if job.status == 'waiting' else 0
    return data


class ImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)


@router.post('/{bid}/pinterest')
def enqueue(bid: int, data: ImportRequest, pid: int = Depends(require_hub)):
    try:
        url = board_url(data.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        moodboards.owned(s, pid, bid, manage=True)
        if s.exec(select(MoodImport).where(MoodImport.board_id == bid, MoodImport.status.in_(ACTIVE))).first():
            raise HTTPException(409, 'A Pinterest import is already running for this board. It continues in the background.')
        if s.exec(select(func.count()).select_from(MoodPin).where(MoodPin.board_id == bid)).one() >= moodboards.MAX_PINS:
            raise HTTPException(409, 'This board has reached its image limit.')
        # The same board link again picks up an unfinished import instead of starting over.
        job = s.exec(select(MoodImport).where(MoodImport.board_id == bid, MoodImport.url == url, MoodImport.full == True,  # noqa: E712
                                              MoodImport.status == 'error').order_by(MoodImport.id.desc())).first()
        if job:
            job.status, job.error, job.note, job.failures, job.next_at = 'queued', '', '', 0, 0
        else:
            # Keep a bounded job history for each profile.
            old = s.exec(select(MoodImport).where(MoodImport.profile_id == pid).order_by(MoodImport.id.desc()).offset(19)).all()
            for row in old:
                s.delete(row)
            job = MoodImport(board_id=bid, profile_id=pid, url=url, full=True)
        s.add(job)
        s.commit()
        s.refresh(job)
        return {'import': payload(job)}


@router.get('/{bid}/pinterest')
def status(bid: int, pid: int = Depends(require_hub)):
    with session() as s:
        moodboards.owned(s, pid, bid)
        job = s.exec(select(MoodImport).where(MoodImport.profile_id == pid, MoodImport.board_id == bid).order_by(MoodImport.id.desc())).first()
        return {'import': payload(job) if job else None}


def _blocked_image(exc):
    if isinstance(exc, pinterest_guard.Paused):
        return True
    if isinstance(exc, curation.FetchError):
        return exc.status in pinterest_guard.BLOCK_STATUSES
    text = str(exc).lower()
    return isinstance(exc, ValueError) and ('content type' in text or 'requested a pause' in text)


def _pending(job):
    done = set(job.processed)
    return [v for v in job.candidates if v['source_url'] not in done]


def _wait(job, seconds, note):
    job.status, job.note = 'waiting', note[:300]
    job.next_at = time.time() + max(1, seconds)


def step(job):
    """One slice of work on a leased job, mutating it. Pinterest is asked at most once."""
    if not job.board_ref:
        job.board_ref, job.total = board_meta(job.url)
        job.fetched = True
        return
    pending = _pending(job)
    if not pending:
        if job.exhausted:
            job.status = 'complete'
            return
        items, following = board_page(job.url, job.board_ref, job.cursor)
        job.candidates, job.processed = items, []
        job.cursor, job.exhausted = following, not following or following == job.cursor
        job.failures = 0
        return
    item = pending[0]
    with session() as s:
        duplicate = s.exec(select(MoodPin.id).where(MoodPin.board_id == job.board_id, MoodPin.source_url == item['source_url'])).first()
    if duplicate:
        job.existing += 1
        job.processed = [*job.processed, item['source_url']]
        return
    try:
        captured = moodboards.capture_image(item['image_url'])
    except pinterest_guard.Paused:
        raise  # Budget or block: the item stays pending and does not count as a failure.
    except Exception as exc:
        if not _blocked_image(exc) or job.failures + 1 >= ITEM_TRIES:
            job.skipped += 1
            job.failures = 0
            job.processed = [*job.processed, item['source_url']]
            return
        raise
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        board = s.get(MoodBoard, job.board_id)
        if not board or board.profile_id != job.profile_id or not s.get(MoodImport, job.id):
            return
        duplicate = s.exec(select(MoodPin.id).where(MoodPin.board_id == job.board_id, MoodPin.source_url == item['source_url'])).first()
        count = s.exec(select(func.count()).select_from(MoodPin).where(MoodPin.board_id == job.board_id)).one()
        if count >= moodboards.MAX_PINS:
            job.status, job.error = 'complete', 'This board reached its image limit.'
            return
        if duplicate:
            job.existing += 1
        else:
            data, mime, width, height = captured
            pin = MoodPin(board_id=job.board_id, profile_id=job.profile_id, **item, saved=True,
                          media_type='gif' if mime == 'image/gif' else 'image', width=width, height=height,
                          attribution='Imported from Pinterest', license='Rights retained by original creators')
            s.add(pin)
            s.flush()
            s.add(MoodAsset(pin_id=pin.id, data=data, mime=mime))
            job.imported += 1
            # Commit the pin and the progress together so a crash cannot import it twice.
            row = s.get(MoodImport, job.id)
            row.imported, row.existing = job.imported, job.existing
            row.processed = [*job.processed, item['source_url']]
            s.add(row)
        s.commit()
    job.failures = 0
    job.processed = [*job.processed, item['source_url']]
    _wait(job, random.uniform(*IMAGE_GAP), '')  # Spacing only; the UI still shows it as importing.


def run(jid, lease):
    with session() as s:
        job = s.get(MoodImport, jid)
        if not job or job.lease != lease:
            return
        s.expunge(job)
    job.status = 'importing'
    try:
        step(job)
    except pinterest_guard.Paused as exc:
        _wait(job, exc.retry_after, str(exc))
    except Exception as exc:
        blocked = _blocked_image(exc)
        job.failures += 1
        missing = exc.status in (400, 404, 410) if isinstance(exc, curation.FetchError) else isinstance(exc, ValueError)
        if not job.board_ref and not blocked and missing:
            job.status = 'error'
            job.error = 'Pinterest did not show this board. Check that the link is a public board. Private boards cannot be imported.'
        elif job.failures >= MAX_FAILURES and not blocked:
            job.status = 'error'
            job.error = 'Pinterest kept failing. Import the same board link again to continue where it stopped.'
        else:
            if not blocked:
                log.info('Pinterest import %s will retry: %s', jid, exc)
            _wait(job, min(MAX_BACKOFF, BACKOFF * 2 ** (job.failures - 1)),
                  'Pinterest asked to slow down.' if blocked else 'Pinterest did not answer.')
    with session() as s:
        row = s.get(MoodImport, jid)
        if not row or row.lease != lease:
            return
        for name in ('status', 'imported', 'skipped', 'existing', 'total', 'processed', 'candidates', 'fetched',
                     'board_ref', 'cursor', 'exhausted', 'failures', 'next_at', 'note', 'error'):
            setattr(row, name, getattr(job, name))
        if row.status == 'complete':
            row.total, row.note, row.next_at = row.imported + row.skipped + row.existing, '', 0
        row.lease, row.lease_until = '', 0
        s.add(row)
        s.commit()


def dispatch_due():
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        jobs = s.exec(select(MoodImport).where(
            ((MoodImport.status.in_(['queued', 'waiting'])) & (MoodImport.next_at <= now))
            | ((MoodImport.status == 'importing') & (MoodImport.lease_until < now))).order_by(MoodImport.next_at, MoodImport.id)).all()
        job = None
        for candidate in jobs:
            board = s.get(MoodBoard, candidate.board_id)
            if not board or board.profile_id != candidate.profile_id or not hub_allowed(s, candidate.profile_id):
                candidate.status = 'error'
                candidate.error = 'Board or profile is no longer available.'
                candidate.lease = ''
                s.add(candidate)
            else:
                job = candidate
                break
        if not job:
            s.commit()
            return False
        job.status = 'importing'
        job.lease = secrets.token_hex(16)
        job.lease_until = now + LEASE_S
        s.add(job)
        s.commit()
        jid, lease = job.id, job.lease
    run(jid, lease)
    return True


def resume_partial():
    """Requeue boards whose import stopped early (old 25-pin snapshots, failures) as whole-board imports.

    The latest job per board and link is reused and walks the board from the start; pins already on the
    board are recognized locally (counted as ``existing``) and never fetched again. Idempotent: resumed jobs are ``full``.
    """
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        latest = {}
        for job in s.exec(select(MoodImport).order_by(MoodImport.id)).all():
            latest[(job.board_id, job.url)] = job
        busy = {job.board_id for job in latest.values() if job.status in ACTIVE}
        count = 0
        for job in latest.values():
            if job.full or job.status in ACTIVE or job.board_id in busy:
                continue
            board = s.get(MoodBoard, job.board_id)
            if not board or board.profile_id != job.profile_id:
                continue
            job.full, job.status, job.error, job.note = True, 'queued', '', ''
            job.candidates, job.processed, job.fetched = [], [], False
            job.board_ref, job.cursor, job.exhausted, job.failures, job.next_at = '', '', False, 0, 0
            job.imported, job.skipped, job.existing, job.total = 0, 0, 0, 0
            s.add(job)
            busy.add(job.board_id)
            count += 1
        s.commit()
    if count:
        log.info('Resuming %s partial Pinterest board import(s) in the background', count)
    return count


async def scheduler():
    swept = 0.0
    while True:
        if time.monotonic() - swept > 600:  # at startup, then now and then: one resumed link per board at a time
            swept = time.monotonic()
            try:
                await asyncio.to_thread(resume_partial)
            except Exception:
                log.exception('Resuming partial Pinterest imports failed')
        try:
            worked = await asyncio.to_thread(dispatch_due)
        except Exception:
            log.exception('Pinterest import tick failed')
            worked = False
        await asyncio.sleep(2 if worked else 10)
