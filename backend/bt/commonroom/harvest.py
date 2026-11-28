"""Fetch posts for a thread: fresh pages of its sources, keyword searches, and backfill down the sort ladder.

Network calls run outside DB transactions. A source is shared by every thread that follows it, so a
fresh poll happens at most every FRESH_S seconds however many threads ask.

Nothing is given up for good: a source that walked its whole ladder re-arms after REARM_S (top-of-week
and top-of-month lists refill), a failing one is retried daily, and a host that asks for a pause backs
the source off without counting as a failure."""
import logging
import re
import time

from sqlmodel import select

from ..db import session
from ..hub import providers
from ..hub.social_fetch import Paused
from . import reddit_oauth, store
from .adapters import ADAPTERS
from .models import CRSource, CRThread, CRThreadSource

log = logging.getLogger(__name__)
FRESH_S = 15 * 60
SOURCES_PER_RUN = 12
BACKFILL_PER_RUN = 6
SEARCHES_PER_RUN = 2
MAX_BACKOFF_S = 6 * 3600
BROKEN_AFTER = 5           # straight failures before a source is marked broken and retried daily
BROKEN_RETRY_S = 86400
REARM_S = 3 * 86400        # an exhausted ladder starts again from the top after this long


def interests(thread: CRThread) -> list[str]:
    parts = [p.strip() for p in re.split(r'[,\n;]+', thread.interests or '') if p.strip()]
    return parts or ([thread.name.strip()] if thread.name.strip() else [])


def search_terms(thread: CRThread) -> list[str]:
    """The owner's interests first, then the expedition's subtopics, without repeats."""
    out, seen = [], set()
    for term in interests(thread) + [str(x).strip() for x in (thread.search_terms or []) if str(x).strip()]:
        if term.lower() not in seen:
            seen.add(term.lower())
            out.append(term[:120])
    return out


def pause_of(e: Exception) -> float:
    """Seconds a host asked us to wait, or 0 when the error is a real failure."""
    if isinstance(e, (Paused, providers.RateLimited)):
        return max(60.0, float(getattr(e, 'retry_in', 0) or 0) or 300.0)
    return 0.0


def _record(sid: int, ok: bool, error: str = '', pause: float = 0.0, hits: int = 0, **fields):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        src = s.get(CRSource, sid)
        if src:
            for k, v in fields.items():
                setattr(src, k, v)
            src.hits += hits
            if ok:
                src.failures, src.error, src.last_ok = 0, '', now
                if src.status == 'broken':
                    src.status = 'ok'
            elif pause:
                src.error = error[:300]
                src.next_poll = max(src.next_poll, now + min(pause, MAX_BACKOFF_S))
            else:
                src.failures += 1
                src.error = error[:300]
                wait = min(MAX_BACKOFF_S, 120 * 2 ** min(src.failures, 8))
                if src.failures >= BROKEN_AFTER:
                    wait = BROKEN_RETRY_S
                    if src.status == 'ok':
                        src.status = 'broken'
                src.next_poll = max(src.next_poll, now + wait)
            s.add(src)
        s.commit()


def _fail(src: CRSource, what: str, e: Exception):
    log.info('Common Room %s %s %s failed: %s', what, src.platform, src.locator, e)
    _record(src.id, False, str(e) or type(e).__name__, pause=pause_of(e))


def rearm(thread_id: int, now: float | None = None) -> int:
    """Start exhausted ladders of this thread's sources again once REARM_S has passed."""
    now = now or time.time()
    srcs = select(CRThreadSource.source_id).where(CRThreadSource.thread_id == thread_id)
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        rows = s.exec(select(CRSource).where(CRSource.id.in_(srcs), CRSource.exhausted == True,  # noqa: E712
                                             CRSource.exhausted_at < now - REARM_S)).all()
        for r in rows:
            r.exhausted, r.ladder, r.backfill_cursor, r.exhausted_at = False, 0, '', 0
            s.add(r)
        s.commit()
        return len(rows)


def _sources(thread_id: int) -> list[CRSource]:
    with session() as s:
        rows = s.exec(select(CRSource).join(CRThreadSource, CRThreadSource.source_id == CRSource.id)
                      .where(CRThreadSource.thread_id == thread_id, CRThreadSource.enabled == True,  # noqa: E712
                             CRSource.status != 'disabled')
                      .order_by(CRSource.next_poll)).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


def poll_gap(source_id: int) -> float:
    """FRESH_S, doubled per throttle level when every sub following the source is skipping its posts."""
    with session() as s:
        levels = s.exec(select(CRThreadSource.throttle).where(CRThreadSource.source_id == source_id,
                                                              CRThreadSource.enabled == True)).all()  # noqa: E712
    return FRESH_S * 2 ** min(levels or [0])


def fresh(src: CRSource) -> int:
    adapter = ADAPTERS.get(src.platform)
    if not adapter:
        return 0
    try:
        page = adapter.fetch(src.locator, adapter.sorts[0], '')
    except Exception as e:  # noqa: BLE001 — one broken source must not stop the run
        _fail(src, 'fresh', e)
        return 0
    ids = store.upsert_posts(page.posts, source_id=src.id)
    _record(src.id, True, hits=len(ids), next_poll=time.time() + poll_gap(src.id))
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
        _fail(src, 'backfill', e)
        return 0
    ids = store.upsert_posts(page.posts, source_id=src.id)
    if page.cursor and page.posts:
        _record(src.id, True, hits=len(ids), backfill_cursor=page.cursor)
    elif rung + 1 < len(sorts):
        _record(src.id, True, hits=len(ids), ladder=rung + 1, backfill_cursor='')
    else:
        _record(src.id, True, hits=len(ids), exhausted=True, exhausted_at=time.time(), backfill_cursor='')
    return len(ids)


def search(thread: CRThread, want_more: bool) -> int:
    """Keyword searches across every platform that supports them, rotating through interests."""
    terms = search_terms(thread)
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


def _open(src: CRSource, now: float) -> bool:
    return not src.exhausted and not (src.failures and src.next_poll > now)


def run(thread: CRThread, want_more: bool) -> dict:
    now = time.time()
    rearm(thread.id, now)
    sources = _sources(thread.id)
    stats = {'fresh': 0, 'backfill': 0, 'search': 0, 'sources': len(sources)}
    for src in [x for x in sources if x.next_poll <= now][:SOURCES_PER_RUN]:
        stats['fresh'] += fresh(src)
    if want_more:
        open_ = [x for x in sources if _open(x, now)]
        # Keyless Reddit shares one slow paced queue with News: one older rung per run is plenty.
        if not reddit_oauth.configured():
            first = next((x for x in open_ if x.platform == 'reddit'), None)
            open_ = [x for x in open_ if x.platform != 'reddit' or x is first]
        for src in open_[:BACKFILL_PER_RUN]:
            stats['backfill'] += backfill(src)
    stats['search'] = search(thread, want_more)
    return stats


def exhausted(thread_id: int) -> bool:
    """True when no source can give older posts now: walked to its end, backing off after an error, or
    without an adapter. Searches still bring new posts over time, and ladders re-arm after REARM_S."""
    now = time.time()
    return all(x.platform not in ADAPTERS or not _open(x, now) for x in _sources(thread_id))
