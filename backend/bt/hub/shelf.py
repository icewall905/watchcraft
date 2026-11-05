"""Profile-scoped inventory for the ready article shelf and watch interests."""
import time

from sqlalchemy import func, or_
from sqlmodel import Session, select

from ..access import hub_allowed
from .article_cleanup import VERSION as CLEANUP_VERSION
from .models import (ArticleText, CaptureJob, ContentItem, Curator, CuratorDiscovery,
                     ItemState, ItemVerdict, StoryItem, Topic)
from .news_supply import CANDIDATE_TARGET, TARGET, snapshot as news_snapshot

WINDOW_SECONDS = 7 * 86400
FRESH_SECONDS = 86400
ARTICLE_TYPES = ('news', 'article')


def _empty(now: float, reason: str) -> dict:
    return {
        'as_of': now,
        'articles': {'ready': 0, 'target': TARGET, 'fresh_24h': 0, 'publishers': 0,
                     'stories': 0, 'oldest_ready_at': None, 'needs_refill': True,
                     'interests': [], 'reason': reason},
    }


def snapshot(s: Session, pid: int, now: float | None = None) -> dict:
    """Summarize current, locally readable article inventory without loading article bodies."""
    now = time.time() if now is None else now
    if not hub_allowed(s, pid):
        return _empty(now, 'Hub access is unavailable for this profile.')

    # Match news_supply's approved, recent candidate universe and archive gates.
    # The bounded 1200-row scan uses SQL booleans for body presence so large
    # article text never crosses into Python just to calculate inventory.
    start = now - WINDOW_SECONDS
    published = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
    muted = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news',
                            Topic.muted.is_(True))).all()
    muted_terms = [str(term).lower() for topic in muted
                   for term in (topic.keywords or [topic.name])]
    visible = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)
    rows = s.exec(
        select(ContentItem.id, ContentItem.title, ContentItem.summary, ContentItem.publisher,
               ContentItem.first_seen, published,
               (func.length(func.trim(ContentItem.body, " \t\r\n\v\f\u00a0")) > 0),
               ContentItem.enrichment, ItemVerdict.tier, ItemState.read_at, ItemState.archived,
               ItemState.dismissed,
               (func.length(func.trim(ArticleText.raw_body, " \t\r\n\v\f\u00a0")) > 0),
               ArticleText.status, ArticleText.version, CaptureJob.status,
               CaptureJob.summary_status, StoryItem.story_id)
        .outerjoin(ItemState, (ItemState.item_id == ContentItem.id) & (ItemState.profile_id == pid))
        .outerjoin(ItemVerdict, (ItemVerdict.item_id == ContentItem.id) & (ItemVerdict.profile_id == pid))
        .outerjoin(ArticleText, ArticleText.item_id == ContentItem.id)
        .outerjoin(CaptureJob, (CaptureJob.item_id == ContentItem.id) & (CaptureJob.profile_id == pid))
        .outerjoin(StoryItem, StoryItem.item_id == ContentItem.id)
        .where(ContentItem.id.in_(visible), ContentItem.type.in_(ARTICLE_TYPES), published >= start,
               or_(ItemState.dismissed.is_(None), ItemState.dismissed.is_(False)),
               or_(ItemVerdict.tier.is_(None), ItemVerdict.tier.not_in(('set_aside', 'reject'))))
        .order_by(published.desc(), ContentItem.id.desc()).limit(1200)
    ).all()

    def muted_item(title, summary):
        text = f'{title or ""} {summary or ""}'.lower()
        return any(term in text for term in muted_terms)

    ready_rows = {}
    for row in rows:
        (iid, title, summary, publisher, first_seen, published_at, body, enrichment,
         tier, read_at, archived, dismissed, raw_body, archive_status, archive_version,
         job_status, summary_status, story_id) = row
        if muted_item(title, summary) or tier not in ('keep', 'feature') or read_at is not None or archived or dismissed:
            continue
        enrichment = enrichment if isinstance(enrichment, dict) else {}
        prepared = (bool(body) and bool(raw_body) and archive_status == 'reviewed'
                    and archive_version == CLEANUP_VERSION
                    and job_status in ('complete', 'partial') and summary_status == 'ready'
                    and bool(str(enrichment.get('summary') or '').strip()))
        if prepared:
            ready_rows[iid] = {
                'published_at': published_at,
                'publisher': (publisher or '').strip(),
                # Unclustered articles count as their own stable story.
                'story_id': story_id if story_id is not None else f'item:{iid}',
                'first_seen': first_seen,
            }

    ready_ids = list(ready_rows)
    discovery_rows = (s.exec(select(CuratorDiscovery.curator_id, CuratorDiscovery.item_id)
                             .where(CuratorDiscovery.profile_id == pid,
                                    CuratorDiscovery.item_id.in_(ready_ids or [-1]))).all()
                      if ready_ids else [])
    by_curator: dict[int, set[int]] = {}
    for curator_id, item_id in discovery_rows:
        by_curator.setdefault(curator_id, set()).add(item_id)

    curators = s.exec(select(Curator).where(Curator.profile_id == pid, Curator.kind == 'watch',
                                             Curator.enabled.is_(True), Curator.interval_hours > 0)
                       .order_by(Curator.id)).all()
    curators = [c for c in curators if not c.content_types or set(c.content_types).intersection(ARTICLE_TYPES)]
    per_interest_target = min(20, max(5, TARGET // max(1, len(curators))))
    interests = []
    interest_deficit = False
    for curator in curators:
        item_ids = by_curator.get(curator.id, set())
        # A story discovered from several URLs only occupies one slot for this interest.
        story_rows: dict[object, dict] = {}
        for iid in item_ids:
            article = ready_rows.get(iid)
            if article is None:
                continue
            sid = article['story_id']
            current = story_rows.get(sid)
            if current is None or article['published_at'] > current['published_at']:
                story_rows[sid] = article
        fresh_story_ids = {sid for sid, item in story_rows.items()
                           if now - FRESH_SECONDS <= item['published_at'] <= now}
        publishers = {item['publisher'] for item in story_rows.values() if item['publisher']}
        count = len(story_rows)
        minimum_fresh = min(3, per_interest_target)
        minimum_publishers = min(3, count)
        needs_refill = (count < per_interest_target or len(fresh_story_ids) < minimum_fresh
                        or (count >= 5 and len(publishers) < minimum_publishers))
        interest_deficit |= needs_refill
        interests.append({
            'id': curator.id,
            'name': curator.name,
            'ready': count,
            'target': per_interest_target,
            'fresh_24h': len(fresh_story_ids),
            'publishers': len(publishers),
            'needs_refill': needs_refill,
            'last_success': curator.last_success,
            'next_run': curator.next_run,
        })

    # ready_count intentionally comes from the established supply counter, so
    # the headline number stays identical to the News page's strict readiness.
    supply = news_snapshot(s, pid, now)
    overall_ready = int(supply.get('ready_count', 0))
    story_ids = {entry['story_id'] for entry in ready_rows.values()}
    fresh_items = [entry for entry in ready_rows.values()
                   if now - FRESH_SECONDS <= entry['published_at'] <= now]
    fresh_stories = {entry['story_id'] for entry in fresh_items}
    publishers = {entry['publisher'] for entry in ready_rows.values() if entry['publisher']}
    oldest = min((entry['published_at'] for entry in ready_rows.values()), default=None)
    needs_refill = not supply.get('met', False) or interest_deficit
    if overall_ready < TARGET:
        reason = f'{TARGET - overall_ready} more ready articles needed.'
    elif not supply.get('candidate_buffer_met', supply.get('met', False)):
        reason = f'{max(0, CANDIDATE_TARGET - int(supply.get("candidate_count", 0)))} more approved candidates needed.'
    elif interest_deficit:
        reason = 'One or more interests need fresher or more varied articles.'
    else:
        reason = 'The ready shelf and interest targets are stocked.'
    return {
        'as_of': now,
        'articles': {
            'ready': overall_ready,
            'target': TARGET,
            'fresh_24h': len(fresh_stories),
            'publishers': len(publishers),
            'stories': len(story_ids),
            'oldest_ready_at': oldest,
            'needs_refill': needs_refill,
            'interests': interests,
            'reason': reason,
        },
    }
