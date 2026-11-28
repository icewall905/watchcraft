"""Profile-scoped progress toward a stocked, curated news shelf.

The readiness goal is deliberately stricter than a master-curator verdict: an
article must also have a locally archived, worker-reviewed body and an AI summary.
The larger candidate buffer keeps collectors running until there is enough judged
material to refill that ready shelf as items are read and age out.

``interest_plan`` splits the same bounded universe per interest (curator) so one
saturated interest cannot hide another that has nothing; ``interest_decision`` turns
that into a per-curator boost / quiet-backoff decision for the scheduler.
"""
import math
import threading
import time

from sqlalchemy import func, or_
from sqlmodel import Session, select

from .article_cleanup import VERSION as CLEANUP_VERSION
from .models import (ArticleText, CaptureJob, ContentItem, Curator, CuratorDiscovery, CuratorRun,
                     ItemState, ItemVerdict, NewsImpression, Topic)

TARGET = 100
CANDIDATE_TARGET = 300
WINDOW_DAYS = 7
WINDOW_SECONDS = WINDOW_DAYS * 86400
QUALIFIED_TIERS = ('keep', 'feature')
ARTICLE_TYPES = ('news', 'article')
SCAN_LIMIT = 1200  # same bounded recent universe as /news

# Supply math (NEWS-CURATION-PLAN): ~90 impressions/day, so aim for at least this many
# new quality-approved articles per day across all interests, and unseen ready stock.
DAILY_FRESH_TARGET = 100
UNSEEN_TARGET = TARGET
FRESH_SECONDS = 86400
SATURATION = 2           # an interest with this multiple of its unseen share (and its daily share) is full
QUIET_RUNS = 3           # this many recent scheduled runs without a new article => back off
QUIET_LOOKBACK = 8
CATCH_UP = 3600          # boosted cadence: hourly, never faster
CACHE_TTL = 60

_cache: dict[int, tuple[float, float, dict]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _scan(s: Session, pid: int, now: float) -> list[tuple]:
    """Distinct visible article rows: (id, first_seen, tier, consumed, prepared)."""
    start = now - WINDOW_SECONDS
    published = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
    muted = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news', Topic.muted.is_(True))).all()
    muted_terms = [str(term).lower() for topic in muted for term in (topic.keywords or [topic.name])]
    # Discovery is an item-id subquery so multiple scouts/curators never inflate the counts.
    visible_ids = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)
    rows = s.exec(select(ContentItem.id, ContentItem.title, ContentItem.summary, ContentItem.first_seen,
                         ItemVerdict.tier, ItemState.read_at, ItemState.archived,
                         (func.length(func.trim(ContentItem.body, " \t\r\n\v\f ")) > 0), ContentItem.enrichment,
                         (func.length(func.trim(ArticleText.raw_body, " \t\r\n\v\f ")) > 0),
                         ArticleText.status, ArticleText.version,
                         CaptureJob.status, CaptureJob.summary_status)
                  .outerjoin(ItemState, (ItemState.item_id == ContentItem.id) & (ItemState.profile_id == pid))
                  .outerjoin(ItemVerdict, (ItemVerdict.item_id == ContentItem.id) & (ItemVerdict.profile_id == pid))
                  .outerjoin(ArticleText, ArticleText.item_id == ContentItem.id)
                  .outerjoin(CaptureJob, (CaptureJob.item_id == ContentItem.id) & (CaptureJob.profile_id == pid))
                  .where(ContentItem.id.in_(visible_ids), ContentItem.type.in_(ARTICLE_TYPES),
                         published >= start,
                         or_(ItemState.dismissed.is_(None), ItemState.dismissed.is_(False)),
                         or_(ItemVerdict.tier.is_(None), ItemVerdict.tier.not_in(('set_aside', 'reject'))))
                  .order_by(published.desc(), ContentItem.id.desc()).limit(SCAN_LIMIT)).all()

    out = []
    for (iid, title, summary, first_seen, tier, read_at, archived, body, enrichment, raw_body,
         archive_status, archive_version, job_status, summary_status) in rows:
        hay = f'{title or ""} {summary or ""}'.lower()
        if any(term in hay for term in muted_terms):
            continue
        enrichment = enrichment if isinstance(enrichment, dict) else {}
        prepared = (bool(body) and bool(raw_body) and archive_status == 'reviewed'
                    and archive_version == CLEANUP_VERSION and job_status in ('complete', 'partial')
                    and summary_status == 'ready' and bool(str(enrichment.get('summary') or '').strip()))
        out.append((iid, first_seen, tier, read_at is not None or bool(archived), prepared))
    return out


def _summary(rows: list[tuple], now: float) -> dict:
    candidates = ready = pending = 0
    for _, _, tier, consumed, prepared in rows:
        if tier is None:
            pending += 1
            continue
        if tier not in QUALIFIED_TIERS:
            continue
        candidates += 1
        if not consumed:
            ready += prepared

    shelf_met = ready >= TARGET
    buffer_met = candidates >= CANDIDATE_TARGET
    return {
        'target': TARGET,
        'candidate_target': CANDIDATE_TARGET,
        'window_days': WINDOW_DAYS,
        # Keep these established fields tied to approval; the UI can describe
        # 'available' as approved while the new fields explain full readiness.
        'available': candidates,
        'shortfall': max(0, TARGET - candidates),
        'ready_count': ready,
        'ready_target': TARGET,
        'ready_shortfall': max(0, TARGET - ready),
        'candidate_count': candidates,
        'candidate_shortfall': max(0, CANDIDATE_TARGET - candidates),
        'ready_met': shelf_met,
        'candidate_buffer_met': buffer_met,
        'candidate_met': buffer_met,
        'met': shelf_met and buffer_met,
        'pending_review': pending,
        'as_of': now,
    }


def snapshot(s: Session, pid: int, now: float | None = None) -> dict:
    """Count distinct quality-approved candidates and fully prepared articles."""
    now = time.time() if now is None else now
    return _summary(_scan(s, pid, now), now)


def news_curators(s: Session, pid: int) -> list[Curator]:
    """Enabled scheduled watch curators that can collect articles: the profile's interests."""
    rows = s.exec(select(Curator).where(Curator.profile_id == pid, Curator.kind == 'watch',
                                        Curator.enabled.is_(True), Curator.interval_hours > 0)
                  .order_by(Curator.id)).all()
    return [c for c in rows if not c.content_types or set(c.content_types).intersection(ARTICLE_TYPES)]


def shares(interest_count: int) -> tuple[int, int]:
    """Per-interest (unseen ready share, daily new-article share)."""
    # Shares sum to (at least) the profile targets, so meeting every share meets the profile.
    # Feed depth comes from each run's large budget (Curator.max_per_run), not from these
    # shares: they only decide which interests get extra hourly runs.
    n = max(1, interest_count)
    return max(5, math.ceil(UNSEEN_TARGET / n)), max(3, math.ceil(DAILY_FRESH_TARGET / n))


def _empty_interest() -> dict:
    return {'ready': 0, 'unseen_ready': 0, 'new_24h': 0, 'qualified': 0,
            'pending_review': 0, 'pending_capture': 0}


def interest_plan(s: Session, pid: int, now: float | None = None, cache: bool = False) -> dict:
    """One bounded scan per profile split per interest (distinct items per curator).

    ready: unread prepared articles (news_supply readiness); unseen_ready: ready and never
    shown on News for this profile; new_24h: quality-approved arrivals in the last 24 h;
    pending_review: awaiting the master curator; pending_capture: approved, unread, not yet
    prepared. Profile totals count each item once even when several curators found it.
    """
    now = time.time() if now is None else now
    if cache:
        with _cache_lock:
            hit = _cache.get(pid)
        if hit and time.monotonic() - hit[0] < CACHE_TTL and abs(now - hit[1]) < CACHE_TTL:
            return hit[2]
    rows = _scan(s, pid, now)
    ids = [r[0] for r in rows]
    seen: set[int] = set()
    by_item: dict[int, list[int]] = {}
    if ids:
        seen = set(s.exec(select(NewsImpression.item_id).where(NewsImpression.profile_id == pid,
                                                               NewsImpression.item_id.in_(ids))).all())
        for cid, iid in s.exec(select(CuratorDiscovery.curator_id, CuratorDiscovery.item_id)
                               .where(CuratorDiscovery.profile_id == pid,
                                      CuratorDiscovery.item_id.in_(ids))).all():
            by_item.setdefault(iid, []).append(cid)

    totals = _empty_interest()
    per: dict[int, dict] = {}
    for iid, first_seen, tier, consumed, prepared in rows:
        counts = {k: 0 for k in totals}
        if tier is None:
            counts['pending_review'] = 1
        elif tier in QUALIFIED_TIERS:
            counts['qualified'] = 1
            counts['new_24h'] = int(first_seen is not None and now - FRESH_SECONDS <= first_seen <= now)
            if not consumed:
                if prepared:
                    counts['ready'] = 1
                    counts['unseen_ready'] = int(iid not in seen)
                else:
                    counts['pending_capture'] = 1
        else:
            continue
        for key, value in counts.items():
            totals[key] += value
        for cid in set(by_item.get(iid, ())):
            entry = per.setdefault(cid, _empty_interest())
            for key, value in counts.items():
                entry[key] += value

    curators = news_curators(s, pid)
    unseen_share, daily_share = shares(len(curators))
    supply = _summary(rows, now)
    plan = {
        'as_of': now,
        'supply': supply,
        'totals': totals,
        'daily_target': DAILY_FRESH_TARGET,
        'unseen_target': UNSEEN_TARGET,
        'unseen_share': unseen_share,
        'daily_share': daily_share,
        'interest_ids': [c.id for c in curators],
        'interests': per,
    }
    # Full when the strict shelf, daily intake and unseen stock all hold, or when every
    # interest meets its share (shares sum to the targets; shared items may overlap).
    plan['profile_met'] = (
        (supply['met'] and totals['new_24h'] >= DAILY_FRESH_TARGET and totals['unseen_ready'] >= UNSEEN_TARGET)
        or (bool(curators) and all(classify(per.get(c.id) or _empty_interest(), plan)[0] != 'underserved'
                                   for c in curators)))
    if cache:
        with _cache_lock:
            _cache[pid] = (time.monotonic(), now, plan)
    return plan


def quiet_streak(s: Session, curator_id: int) -> int:
    """Consecutive latest completed scheduled runs that added no new article item."""
    runs = s.exec(select(CuratorRun.started_at, CuratorRun.finished_at)
                  .where(CuratorRun.curator_id == curator_id, CuratorRun.trigger == 'schedule',
                         CuratorRun.status.in_(('ok', 'partial')), CuratorRun.finished_at.is_not(None))
                  .order_by(CuratorRun.started_at.desc()).limit(QUIET_LOOKBACK)).all()
    if not runs:
        return 0
    found = s.exec(select(CuratorDiscovery.found_at)
                   .join(ContentItem, ContentItem.id == CuratorDiscovery.item_id)
                   .where(CuratorDiscovery.curator_id == curator_id,
                          CuratorDiscovery.found_at >= runs[-1][0],
                          ContentItem.type.in_(ARTICLE_TYPES))).all()
    streak = 0
    for started, finished in runs:
        if any(started <= at <= finished for at in found):
            break
        streak += 1
    return streak


def quiet_backoff(streak: int, interval_hours: int) -> int:
    """Seconds between boosted runs: hourly, doubling after QUIET_RUNS empty runs, capped at the interval."""
    if streak < QUIET_RUNS:
        return CATCH_UP
    cap = max(CATCH_UP, int(interval_hours or 0) * 3600)
    return min(cap, CATCH_UP * 2 ** (streak - QUIET_RUNS + 1))


def classify(entry: dict, plan: dict) -> tuple[str, str]:
    """Interest state (underserved | ok | saturated) and a short reason."""
    unseen_share, daily_share = plan['unseen_share'], plan['daily_share']
    # Large unseen stock wins over a trickle of new arrivals: a slow source with a deep
    # backlog must not be boosted forever.
    if entry['unseen_ready'] >= SATURATION * unseen_share:
        return 'saturated', 'Plenty of unseen articles'
    if entry['unseen_ready'] < unseen_share:
        return 'underserved', f'{entry["unseen_ready"]} unseen ready of {unseen_share}'
    if entry['new_24h'] < daily_share:
        return 'underserved', f'{entry["new_24h"]} new in 24 h of {daily_share}'
    return 'ok', 'Meets its share'


def interest_decision(s: Session, curator: Curator, now: float | None = None,
                      cache: bool = False, plan: dict | None = None) -> dict:
    """Should this curator get the boosted batch/cadence, and how far apart are its boosted runs?

    Boost when its own interest is underserved, even if the profile total is met; never for a
    saturated interest. Otherwise boost only while the profile as a whole is short. Quiet
    interests keep boosting but space runs out exponentially up to the configured interval.
    """
    plan = plan if plan is not None else interest_plan(s, curator.profile_id, now, cache=cache)
    entry = plan['interests'].get(curator.id) or _empty_interest()
    state, reason = classify(entry, plan)
    boost = state == 'underserved' or (state == 'ok' and not plan['profile_met'])
    if state == 'ok' and boost:
        reason = 'Profile supply is short'
    streak = quiet_streak(s, curator.id) if curator.id is not None else 0
    backoff = quiet_backoff(streak, curator.interval_hours)
    quiet = streak >= QUIET_RUNS
    return {'state': state, 'boost': boost, 'reason': reason, 'quiet_runs': streak, 'quiet': quiet,
            'backoff_seconds': backoff if boost else None,
            'quiet_reason': (f'{streak} recent runs found no new articles; next boosted run in '
                             f'{backoff // 3600} h' if quiet and boost else '')}
