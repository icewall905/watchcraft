"""Profile-scoped progress toward a stocked, curated news shelf.

The readiness goal is deliberately stricter than a master-curator verdict: an
article must also have a locally archived, worker-reviewed body and an AI summary.
The larger candidate buffer keeps collectors running until there is enough judged
material to refill that ready shelf as items are read and age out.
"""
import time

from sqlalchemy import func, or_
from sqlmodel import Session, select

from .article_cleanup import VERSION as CLEANUP_VERSION
from .models import (ArticleText, CaptureJob, ContentItem, CuratorDiscovery, ItemState,
                     ItemVerdict, Topic)

TARGET = 100
CANDIDATE_TARGET = 300
WINDOW_DAYS = 7
WINDOW_SECONDS = WINDOW_DAYS * 86400
QUALIFIED_TIERS = ('keep', 'feature')


def snapshot(s: Session, pid: int, now: float | None = None) -> dict:
    """Count distinct quality-approved candidates and fully prepared articles."""
    now = time.time() if now is None else now
    start = now - WINDOW_SECONDS
    published = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
    muted = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news', Topic.muted.is_(True))).all()
    muted_terms = [str(term).lower() for topic in muted for term in (topic.keywords or [topic.name])]
    # Keep the same bounded recent universe as /news. Discovery is an item-id
    # subquery so multiple scouts/curators never inflate the supply counts.
    visible_ids = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)
    rows = s.exec(select(ContentItem.id, ContentItem.title, ContentItem.summary, ItemVerdict.tier,
                         ItemState.read_at, ItemState.archived,
                         (func.length(func.trim(ContentItem.body, " \t\r\n\v\f\u00a0")) > 0), ContentItem.enrichment,
                         (func.length(func.trim(ArticleText.raw_body, " \t\r\n\v\f\u00a0")) > 0),
                         ArticleText.status, ArticleText.version,
                         CaptureJob.status, CaptureJob.summary_status)
                  .outerjoin(ItemState, (ItemState.item_id == ContentItem.id) & (ItemState.profile_id == pid))
                  .outerjoin(ItemVerdict, (ItemVerdict.item_id == ContentItem.id) & (ItemVerdict.profile_id == pid))
                  .outerjoin(ArticleText, ArticleText.item_id == ContentItem.id)
                  .outerjoin(CaptureJob, (CaptureJob.item_id == ContentItem.id) & (CaptureJob.profile_id == pid))
                  .where(ContentItem.id.in_(visible_ids), ContentItem.type.in_(('news', 'article')),
                         published >= start,
                         or_(ItemState.dismissed.is_(None), ItemState.dismissed.is_(False)),
                         or_(ItemVerdict.tier.is_(None), ItemVerdict.tier.not_in(('set_aside', 'reject'))))
                  .order_by(published.desc(), ContentItem.id.desc()).limit(1200)).all()

    def is_muted(title, summary):
        hay = f'{title or ""} {summary or ""}'.lower()
        return any(term in hay for term in muted_terms)

    candidates = ready = pending = 0
    for _, title, summary, tier, read_at, archived, body, enrichment, raw_body, archive_status, archive_version, job_status, summary_status in rows:
        if is_muted(title, summary):
            continue
        if tier is None:
            pending += 1
            continue
        if tier not in QUALIFIED_TIERS:
            continue
        candidates += 1
        if read_at is not None or archived:
            continue
        enrichment = enrichment if isinstance(enrichment, dict) else {}
        prepared = (bool(body) and bool(raw_body) and archive_status == 'reviewed'
                    and archive_version == CLEANUP_VERSION and job_status in ('complete', 'partial')
                    and summary_status == 'ready' and bool(str(enrichment.get('summary') or '').strip()))
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
