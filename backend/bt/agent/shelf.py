"""Local, playable inventory for the profile's enabled video shelves.

This is a bounded read-only snapshot. It never contacts video providers, and it
does not treat accepted items waiting in the download/subtitle queues as fresh
collection demand.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import stat
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, col, select

from .. import subtitle_jobs
from ..downloader.service import abs_path
from ..models import Candidate, Category, Download, GeneratedSubtitles, Video, WatchHistory
from .data import accepted_unwatched, accepted_variety, category_refill_deficit
from .runtime import aware

SHELF_SCAN_LIMIT = 2000
FRESH_DAYS = 7


def _instant(value: Optional[datetime | float]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return aware(value)


def _local_file(relative: str) -> bool:
    path = abs_path(relative) if relative else None
    try:
        if not path:
            return False
        metadata = path.stat()
        return stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0 and os.access(path, os.R_OK)
    except OSError:
        return False


def snapshot(s: Session, pid: int, now: Optional[datetime | float] = None) -> dict:
    """Return enabled per-profile video shelf readiness and refill signals.

    ``ready`` requires an accepted, non-dismissed, unfinished item whose saved
    file exists under MEDIA_DIR. Shorts additionally need a valid generated VTT
    track because the Reels player must be usable with sound off. ``accepted``
    counts eligible inventory separately, so pending local work does not trigger
    redundant collection.
    """
    instant = _instant(now) or datetime.now(timezone.utc)
    fresh_cutoff = instant - timedelta(days=FRESH_DAYS)
    categories = list(s.exec(select(Category).where(
        Category.profile_id == pid, Category.enabled == True).order_by(  # noqa: E712
            Category.position, Category.id)).all())
    watched = set(s.exec(select(WatchHistory.video_id).where(
        WatchHistory.profile_id == pid, WatchHistory.finished == True)).all())  # noqa: E712

    rows_by_category: dict[int, list] = {}
    file_cache: dict[str, bool] = {}
    for category in categories:
        query = (select(Candidate.video_id, Candidate.created_at, Video.published_at,
                        Video.channel_id, Video.channel_name, Download.status, Download.path,
                        Download.attempts)
            .join(Video, Video.id == Candidate.video_id)
            .outerjoin(Download, Download.video_id == Candidate.video_id)
            .where(Candidate.category_id == category.id,
                   Candidate.status == "accepted", Candidate.dismissed == False))  # noqa: E712
        if category.kind == "shorts":
            query = query.add_columns(GeneratedSubtitles.status, GeneratedSubtitles.vtt)\
                .outerjoin(GeneratedSubtitles, GeneratedSubtitles.video_id == Candidate.video_id)
        rows = s.exec(query.order_by(col(Candidate.score).desc().nullslast(),
                                     col(Candidate.judged_at).desc().nullslast(), Candidate.id)
                      .limit(SHELF_SCAN_LIMIT)).all()
        rows_by_category[category.id] = rows

    details = []
    total_ready_ids: set[str] = set()
    total_fresh_ids: set[str] = set()
    total_creators: set[str] = set()
    total_first_seen_ids: set[str] = set()
    total_published_fresh_ids: set[str] = set()
    for category in categories:
        ready_ids: set[str] = set()
        fresh_ids: set[str] = set()
        creator_ids: set[str] = set()
        first_seen_ids: set[str] = set()
        published_fresh_ids: set[str] = set()
        pending_ids: set[str] = set()
        failed_ids: set[str] = set()
        candidate_statuses = s.exec(select(Candidate.video_id, Candidate.status)
            .where(Candidate.category_id == category.id,
                   Candidate.dismissed == False,  # noqa: E712
                   col(Candidate.status).in_(("found", "enriched")))
            .limit(SHELF_SCAN_LIMIT)).all()
        pending_ids.update(vid for vid, _ in candidate_statuses if vid not in watched)
        accepted_count = accepted_unwatched(s, category, instant)
        age_cutoff = instant - timedelta(days=category.max_age_days) if category.max_age_days else None
        for row in rows_by_category[category.id]:
            vid, created_at, published_at, channel_id, channel_name, download_status, download_path, attempts = row[:8]
            subtitle_status, subtitle_vtt = row[8:10] if category.kind == "shorts" else (None, None)
            if vid in watched:
                continue
            if age_cutoff and published_at is not None:
                published_for_age = _instant(published_at)
                if published_for_age is not None and published_for_age < age_cutoff:
                    continue
            published = _instant(published_at)
            discovered = _instant(created_at)

            local = False
            if download_status == "done":
                key = download_path or ""
                if key not in file_cache:
                    file_cache[key] = _local_file(key)
                local = file_cache[key]
            muted_ready = True
            if category.kind == "shorts":
                muted_ready = bool(subtitle_status == "ready" and subtitle_jobs.usable_vtt(subtitle_vtt))
            if local and muted_ready:
                ready_ids.add(vid)
                channel = (channel_id or "").strip() or (channel_name or "").strip()
                if channel:
                    creator_ids.add(channel.casefold())
                if published is not None:
                    if published >= fresh_cutoff:
                        fresh_ids.add(vid)
                        published_fresh_ids.add(vid)
                elif discovered is not None and discovered >= fresh_cutoff:
                    fresh_ids.add(vid)
                    first_seen_ids.add(vid)
            elif ((download_status == "evicted") or
                  (download_status == "error" and (attempts or 0) >= 3) or
                  (download_status == "done" and not local) or
                  (category.kind == "shorts" and (
                      subtitle_status == "failed" or
                      (subtitle_status == "ready" and not subtitle_jobs.usable_vtt(subtitle_vtt))))):
                failed_ids.add(vid)
            else:
                pending_ids.add(vid)

        target = max(0, int(category.target_queue or 0))
        eligible_fresh, eligible_creators = accepted_variety(s, category, instant)
        status_counts = s.exec(select(Candidate.status, func.count())
            .where(Candidate.category_id == category.id, Candidate.dismissed == False)  # noqa: E712
            .group_by(Candidate.status)).all()
        backlog = sum(count for status, count in status_counts if status in ("found", "enriched"))
        refill_deficit = category_refill_deficit(s, category, instant, backlog)
        details.append({
            "id": category.id, "name": category.name, "emoji": category.emoji,
            "kind": category.kind, "ready": len(ready_ids), "target": target,
            "fresh_7d": len(fresh_ids), "fresh_7d_published": len(published_fresh_ids),
            "fresh_7d_first_seen": len(first_seen_ids), "creators": len(creator_ids),
            "pending": len(pending_ids), "failed": len(failed_ids), "accepted": accepted_count,
            "eligible_fresh_7d": eligible_fresh, "eligible_creators": eligible_creators,
            "needs_refill": refill_deficit > 0,
        })
        total_ready_ids.update(ready_ids)
        total_fresh_ids.update(fresh_ids)
        total_creators.update(creator_ids)
        total_first_seen_ids.update(first_seen_ids)
        total_published_fresh_ids.update(published_fresh_ids)

    total_target = sum(max(0, int(category.target_queue or 0)) for category in categories)
    total_accepted = sum(accepted_unwatched(s, category, instant) for category in categories)
    total_pending = sum(row["pending"] for row in details)
    total_failed = sum(row["failed"] for row in details)
    needs_refill = any(row["needs_refill"] for row in details)
    reason = ("Some saved videos or captions need attention."
              if total_failed else "Accepted videos are awaiting local files or captions."
              if total_pending else "Freshness, volume or creator variety needs a top-up."
              if needs_refill else "Enabled shelves are ready." if categories else "No video categories are enabled.")
    return {
        "ready": len(total_ready_ids), "target": total_target,
        "fresh_7d": len(total_fresh_ids),
        "fresh_7d_published": len(total_published_fresh_ids),
        "fresh_7d_first_seen": len(total_first_seen_ids),
        "creators": len(total_creators), "pending": total_pending,
        "failed": total_failed, "accepted": total_accepted, "needs_refill": needs_refill,
        "categories": details, "reason": reason,
    }
