"""DB queries shared by the pipeline steps."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, col, func, select

from ..models import Candidate, Category, Feedback, Lesson, ProfileChannel, Video, WatchHistory, now
from .runtime import aware, parse_ts

BLOCK_TRUST = -0.8
FOUND_CAP = 12  # max `found` backlog per category
LOW_WATER = 4  # discover more when pending (found+enriched) drops below this


def lessons_for(s: Session, cat: Category, channel_id: Optional[str] = None) -> list[Lesson]:
    q = select(Lesson).where(Lesson.profile_id == cat.profile_id, Lesson.active == True,  # noqa: E712
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
                                                            Feedback.rating < 0)).all()),
    )


def subscribed_channels(s: Session, profile_id: int) -> list[ProfileChannel]:
    return list(s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == profile_id,
                                                    ProfileChannel.subscribed == True,  # noqa: E712
                                                    ProfileChannel.blocked == False)).all())  # noqa: E712


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
    if hide_shorts and item.get("is_short"):
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


def accepted_unwatched(s: Session, cat: Category) -> int:
    watched = set(s.exec(select(WatchHistory.video_id).where(WatchHistory.profile_id == cat.profile_id,
                                                             WatchHistory.finished == True)).all())  # noqa: E712
    vids = s.exec(select(Candidate.video_id).where(Candidate.category_id == cat.id, Candidate.status == "accepted",
                                                   Candidate.dismissed == False)).all()  # noqa: E712
    return sum(1 for v in vids if v not in watched)


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
        deficit = (cat.target_queue or 0) - accepted_unwatched(s, cat)
        if deficit <= 0 and cat.id != focus_id:
            continue
        last = aware(cat.last_worked_at)
        idle_h = 24.0 if last is None else min(24.0, (t - last).total_seconds() / 3600)
        weight = max(1, cat.priority or 1) * max(deficit, 1) * (1 + idle_h)
        if cat.id == focus_id:
            weight = float("inf")
        out.append(Need(cat, deficit, weight, status_counts(s, cat.id)))
    out.sort(key=lambda n: (-n.weight, aware(n.cat.last_worked_at) or t - timedelta(days=3650)))
    return out


def recent_titles(s: Session, cat_id: int, status: str, n: int = 8) -> list[str]:
    rows = s.exec(select(Video.title).join(Candidate, Candidate.video_id == Video.id)
                  .where(Candidate.category_id == cat_id, Candidate.status == status)
                  .order_by(col(Candidate.judged_at).desc()).limit(n)).all()
    return [t for t in rows if t]


def screened(c: Candidate) -> bool:
    return str((c.red_flags or [""])[0]).startswith("off-topic:")


def rejections_for(s: Session, cat_id: int, profile_id: int, n: int = 8) -> list[str]:
    """Recently rejected candidates in a category, with the reason. The family's own feedback
    (dislike chips/text) beats the AI's verdict note; the AI note is the fallback."""
    rows = s.exec(select(Candidate, Video).join(Video, Video.id == Candidate.video_id, isouter=True)
                  .where(Candidate.category_id == cat_id, Candidate.status == "rejected")
                  .order_by(col(Candidate.judged_at).desc()).limit(n * 4)).all()
    disliked = {f.video_id: f for f in s.exec(
        select(Feedback).where(Feedback.profile_id == profile_id, Feedback.rating < 0)
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
                  .where(Feedback.profile_id == profile_id, Feedback.rating < 0)
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
    rows = s.exec(select(Candidate, Video.title).join(Video, Video.id == Candidate.video_id)
                  .where(Candidate.category_id == cat_id, Candidate.status.in_(["accepted", "rejected"]),
                         Candidate.dismissed == False, Candidate.source.startswith("search:"))
                  .order_by(col(Candidate.judged_at).desc(), col(Candidate.id).desc()).limit(n)).all()
    groups = {}
    for c, title in rows:
        group = groups.setdefault(c.source, {"accepted": 0, "rejected": 0, "off_topic": 0, "examples": []})
        group[c.status] += 1
        group["off_topic"] += c.status == "rejected" and screened(c)
        if c.status == "rejected" and len(group["examples"]) < 2:
            group["examples"].append(title[:120])
    return [f"{source}: {g['accepted']} accepted, {g['rejected']} rejected"
            + (f" ({g['off_topic']} off-topic)" if g["off_topic"] else "")
            + ("; rejected examples: " + "; ".join(g["examples"]) if g["examples"] else "")
            for source, g in list(groups.items())[:12]]
