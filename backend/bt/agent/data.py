"""DB queries shared by the pipeline steps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional
from urllib.parse import unquote, urlsplit

from sqlalchemy import or_
from sqlmodel import Session, col, func, select

from ..models import (Candidate, Category, Channel, Download, Feedback, GeneratedSubtitles, Lesson,
                      ProfileChannel, Video, WatchHistory, now)
from ..downloader.service import abs_path
from .. import subtitle_jobs
from .runtime import aware, parse_ts

BLOCK_TRUST = -0.8
FOUND_CAP = 12  # max `found` backlog per category
LOW_WATER = 4  # discover more when pending (found+enriched) drops below this


def lessons_for(s: Session, cat: Category, channel_id: Optional[str] = None,
                content_type: str = "video") -> list[Lesson]:
    q = select(Lesson).where(Lesson.profile_id == cat.profile_id, Lesson.content_type == content_type,
                             Lesson.active == True,  # noqa: E712
                             or_(col(Lesson.category_id).is_(None), Lesson.category_id == cat.id))
    out = []
    for le in s.exec(q.order_by(col(Lesson.weight).desc(), Lesson.id)).all():
        if le.channel_id and le.channel_id != channel_id and channel_id is not None:
            continue
        out.append(le)
    return out


def trust_for(s: Session, profile_id: int, channel_id: str) -> Optional[float]:
    pc = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == profile_id,
                                             ProfileChannel.channel_id == channel_id)).first()
    return pc.trust if pc else None


@dataclass
class ProfileCtx:
    blocked: set = field(default_factory=set)
    watched: set = field(default_factory=set)
    disliked: set = field(default_factory=set)


def profile_ctx(s: Session, profile_id: int) -> ProfileCtx:
    pcs = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == profile_id)).all()
    return ProfileCtx(
        blocked={p.channel_id for p in pcs if p.blocked or (p.trust or 0) <= BLOCK_TRUST},
        watched=set(s.exec(select(WatchHistory.video_id).where(WatchHistory.profile_id == profile_id,
                                                               WatchHistory.finished == True)).all()),  # noqa: E712
        disliked=set(s.exec(select(Feedback.video_id).where(Feedback.profile_id == profile_id,
                                                            Feedback.content_type == "video",
                                                            Feedback.rating < 0)).all()),
    )


def subscribed_channels(s: Session, profile_id: int) -> list[ProfileChannel]:
    return list(s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == profile_id,
                                                    ProfileChannel.subscribed == True,  # noqa: E712
                                                    ProfileChannel.blocked == False,
                                                    ProfileChannel.trust > BLOCK_TRUST)).all())  # noqa: E712


def subscription_context(s: Session, profile_id: int) -> list[dict]:
    """Bounded, profile-owned suggestions; a subscription alone never establishes topic fit."""
    ids = [pc.channel_id for pc in subscribed_channels(s, profile_id)][:300]
    if not ids:
        return []
    channels = {c.id: c for c in s.exec(select(Channel).where(Channel.id.in_(ids))).all()}
    ranked = select(Video.channel_id, Video.title,
                    func.row_number().over(partition_by=Video.channel_id,
                                           order_by=col(Video.fetched_at).desc()).label("rank"))\
        .where(Video.channel_id.in_(ids)).subquery()
    examples = {}
    for channel_id, title in s.exec(select(ranked.c.channel_id, ranked.c.title).where(ranked.c.rank <= 3)).all():
        examples.setdefault(channel_id, []).append(title[:140])
    return [{"id": cid, "name": channels[cid].name[:100] if cid in channels else "",
             "handle": channels[cid].handle[:100] if cid in channels else "",
             "recent_titles": examples.get(cid, [])} for cid in ids[:300]]


def channel_alternatives(s: Session, unavailable: list[dict]) -> list[dict]:
    """Offer observed exact-name matches as alternatives, never silently infer an alias."""
    labels = set()
    for row in unavailable:
        path = unquote(urlsplit(row["reference"]).path).split("/")
        if len(path) > 1 and path[1].startswith("@"):
            labels.add(path[1][1:].casefold())
    if not labels:
        return []
    rows = s.exec(select(Channel).where(or_(func.lower(Channel.name).in_(labels),
                                           func.lower(Channel.handle).in_(["@" + name for name in labels])))
                  .order_by(col(Channel.updated_at).desc()).limit(12)).all()
    return [{"id": c.id, "name": c.name[:100], "handle": c.handle[:100]} for c in rows]


def title_key(title: str) -> str:
    """Re-uploads keep the title; short generic titles ("Bengal cat") are not keyed."""
    t = " ".join((title or "").casefold().split())
    return t if len(t) >= 25 else ""


def rejected_titles(s: Session, cat_id: int) -> set[str]:
    rows = s.exec(select(Video.title).join(Candidate, Candidate.video_id == Video.id)
                  .where(Candidate.category_id == cat_id, Candidate.status == "rejected",
                         Candidate.dismissed == False)).all()  # noqa: E712
    return {k for k in map(title_key, rows) if k}


def prefilter(item: dict, cat: Category, ctx: ProfileCtx, hide_shorts: bool,
              existing: Optional[set] = None, rejected: Optional[set] = None) -> Optional[str]:
    """Return a rejection reason, or None if the entry may proceed. `item` = entry dict or Video.model_dump()."""
    vid = item.get("id")
    if not vid:
        return "no id"
    if existing is not None and vid in existing:
        return "already a candidate"
    if rejected and title_key(item.get("title")) in rejected:
        return "same title rejected before"
    if item.get("is_live"):
        return "live stream"
    if cat.kind == "shorts":
        # Discovery may have only an id/title; enrichment applies this again with metadata.
        if not item.get("is_short") and int(item.get("duration_s") or 0) > 0:
            return "not a short"
    elif hide_shorts and item.get("is_short"):
        return "short"
    dur = int(item.get("duration_s") or 0)
    if dur:
        if cat.min_duration_s and dur < cat.min_duration_s:
            return f"too short ({dur}s)"
        if cat.max_duration_s and dur > cat.max_duration_s:
            return f"too long ({dur}s)"
    title = (item.get("title") or "").lower()
    for kw in cat.exclude_keywords or []:
        if kw and kw.lower() in title:
            return f"excluded keyword '{kw}'"
    if item.get("channel_id") and item["channel_id"] in ctx.blocked:
        return "blocked/distrusted channel"
    if vid in ctx.disliked:
        return "disliked before"
    if vid in ctx.watched:
        return "already watched"
    if cat.max_age_days:
        pub = parse_ts(item.get("published_at"))
        if pub and pub < now() - timedelta(days=cat.max_age_days):
            return f"older than {cat.max_age_days} days"
    return None


def candidate_ids(s: Session, cat_id: int) -> set[str]:
    return set(s.exec(select(Candidate.video_id).where(Candidate.category_id == cat_id)).all())


def status_counts(s: Session, cat_id: int) -> dict:
    rows = s.exec(select(Candidate.status, func.count()).where(Candidate.category_id == cat_id,
                                                               Candidate.dismissed == False)  # noqa: E712
                  .group_by(Candidate.status)).all()
    return {st: n for st, n in rows}


def accepted_unwatched(s: Session, cat: Category, at=None) -> int:
    """Accepted, non-dismissed, unwatched inventory eligible for refill.

    Local download completion is deliberately not part of this count: the downloader
    can still be working through accepted inventory. A category with an age limit,
    however, must not look full because it contains old videos.
    """
    watched = select(WatchHistory.video_id).where(WatchHistory.profile_id == cat.profile_id,
                                                  WatchHistory.finished == True)  # noqa: E712
    q = select(func.count()).select_from(Candidate).join(Video, Video.id == Candidate.video_id).where(
        Candidate.category_id == cat.id, Candidate.status == "accepted",
        Candidate.dismissed == False, col(Candidate.video_id).not_in(watched))  # noqa: E712
    if cat.max_age_days:
        instant = aware(at) if at is not None else now()
        if instant is None:
            instant = now()
        cutoff = instant - timedelta(days=cat.max_age_days)
        # Unknown publication dates remain eligible. Discovery time is not a proxy
        # for publication age and must not make old timeless inventory disappear.
        q = q.where(or_(Video.published_at.is_(None), Video.published_at >= cutoff))
    return int(s.exec(q).one() or 0)


def accepted_variety(s: Session, cat: Category, at=None) -> tuple[int, int]:
    """Fresh and creator counts among age-eligible, accepted, unfinished videos."""
    instant = aware(at) if at is not None else now()
    if instant is None:
        instant = now()
    watched = select(WatchHistory.video_id).where(WatchHistory.profile_id == cat.profile_id,
                                                  WatchHistory.finished == True)  # noqa: E712
    columns = [Candidate.video_id, Candidate.created_at, Video.published_at,
               Video.channel_id, Video.channel_name, Download.status, Download.path,
               Download.attempts]
    q = select(*columns).join(Video, Video.id == Candidate.video_id)\
        .outerjoin(Download, Download.video_id == Candidate.video_id).where(
        Candidate.category_id == cat.id, Candidate.status == "accepted",
        Candidate.dismissed == False, col(Candidate.video_id).not_in(watched))  # noqa: E712
    if cat.kind == "shorts":
        q = q.add_columns(GeneratedSubtitles.status, GeneratedSubtitles.vtt)\
            .outerjoin(GeneratedSubtitles, GeneratedSubtitles.video_id == Candidate.video_id)
    if cat.max_age_days:
        q = q.where(or_(Video.published_at.is_(None),
                        Video.published_at >= instant - timedelta(days=cat.max_age_days)))
    rows = s.exec(q.order_by(col(Candidate.judged_at).desc().nullslast(), Candidate.id.desc())
                  .limit(2000)).all()
    fresh_cutoff = instant - timedelta(days=7)
    fresh, creators = set(), set()
    for row in rows:
        vid, created_at, published_at, channel_id, channel_name, download_status, download_path, attempts = row[:8]
        caption_status, vtt = row[8:10] if cat.kind == "shorts" else (None, None)
        if download_status == "evicted" or (download_status == "error" and (attempts or 0) >= 3):
            continue
        if cat.kind == "shorts" and (caption_status == "failed" or
                                      (caption_status == "ready" and not subtitle_jobs.usable_vtt(vtt))):
            continue
        if download_status == "done":
            resolved = abs_path(download_path) if download_path else None
            try:
                if not resolved or not resolved.is_file() or resolved.stat().st_size <= 0:
                    continue
            except OSError:
                continue
        published = aware(published_at) if published_at else None
        discovered = aware(created_at) if created_at else None
        if (published is not None and published >= fresh_cutoff) or (
                published is None and discovered is not None and discovered >= fresh_cutoff):
            fresh.add(vid)
        creator = (channel_id or "").strip() or (channel_name or "").strip()
        if creator:
            creators.add(creator.casefold())
    return len(fresh), len(creators)


def terminal_failures(s: Session, cat: Category, at=None) -> int:
    """Count bounded accepted inventory that cannot become locally ready as-is."""
    instant = aware(at) if at is not None else now()
    if instant is None:
        instant = now()
    watched = select(WatchHistory.video_id).where(WatchHistory.profile_id == cat.profile_id,
                                                  WatchHistory.finished == True)  # noqa: E712
    q = select(Video.id, Download.status, Download.path, Download.attempts)\
        .join(Candidate, Candidate.video_id == Video.id)\
        .outerjoin(Download, Download.video_id == Video.id)\
        .where(Candidate.category_id == cat.id, Candidate.status == "accepted",
               Candidate.dismissed == False, col(Video.id).not_in(watched))  # noqa: E712
    if cat.kind == "shorts":
        q = q.add_columns(GeneratedSubtitles.status, GeneratedSubtitles.vtt)\
            .outerjoin(GeneratedSubtitles, GeneratedSubtitles.video_id == Video.id)
    if cat.max_age_days:
        q = q.where(or_(Video.published_at.is_(None),
                        Video.published_at >= instant - timedelta(days=cat.max_age_days)))
    rows = s.exec(q.limit(2000)).all()
    failed = set()
    file_cache = {}
    for row in rows:
        vid, download_status, path, attempts = row[:4]
        subtitle_status, vtt = row[4:6] if cat.kind == "shorts" else (None, None)
        is_failed = download_status == "evicted" or (
            download_status == "error" and (attempts or 0) >= 3)
        if download_status == "done":
            if path not in file_cache:
                resolved = abs_path(path) if path else None
                try:
                    file_cache[path] = bool(resolved and resolved.is_file() and resolved.stat().st_size > 0)
                except OSError:
                    file_cache[path] = False
            is_failed = not file_cache[path]
        if cat.kind == "shorts" and (subtitle_status == "failed" or
                                      (subtitle_status == "ready" and not subtitle_jobs.usable_vtt(vtt))):
            is_failed = True
        if is_failed:
            failed.add(vid)
    return len(failed)


def category_refill_deficit(s: Session, cat: Category, at=None,
                            pending: Optional[int] = None) -> int:
    """Base target deficit plus a small, cooled-down freshness/diversity top-up."""
    target = max(0, cat.target_queue or 0)
    accepted = accepted_unwatched(s, cat, at)
    if target == 0:
        return 0
    instant = aware(at) if at is not None else now()
    if instant is None:
        instant = now()
    failed = terminal_failures(s, cat, instant)
    deficit = max(0, target - max(0, accepted - failed))
    headroom = max(3, min(10, target // 2))
    ceiling = target + headroom
    if accepted < target or target < 5:
        # Terminal media failures create replacement demand, but cap the total
        # accepted inventory so a broken source cannot grow the queue forever.
        return min(deficit, max(0, ceiling - accepted))
    counts = status_counts(s, cat.id)
    backlog = counts.get("found", 0) + counts.get("enriched", 0) if pending is None else pending
    if backlog >= LOW_WATER:
        return 0
    last = aware(cat.last_worked_at)
    if last is not None and (instant - last).total_seconds() < 3600:
        return 0
    fresh, creators = accepted_variety(s, cat, instant)
    gap = max(deficit, max(3 - fresh, 3 - creators, 0))
    available = max(0, ceiling - accepted)
    # Account for work already in the candidate pipeline before adding more.
    return min(gap, available, max(0, LOW_WATER - backlog))


@dataclass
class Need:
    cat: Category
    deficit: int
    weight: float
    counts: dict


def category_needs(s: Session, focus_id: Optional[int] = None) -> list[Need]:
    """Enabled categories (all profiles) with a queue deficit, neediest first.

    weight = priority × deficit, boosted by time since last worked (so categories rotate)."""
    out = []
    t = now()
    for cat in s.exec(select(Category).where(Category.enabled == True)).all():  # noqa: E712
        counts = status_counts(s, cat.id)
        pending = counts.get("found", 0) + counts.get("enriched", 0)
        deficit = category_refill_deficit(s, cat, t, pending)
        if deficit <= 0 and cat.id != focus_id and pending == 0:
            continue
        last = aware(cat.last_worked_at)
        idle_h = 24.0 if last is None else min(24.0, (t - last).total_seconds() / 3600)
        weight = max(1, cat.priority or 1) * max(deficit, 1) * (1 + idle_h)
        if cat.id == focus_id:
            weight = float("inf")
        out.append(Need(cat, deficit, weight, counts))
    out.sort(key=lambda n: (-n.weight, aware(n.cat.last_worked_at) or t - timedelta(days=3650)))
    return out


def recent_titles(s: Session, cat_id: int, status: str, n: int = 8) -> list[str]:
    rows = s.exec(select(Video.title).join(Candidate, Candidate.video_id == Video.id)
                  .where(Candidate.category_id == cat_id, Candidate.status == status)
                  .order_by(col(Candidate.judged_at).desc()).limit(n)).all()
    return [t for t in rows if t]


def screened(c: Candidate) -> bool:
    """Dropped by the title screen; judged rejections always carry a score, even when the judge says off-topic."""
    return c.score is None and str((c.red_flags or [""])[0]).startswith("off-topic:")


def rejections_for(s: Session, cat_id: int, profile_id: int, n: int = 8) -> list[str]:
    """Recently rejected candidates in a category, with the reason. The family's own feedback
    (dislike chips/text) beats the AI's verdict note; the AI note is the fallback."""
    rows = s.exec(select(Candidate, Video).join(Video, Video.id == Candidate.video_id, isouter=True)
                  .where(Candidate.category_id == cat_id, Candidate.status == "rejected")
                  .order_by(col(Candidate.judged_at).desc()).limit(n * 4)).all()
    disliked = {f.video_id: f for f in s.exec(
        select(Feedback).where(Feedback.profile_id == profile_id, Feedback.content_type == "video", Feedback.rating < 0)
        .order_by(col(Feedback.created_at))).all()}  # last dislike per video wins
    out = []
    for c, v in rows:
        f = disliked.get(c.video_id)
        if f is None and screened(c):
            continue  # title-screen misses teach nothing about quality; discovery_yield counts them
        if len(out) >= n:
            break
        why = ""
        if f is not None:
            parts = [p for p in [", ".join(f.chips or []), (f.text or "").strip()] if p]
            why = "family: " + "; ".join(parts) if parts else ""
        if not why:
            ai = (c.red_flags or [""])[0] or c.summary
            why = f"AI: {ai}" if ai else ""
        out.append(f"{(v.title if v else c.video_id)} ({why})" if why else (v.title if v else c.video_id))
    return out


def queue_titles(s: Session, cat_id: int, profile_id: int, n: int = 15) -> list[str]:
    """Titles currently sitting in the category feed: accepted, not dismissed, not finished watching."""
    watched = set(s.exec(select(WatchHistory.video_id).where(WatchHistory.profile_id == profile_id,
                                                             WatchHistory.finished == True)).all())  # noqa: E712
    q = select(Video.title).join(Candidate, Candidate.video_id == Video.id).where(
        Candidate.category_id == cat_id, Candidate.status == "accepted",
        Candidate.dismissed == False)  # noqa: E712
    if watched:
        q = q.where(col(Candidate.video_id).not_in(watched))
    rows = s.exec(q.order_by(col(Candidate.judged_at).desc()).limit(n)).all()
    return [t for t in rows if t]


def queue_context(s: Session, cat_id: int, profile_id: int, n: int = 40) -> list[str]:
    """Rich, bounded context for curation prompts.

    Keep ``queue_titles`` as the small title-only compatibility helper used by
    callers and tests, while prompts get enough signal to reason about
    repetition: creator, runtime, and the judge's topic-first tags.
    """
    watched = set(s.exec(select(WatchHistory.video_id).where(
        WatchHistory.profile_id == profile_id, WatchHistory.finished == True)).all())  # noqa: E712
    q = select(Video, Candidate).join(Candidate, Candidate.video_id == Video.id).where(
        Candidate.category_id == cat_id, Candidate.status == "accepted",
        Candidate.dismissed == False)  # noqa: E712
    if watched:
        q = q.where(col(Candidate.video_id).not_in(watched))
    rows = s.exec(q.order_by(col(Candidate.judged_at).desc(), col(Candidate.id).desc()).limit(n)).all()
    out = []
    for v, candidate in rows:
        tags = [str(t).strip().lower() for t in (candidate.tags or []) if str(t).strip()]
        topic = ", ".join(tags[:6]) or "(untagged)"
        creator = v.channel_name or v.channel_id or "unknown creator"
        out.append(f"{v.title} — {creator} — {_duration_label(v.duration_s)} — topics: {topic}")
    return out


def _duration_label(seconds: Optional[int]) -> str:
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "duration unknown"
    return f"{seconds // 60}:{seconds % 60:02d}"


def recent_dislikes(s: Session, profile_id: int, n: int = 8) -> list[str]:
    rows = s.exec(select(Feedback, Video).join(Video, Video.id == Feedback.video_id, isouter=True)
                  .where(Feedback.profile_id == profile_id, Feedback.content_type == "video", Feedback.rating < 0)
                  .order_by(col(Feedback.created_at).desc()).limit(n)).all()
    out = []
    for f, v in rows:
        why = ", ".join(f.chips or []) + (f" — {f.text}" if f.text else "")
        out.append(f"{v.title if v else f.video_id}" + (f" ({why})" if why else ""))
    return out


def touch_category(s: Session, cat_id: int) -> None:
    c = s.get(Category, cat_id)
    if c:
        c.last_worked_at = now()
        s.add(c)
        s.commit()


def discovery_yield(s: Session, cat_id: int, n: int = 120) -> list[str]:
    """Recent judged search batches, so planning can improve poor retrieval phrases."""
    rows = s.exec(select(Candidate, Video).join(Video, Video.id == Candidate.video_id)
                  .where(Candidate.category_id == cat_id, Candidate.status.in_(["accepted", "rejected"]),
                         Candidate.dismissed == False)
                  .order_by(col(Candidate.judged_at).desc(), col(Candidate.id).desc()).limit(n)).all()
    groups = {}
    for c, video in rows:
        # Recover channel provenance for historical rows that only recorded "rss".
        source = f"rss: {video.channel_id}" if c.source == "rss" else c.source
        if not source.startswith(("search:", "channel:", "rss:")):
            continue
        group = groups.setdefault(source, {"accepted": 0, "rejected": 0, "off_topic": 0, "examples": []})
        group[c.status] += 1
        group["off_topic"] += c.status == "rejected" and screened(c)
        if c.status == "rejected" and len(group["examples"]) < 2:
            group["examples"].append(video.title[:120])
    return [f"{source}: {g['accepted']} accepted, {g['rejected']} rejected"
            + (f" ({g['off_topic']} off-topic)" if g["off_topic"] else "")
            + ("; rejected examples: " + "; ".join(g["examples"]) if g["examples"] else "")
            for source, g in list(groups.items())[:12]]
