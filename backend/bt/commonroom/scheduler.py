"""Lease-based background curator loop for Common Room threads."""
import asyncio
import logging
import secrets
import time

from sqlalchemy import delete, func, or_
from sqlmodel import select

from ..access import hub_worker_allowed, is_child
from ..db import session
from . import curate, expedition, harvest
from .models import (CRAsset, CRCandidate, CRComments, CRPick, CRPost, CRPostVector, CRSourcePost, CRThread)

log = logging.getLogger(__name__)
STALE_S = 1800
MORE_GAP_S = 20  # pause between back-to-back backfill runs of one thread
RETAIN_S = 21 * 86400
_pruned = [0.0]


def claim() -> tuple[CRThread, str] | None:
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        rows = s.exec(select(CRThread).where(
            ((CRThread.enabled == True) & (CRThread.lease == '') &  # noqa: E712
             or_(CRThread.next_run <= now,
                 (CRThread.want_more == True) & (func.coalesce(CRThread.last_run, 0) < now - MORE_GAP_S))) |  # noqa: E712
            ((CRThread.lease != '') & (CRThread.build_started < now - STALE_S)))
            .order_by(CRThread.want_more.desc(), CRThread.next_run).limit(200)).all()
        thread = next((t for t in rows if hub_worker_allowed(s, t.profile_id)), None)
        if not thread:
            s.commit()
            return None
        lease = secrets.token_hex(16)
        thread.lease, thread.build_started, thread.last_status = lease, now, 'collecting'
        if is_child(s, thread.profile_id):
            thread.child_friendly, thread.strictness = True, 'strict'
        s.add(thread)
        s.commit()
        s.refresh(thread)
        s.expunge(thread)
        return thread, lease


def finish(tid: int, lease: str, status: str, error: str = '', want_more: bool | None = None):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        t = s.get(CRThread, tid)
        if t and t.lease == lease:
            t.lease, t.build_started, t.last_run = '', 0, now
            t.last_status, t.error = status, error[:500]
            t.source_offset += 1
            t.next_run = now + t.interval_minutes * 60 if not error else now + 300
            if want_more is not None or error:
                t.want_more = bool(want_more) and not error
            s.add(t)
        s.commit()


def run_thread(thread: CRThread, lease: str) -> dict:
    want_more = thread.want_more
    try:
        if thread.discovered_at < time.time() - expedition.WEEKLY_S:
            expedition.request(thread.id, 'weekly' if thread.discovered_at else 'new', min_gap=0)
        stats = harvest.run(thread, want_more)
        stats.update(curate.run(thread, lease))
    except Exception as e:  # noqa: BLE001 — the thread retries in a few minutes
        log.exception('Common Room run failed for thread %s', thread.id)
        finish(thread.id, lease, 'error', str(e) or type(e).__name__)
        return {}
    status = (f'Kept {stats["kept"]} of {stats["judged"]} posts judged from {stats["sources"]} sources'
              f' ({stats["fresh"] + stats["backfill"] + stats["search"]} fetched).')
    # Keep backfilling while a backfill run still finds keepers and older pages remain.
    dry = harvest.exhausted(thread.id)
    more = want_more and stats['kept'] < 5 and not dry and stats['backfill'] > 0
    if want_more and stats['kept'] < 5 and (dry or not stats['sources']):
        expedition.request(thread.id, 'dry')  # the sources ran out: go find more places
    expedition.prune_links(thread.id)
    finish(thread.id, lease, status, want_more=more)
    return stats


def dispatch_due() -> bool:
    got = claim()
    if not got:
        return False
    run_thread(*got)
    return True


def prune(now: float | None = None):
    """Drop old posts no thread kept. Saved or picked posts stay."""
    now = now or time.time()
    if now - _pruned[0] < 3600:
        return
    _pruned[0] = now
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        picked = select(CRPick.post_id)
        old = list(s.exec(select(CRPost.id).where(CRPost.fetched_at < now - RETAIN_S, CRPost.id.not_in(picked))
                          .limit(5000)).all())
        if old:
            for model, col in ((CRSourcePost, CRSourcePost.post_id), (CRCandidate, CRCandidate.post_id),
                               (CRPostVector, CRPostVector.post_id), (CRComments, CRComments.post_id),
                               (CRAsset, CRAsset.post_id), (CRPost, CRPost.id)):
                s.exec(delete(model).where(col.in_(old)))
        # Comments of kept posts stay: they are the offline copy shown when the source cannot be reached.
        s.exec(delete(CRComments).where(CRComments.fetched_at < now - 86400, CRComments.post_id.not_in(picked)))
        s.commit()
    from . import assets
    assets.gc()


async def scheduler():
    while True:
        try:
            worked = await asyncio.to_thread(dispatch_due)
        except Exception:
            log.exception('Common Room scheduler tick failed')
            worked = False
        try:
            from . import previews
            await asyncio.to_thread(previews.sweep)
        except Exception:
            log.exception('Common Room previews failed')
        try:
            from . import store
            await asyncio.to_thread(store.backfill_languages)
        except Exception:
            log.exception('Common Room language backfill failed')
        try:
            from . import assets
            await asyncio.to_thread(assets.sweep)
        except Exception:
            log.exception('Common Room local copies failed')
        try:
            await asyncio.to_thread(prune)
        except Exception:
            log.exception('Common Room prune failed')
        await asyncio.sleep(2 if worked else 20)
