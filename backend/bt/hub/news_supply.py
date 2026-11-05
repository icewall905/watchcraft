"""Profile-scoped progress toward a useful, curated news shelf.

This measures distinct recent news/article items that the master curator kept or
featured. It is deliberately read-only: it never creates sources, changes curator
settings, relaxes gates, or enlarges the daily edition.
"""
import time

from sqlalchemy import func, or_
from sqlmodel import Session, select

from .models import ContentItem, CuratorDiscovery, ItemState, ItemVerdict, Topic

TARGET = 100
WINDOW_DAYS = 7
WINDOW_SECONDS = WINDOW_DAYS * 86400
QUALIFIED_TIERS = ('keep', 'feature')


def snapshot(s: Session, pid: int, now: float | None = None) -> dict:
    """Return the profile's count of distinct, quality-approved recent articles."""
    now = time.time() if now is None else now
    start = now - WINDOW_SECONDS
    published = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
    muted = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news', Topic.muted.is_(True))).all()
    muted_terms = [str(term).lower() for topic in muted for term in (topic.keywords or [topic.name])]
    # Match /news: it considers the newest 1,200 visible items before applying muted
    # topic substring filters. Select only the fields needed for counting; SQLite's
    # lower() is ASCII-only, so do the final case-fold-compatible match in Python.
    visible_ids = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)
    rows = s.exec(select(ContentItem.id, ContentItem.title, ContentItem.summary, ItemVerdict.tier)
                  .outerjoin(ItemState, (ItemState.item_id == ContentItem.id) & (ItemState.profile_id == pid))
                  .outerjoin(ItemVerdict, (ItemVerdict.item_id == ContentItem.id) & (ItemVerdict.profile_id == pid))
                  .where(ContentItem.id.in_(visible_ids), ContentItem.type.in_(('news', 'article')),
                         published >= start,
                         or_(ItemState.dismissed.is_(None), ItemState.dismissed.is_(False)),
                         or_(ItemVerdict.tier.is_(None), ItemVerdict.tier.not_in(('set_aside', 'reject'))))
                  .order_by(published.desc(), ContentItem.id.desc()).limit(1200)).all()
    def is_muted(title, summary):
        hay = f'{title or ""} {summary or ""}'.lower()
        return any(term in hay for term in muted_terms)

    available = sum(tier in QUALIFIED_TIERS and not is_muted(title, summary)
                    for _, title, summary, tier in rows)
    pending = sum(tier is None and not is_muted(title, summary)
                  for _, title, summary, tier in rows)
    return {
        'target': TARGET,
        'window_days': WINDOW_DAYS,
        'available': available,
        'shortfall': max(0, TARGET - available),
        'met': available >= TARGET,
        'pending_review': pending,
        'as_of': now,
    }
