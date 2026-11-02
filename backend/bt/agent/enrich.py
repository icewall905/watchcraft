"""Enrich one `found` candidate: video_info + transcript + comments (each separately rate-limited)."""
from __future__ import annotations

import logging
from time import monotonic, time

from sqlalchemy import exists, update

from ..db import drop_kv, get_kv, save_setting, session
from ..models import Candidate, Category, Video, now
from . import data, timing
from .runtime import Runtime

log = logging.getLogger("bt.agent")


def _fail(rt: Runtime, rl, cand_id: int, cat: Category, title: str, err: Exception, what: str,
          *, request_made: bool = True) -> None:
    if request_made:
        rl.failed()
    with session() as s:
        c = s.get(Candidate, cand_id)
        if c is None or c.status != "found" or c.dismissed:
            return
        result = s.execute(update(Candidate).where(
            Candidate.id == cand_id, Candidate.status == "found", Candidate.dismissed == False,  # noqa: E712
        ).values(status="error", error=f"{what}: {err}"[:500]))
        s.commit()
        if not result.rowcount:
            return
    rt.event("error", f"⚠️ Skipped {title or c.video_id} — {what} failed: {err}"[:500], "warn", cat,
             video_id=c.video_id, payload={"error": str(err)[:500], "step": what})


def enrich_one(rt: Runtime, rl, cand: Candidate, cat: Category) -> str:
    """Returns resulting candidate status. YtBlocked propagates (caller opens the circuit breaker)."""
    yt, th = rt.yt, rt.throttle()
    with session() as s:
        v = s.get(Video, cand.video_id)
        title = v.title if v else cand.video_id
        current = s.get(Candidate, cand.id)
        category = s.get(Category, cat.id)
        if current is None or category is None or not category.enabled:
            return "gone"  # deleted meanwhile
        if current.status != "found" or current.dismissed:
            return "gone"
        if v and v.enriched_at:
            rt.check()
            # Conditional write prevents a manual decision or dismissal made
            # after selection from being overwritten by the reuse path.
            result = s.execute(update(Candidate).where(
                Candidate.id == cand.id, Candidate.status == "found", Candidate.dismissed == False,  # noqa: E712
                exists().where(Category.id == cat.id, Category.enabled == True),  # noqa: E712
            ).values(status="enriched"))
            if not result.rowcount:
                s.rollback()
                return "gone"
            category.last_worked_at = now()
            s.add(category)
            s.commit()
            rt.event("metadata", f"♻️ Already researched: {title} — reusing transcript & comments", "debug", cat,
                     video_id=cand.video_id)
            return "enriched"
        data.touch_category(s, cat.id)

    key = f"research:{cand.video_id}"
    progress = get_kv(key, {})
    st = dict(video_id=cand.video_id, title=title)
    # 1) full metadata
    # Caption URLs expire. Once the transcript is durable we only need the
    # Video metadata; otherwise refresh caption discovery after one hour.
    reuse = bool(v and v.fetched_at and progress.get("metadata_done") and
                 ("transcript" in progress or time() - progress.get("metadata_ts", 0) < 3600))
    info = progress.get("caption_info", {})
    if not reuse:
        request_made = False
        try:
            rt.check()
            # The cache-only operation cannot extract on a miss, even if the
            # cache changes concurrently. Older injected providers may not
            # expose this optional seam and retain the normal admission path.
            cache_lookup = getattr(yt, "cached_video_info", None)
            with timing.span("metadata_cache", candidate_id=cand.id, category_id=cat.id, profile_id=cat.profile_id) as measured:
                info = cache_lookup(cand.video_id) if cache_lookup else None
                measured.result = "hit" if info is not None else "miss"
            if info is None:
                rl.wait_turn("metadata", cat, **st)
                rt.state("fetching", f"Fetching details: {title}", cat, **st)
                rt.event("metadata", f"📄 Fetching details: {title}", "info", cat, video_id=cand.video_id)
                rt.check()
                started = monotonic()
                request_made = True
                with timing.span("metadata", candidate_id=cand.id, category_id=cat.id, profile_id=cat.profile_id):
                    info = yt.video_info(cand.video_id, origin="worker")
                log.info("research %s metadata completed in %.2fs", cand.video_id, monotonic() - started)
                rl.success()
        except yt.YtBlocked:
            raise
        except yt.YtError as e:
            _fail(rt, rl, cand.id, cat, title, e, "metadata", request_made=request_made)
            return "error"
    with session() as s:
        v = s.get(Video, cand.video_id) if reuse else yt.upsert_video(s, info)
        if v is None:
            drop_kv(key)
            return "gone"
        if not v.chapters and info.get("chapters"):
            v.chapters = info["chapters"]
        v.fetched_at = v.fetched_at or now()
        s.add(v)
        s.commit()
        title = v.title or title
        st["title"] = title
        why = data.prefilter(v.model_dump(), cat, data.profile_ctx(s, cat.profile_id),
                             bool(rt.settings()["extras"].get("hide_shorts", True)))
        c = s.get(Candidate, cand.id)
        if c is None or c.status != "found" or c.dismissed:
            return "gone"
        if why:
            result = s.execute(update(Candidate).where(
                Candidate.id == cand.id, Candidate.status == "found", Candidate.dismissed == False,  # noqa: E712
            ).values(status="rejected", red_flags=[f"prefilter: {why}"], summary="", judged_at=now()))
            s.commit()
            if not result.rowcount:
                return "gone"
    if not reuse:
        progress.update(metadata_done=True, metadata_ts=time(), caption_info={
            k: info[k] for k in ("id", "title", "language", "subtitles", "automatic_captions") if k in info})
        save_setting(key, progress)
    if why:
        drop_kv(key)
        rt.event("reject", f"🚫 Filtered: {title} — {why}", "info", cat, video_id=cand.video_id,
                 payload={"prefilter": why})
        return "rejected"

    # 2) transcript (optional — a failure is not fatal)
    tr = progress.get("transcript", "")
    if "transcript" not in progress:
        rt.check()
        pick_track = getattr(yt, "pick_track", None)
        with timing.span("caption_check", candidate_id=cand.id, category_id=cat.id, profile_id=cat.profile_id) as measured:
            no_track = pick_track is not None and pick_track(info) is None
            if pick_track is not None:
                measured.result = "no_track" if no_track else "available"
        if no_track:
            # Absence is a completed, network-free attempt, unlike a blocked
            # or interrupted transfer. Keep it in the resumable checkpoint.
            progress["transcript"] = ""
            progress["transcript_state"] = "no_track"
            save_setting(key, progress)
    if "transcript" not in progress:
        rl.wait_turn("transcript", cat, **st)
        rt.state("transcript", f"Reading transcript: {title}", cat, **st)
        rt.event("transcript", f"📜 Reading transcript: {title}", "info", cat, video_id=cand.video_id)
        try:
            started = monotonic()
            with timing.span("transcript", candidate_id=cand.id, category_id=cat.id, profile_id=cat.profile_id):
                tr = yt.transcript(info, int(th.get("transcript_max_chars") or 40000)) or ""
            log.info("research %s transcript completed in %.2fs", cand.video_id, monotonic() - started)
            rl.success()
        except yt.YtBlocked:
            raise
        except yt.YtError as e:
            rl.failed()
            tr = ""
            rt.event("transcript", f"⚠️ No transcript for {title}: {e}"[:500], "warn", cat, video_id=cand.video_id)
        # Empty is a completed attempt too. Blocked/paused attempts never reach
        # this checkpoint, so they are retried on resume.
        progress["transcript"] = tr
        save_setting(key, progress)

    # 3) comments (optional)
    rt.check()
    n = int(th.get("max_comments") or 40)
    rl.wait_turn("comments", cat, **st)
    rt.state("comments", f"Reading comments: {title}", cat, **st)
    try:
        started = monotonic()
        with timing.span("comments", candidate_id=cand.id, category_id=cat.id, profile_id=cat.profile_id):
            cm = list(yt.comments(cand.video_id, max_comments=n, origin="worker") or [])
        log.info("research %s comments completed in %.2fs", cand.video_id, monotonic() - started)
        rl.success()
    except yt.YtBlocked:
        raise
    except yt.YtError as e:
        rl.failed()
        cm = []
        rt.event("comments", f"⚠️ Comments unavailable for {title}: {e}"[:500], "warn", cat, video_id=cand.video_id)
    rt.event("comments", f"💬 Read {len(cm)} comments: {title}", "info", cat, video_id=cand.video_id,
             payload={"comments": len(cm), "transcript_chars": len(tr)})
    with session() as s:
        v = s.get(Video, cand.video_id) or Video(id=cand.video_id)
        v.transcript, v.comments, v.enriched_at = tr, cm, now()
        s.add(v)
        # Keep reusable public evidence, but never overwrite a manual decision,
        # dismissal or disabled playlist made while the network call ran.
        result = s.execute(update(Candidate).where(
            Candidate.id == cand.id, Candidate.status == "found", Candidate.dismissed == False,  # noqa: E712
            exists().where(Category.id == cat.id, Category.enabled == True),  # noqa: E712
        ).values(status="enriched"))
        s.commit()
        if not result.rowcount:
            return "gone"
    drop_kv(key)
    return "enriched"
