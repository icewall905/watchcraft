"""Fetch posts for a thread: fresh pages of its sources, keyword searches, and backfill down the sort ladder.

Network calls run outside DB transactions. A source is shared by every thread that follows it, so a
fresh poll happens at most every FRESH_S seconds however many threads ask."""
import logging
import re
import time

from sqlmodel import select

from ..db import session
from . import store
from .adapters import ADAPTERS
from .models import CRSource, CRThread, CRThreadSource

log = logging.getLogger(__name__)
FRESH_S = 15 * 60
SOURCES_PER_RUN = 12
BACKFILL_PER_RUN = 6
SEARCHES_PER_RUN = 2
MAX_BACKOFF_S = 6 * 3600


def interests(thread: CRThread) -> list[str]:
    parts = [p.strip() for p in re.split(r'[,\n;]+', thread.interests or '') if p.strip()]
    return parts or ([thread.name.strip()] if thread.name.strip() else [])


def _record(sid: int, ok: bool, error: str = '', **fields):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        src = s.get(CRSource, sid)
        if src:
            for k, v in fields.items():
                setattr(src, k, v)
            if ok:
                src.failures, src.error, src.last_ok = 0, '', now
            else:
                src.failures += 1
                src.error = error[:300]
                src.next_poll = max(src.next_poll, now + min(MAX_BACKOFF_S, 120 * 2 ** min(src.failures, 8)))
            s.add(src)
        s.commit()


def _sources(thread_id: int) -> list[CRSource]:
    with session() as s:
        rows = s.exec(select(CRSource).join(CRThreadSource, CRThreadSource.source_id == CRSource.id)
                      .where(CRThreadSource.thread_id == thread_id, CRThreadSource.enabled == True)  # noqa: E712
                      .order_by(CRSource.next_poll)).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


def fresh(src: CRSource) -> int:
    adapter = ADAPTERS.get(src.platform)
    if not adapter:
        return 0
    try:
        page = adapter.fetch(src.locator, adapter.sorts[0], '')
    except Exception as e:  # noqa: BLE001 — one broken source must not stop the run
        log.info('Common Room fresh %s %s failed: %s', src.platform, src.locator, e)
        _record(src.id, False, str(e) or type(e).__name__)
        return 0
    ids = store.upsert_posts(page.posts, source_id=src.id)
    _record(src.id, True, next_poll=time.time() + FRESH_S)
    return len(ids)


def backfill(src: CRSource) -> int:
    """One older page: walk the current sort to its end, then move to the next rung of the ladder."""
    adapter = ADAPTERS.get(src.platform)
    if not adapter or src.exhausted:
        return 0
    sorts = adapter.sorts
    rung = min(src.ladder, len(sorts) - 1)
    try:
        page = adapter.fetch(src.locator, sorts[rung], src.backfill_cursor)
    except Exception as e:  # noqa: BLE001
        log.info('Common Room backfill %s %s failed: %s', src.platform, src.locator, e)
        _record(src.id, False, str(e) or type(e).__name__)
        return 0
    ids = store.upsert_posts(page.posts, source_id=src.id)
    if page.cursor and page.posts:
        _record(src.id, True, backfill_cursor=page.cursor)
    elif rung + 1 < len(sorts):
        _record(src.id, True, ladder=rung + 1, backfill_cursor='')
    else:
        _record(src.id, True, exhausted=True, backfill_cursor='')
    return len(ids)


def search(thread: CRThread, want_more: bool) -> int:
    """Keyword searches across every platform that supports them, rotating through interests."""
    terms = interests(thread)
    if not terms:
        return 0
    n = SEARCHES_PER_RUN * (2 if want_more else 1)
    picked = [terms[(thread.source_offset + i) % len(terms)] for i in range(min(n, len(terms)))]
    found = 0
    for q in picked:
        for adapter in ADAPTERS.values():
            if not adapter.search:
                continue
            try:
                page = adapter.search(q, '')
            except Exception as e:  # noqa: BLE001
                log.info('Common Room search %s %r failed: %s', adapter.key, q, e)
                continue
            found += len(store.upsert_posts(page.posts, thread_id=thread.id))
    return found


def run(thread: CRThread, want_more: bool) -> dict:
    now = time.time()
    sources = _sources(thread.id)
    stats = {'fresh': 0, 'backfill': 0, 'search': 0, 'sources': len(sources)}
    for src in [x for x in sources if x.next_poll <= now][:SOURCES_PER_RUN]:
        stats['fresh'] += fresh(src)
    if want_more:
        open_ = [x for x in sources if not x.exhausted and x.failures < 5 and not (x.failures and x.next_poll > now)]
        for src in open_[:BACKFILL_PER_RUN]:
            stats['backfill'] += backfill(src)
    stats['search'] = search(thread, want_more)
    return stats


def exhausted(thread_id: int) -> bool:
    """True when no source can give older posts now: walked to its end, failing, backing off after an
    error, or without an adapter. Searches still bring new posts over time."""
    now = time.time()
    return all(x.exhausted or x.failures >= 5 or x.platform not in ADAPTERS or (x.failures and x.next_poll > now)
               for x in _sources(thread_id))
