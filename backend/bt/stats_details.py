"""Paged drill-downs for the all-profile Statistics page.

Rows represent the records counted by the statistic: a video can appear once per
category, profile watch, or encoding attempt. Keep these predicates aligned with
api.core.stats_page rather than silently deduplicating the videos.
"""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import exists
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, func, select

from .models import (Candidate, Category, Download, Feedback, Lesson, Profile,
                     ReencodeJob, ReencodeResult, Video, WatchHistory, YtRequest, now)


def explore(s: Session, metric: str, category_id: int | None = None, day: date | None = None,
            offset: int = 0, limit: int = 40, height: int | None = None) -> dict:
    decision_only = metric.startswith("decision_")
    if decision_only:
        metric = metric.removeprefix("decision_")
    candidate_metrics = {"ready", "review", "accepted", "scored", "found", "rejected_ai", "rejected_family", "dismissed", "error"}
    download_metrics = {"offline", "waiting_download", "encoded_copies", "downloads"}
    job_metrics = {"encode_queue", "encode_kept", "encode_errors"}
    result_metrics = {"encode_saved", "encode_not_smaller", "encode_failed"}
    category = s.get(Category, category_id) if category_id is not None else None
    if category_id is not None and not category:
        raise HTTPException(404, "playlist not found")

    if metric == "offline_channels":
        if category_id is not None or day is not None or height is not None:
            raise HTTPException(400, "channel totals describe the current offline library")
        channel_key = func.coalesce(func.nullif(Video.channel_id, ""), Video.channel_name)
        query = select(channel_key, func.max(Video.channel_id), func.max(Video.channel_name), func.count(),
                       func.sum(Video.duration_s)).join(Download, Download.video_id == Video.id).where(
                           Download.status == "done", channel_key != "").group_by(channel_key)
        total = s.exec(select(func.count()).select_from(query.subquery())).one()
        rows = s.exec(query.order_by(func.count().desc(), channel_key).offset(offset).limit(limit)).all()
        return {"total": total, "offset": offset, "limit": limit, "items": [
            {"key": key, "video_id": None, "channel_id": cid or None, "title": name or cid,
             "channel_name": "", "duration_s": 0, "offline": False, "candidate_id": None,
             "category_id": None, "category_name": "", "profile_id": None, "profile_name": "",
             "detail": f"{n} saved videos · {(duration or 0) / 3600:.1f} hours offline",
             "score": None, "rating": None, "size_bytes": 0, "height": 0, "original_bytes": 0, "output_bytes": 0}
            for key, cid, name, n, duration in rows]}

    if metric in candidate_metrics:
        model = Candidate
        query = select(Candidate, Video).join(Category, Category.id == Candidate.category_id).join(
            Video, Video.id == Candidate.video_id, isouter=True)
        family = exists(select(Feedback.id).where(Feedback.video_id == Candidate.video_id,
                                                  Feedback.category_id == Candidate.category_id, Feedback.content_type == "video", Feedback.rating < 0))
        watched = exists(select(WatchHistory.id).where(WatchHistory.video_id == Candidate.video_id,
                         WatchHistory.profile_id == Category.profile_id, WatchHistory.finished == True))  # noqa: E712
        if metric == "dismissed":
            query = query.where(Candidate.dismissed == True)  # noqa: E712
        elif metric in ("rejected_ai", "rejected_family"):
            query = query.where(Candidate.status == "rejected", family if metric == "rejected_family" else ~family)
        else:
            if metric != "scored" and not (metric == "accepted" and day):
                query = query.where(Candidate.dismissed == False)  # noqa: E712
            if metric == "ready":
                query = query.where(Candidate.status == "accepted", ~watched)
            elif metric == "review":
                query = query.where(col(Candidate.status).in_(["found", "enriched"]))
            elif metric in ("accepted", "scored"):
                query = query.where(Candidate.status == "accepted")
            elif metric == "error":
                query = query.where(Candidate.status == "error")
        if decision_only:
            query = query.where(Candidate.dismissed == False)  # noqa: E712
        if category_id is not None:
            query = query.where(Candidate.category_id == category_id)
        if metric == "found":
            # Daily "found" counts every discovery, whatever its current verdict.
            query = select(Candidate, Video).join(Category, Category.id == Candidate.category_id).join(
                Video, Video.id == Candidate.video_id, isouter=True)
            if category_id is not None:
                query = query.where(Candidate.category_id == category_id)
        timestamp = Candidate.created_at if metric in ("found", "review", "dismissed") else Candidate.judged_at
        order = col(Candidate.id).desc()
    elif metric in download_metrics:
        model = Download
        query = select(Download, Video).join(Video, Video.id == Download.video_id, isouter=True)
        if metric == "downloads":
            query = query.where(Download.finished_at != None)  # noqa: E711
        else:
            query = query.where(col(Download.status).in_(["queued", "downloading"]) if metric == "waiting_download"
                                else Download.status == "done")
        if metric == "encoded_copies":
            query = query.where(Download.vcodec == "h264 (NVENC)")
        timestamp, order = Download.finished_at, col(Download.video_id).asc()
    elif metric in ("watched", "feedback"):
        model = WatchHistory if metric == "watched" else Feedback
        query = select(model, Video).join(Video, Video.id == model.video_id, isouter=True)
        if metric == "feedback":
            query = query.where(Feedback.content_type == "video")
        if metric == "watched":
            query = query.where(WatchHistory.finished == True)  # noqa: E712
        if category_id is not None:
            query = query.where(model.category_id == category_id)
        timestamp = WatchHistory.updated_at if metric == "watched" else Feedback.created_at
        order = col(model.id).desc()
    elif metric in job_metrics | result_metrics:
        model = ReencodeJob if metric in job_metrics else ReencodeResult
        query = select(model, Video).join(Video, Video.id == model.video_id, isouter=True)
        if metric == "encode_queue":
            query = query.where(ReencodeJob.status == "queued")
        elif metric in ("encode_kept", "encode_errors"):
            same_size = ReencodeJob.error == "Output was not smaller; original kept"
            query = query.where(ReencodeJob.status == "error", same_size if metric == "encode_kept" else ~same_size)
        else:
            outcome = {"encode_saved": "saved", "encode_not_smaller": "not_smaller", "encode_failed": "failed"}[metric]
            query = query.where(ReencodeResult.outcome == outcome)
        timestamp, order = model.created_at, col(model.video_id).asc() if metric in job_metrics else col(model.id).desc()
    elif metric == "videos":
        model = Video
        query = select(Video)
        timestamp, order = Video.fetched_at, col(Video.id).asc()
    elif metric in ("lessons", "requests"):
        model = Lesson if metric == "lessons" else YtRequest
        query = select(model)
        if metric == "lessons":
            query = query.where(Lesson.active == True, Lesson.content_type == "video")  # noqa: E712
        else:
            day = day or now().date()
        timestamp, order = (Lesson.created_at if metric == "lessons" else YtRequest.ts), col(model.id).desc()
    else:
        raise HTTPException(400, "unknown statistic")

    if category_id is not None and metric not in candidate_metrics | {"watched", "feedback"}:
        raise HTTPException(400, "this statistic cannot be filtered by category")
    if height is not None:
        if metric != "offline":
            raise HTTPException(400, "only offline copies can be filtered by quality")
        query = query.where(Download.height == height)
    if day:
        query = query.where(func.date(timestamp) == day.isoformat())
    total = s.exec(select(func.count()).select_from(query.subquery())).one()
    if metric not in ("lessons", "requests"):
        query = query.options(load_only(Video.id, Video.title, Video.channel_name, Video.duration_s))
    rows = s.exec(query.order_by(col(timestamp).desc(), order).offset(offset).limit(limit)).all()
    cats = {c.id: c for c in s.exec(select(Category)).all()}
    profiles = {p.id: p.name for p in s.exec(select(Profile)).all()}
    # Offline flags for the page in one query, with no YouTube requests.
    ids = [r.id if metric == "videos" else r[0].video_id for r in rows] if metric not in ("lessons", "requests") else []
    offline = set(s.exec(select(Download.video_id).where(col(Download.video_id).in_(ids), Download.status == "done")).all()) if ids else set()
    items = []
    for entry in rows:
        if metric in ("lessons", "requests"):
            record, video = entry, None
        elif metric == "videos":
            record, video = entry, entry
        else:
            record, video = entry
        vid = video.id if video else getattr(record, "video_id", None)
        cid = getattr(record, "category_id", None)
        cat = cats.get(cid)
        pid = getattr(record, "profile_id", None) or (cat.profile_id if cat else None)
        detail = getattr(record, "error", "") or ""
        if isinstance(record, Candidate):
            detail = record.summary or "; ".join(record.red_flags) or detail
        if isinstance(record, Feedback):
            detail = record.text or ", ".join(record.chips)
        if isinstance(record, ReencodeResult):
            detail = {"saved": "Smaller copy saved", "not_smaller": "Original kept: output was not smaller",
                      "failed": "Encoding failed; original kept"}.get(record.outcome, record.outcome)
        items.append({
            "key": str(getattr(record, "id", None) or vid), "video_id": vid, "channel_id": None,
            "title": (video.title if video else "") or (record.text if isinstance(record, Lesson) else
                     f"YouTube · {record.kind}" if isinstance(record, YtRequest) else vid or "Unknown video"),
            "channel_name": video.channel_name if video else "", "duration_s": video.duration_s if video else 0,
            "offline": vid in offline, "candidate_id": record.id if isinstance(record, Candidate) else None,
            "category_id": cid, "category_name": cat.name if cat else "", "profile_id": pid,
            "profile_name": profiles.get(pid, ""), "detail": detail or (record.detail if isinstance(record, YtRequest) else ""),
            "score": record.score if isinstance(record, Candidate) else None,
            "size_bytes": getattr(record, "size_bytes", 0), "height": getattr(record, "height", 0),
            "rating": record.rating if isinstance(record, Feedback) else None,
            "original_bytes": getattr(record, "original_bytes", 0), "output_bytes": getattr(record, "output_bytes", 0),
        })
    return {"items": items, "total": total, "offset": offset, "limit": limit}
