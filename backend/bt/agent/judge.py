"""Judge `enriched` candidates with the LLM (transcript + comments + thumbnail)."""
from __future__ import annotations

import base64
import logging
from time import monotonic
from typing import Optional

import httpx
from sqlmodel import col, select

from ..db import session
from ..models import Candidate, Category, Video, now
from . import data, prompts
from .runtime import Interrupt, Runtime

log = logging.getLogger("bt.agent")


def fetch_thumb(video_id: str) -> Optional[str]:
    """Thumbnail as base64 data URL (static CDN, not a YouTube API request)."""
    try:
        r = httpx.get(f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", timeout=15, follow_redirects=True)
        if r.status_code == 200 and r.content:
            ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
            return f"data:{ctype};base64," + base64.b64encode(r.content).decode()
    except httpx.HTTPError as e:
        log.info("thumbnail fetch failed for %s: %s", video_id, e)
    return None


def _str_list(v, n: int = 8) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x).strip()[:200] for x in (v or []) if str(x).strip()][:n]


def normalize(out: dict) -> dict:
    try:
        score = int(round(float(out.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    ks = out.get("kid_safe", False)
    if isinstance(ks, str):
        ks = ks.strip().lower() in ("true", "yes", "1")
    return {
        "score": max(0, min(100, score)),
        "summary": str(out.get("summary") or "").strip()[:600],
        "reasons": _str_list(out.get("reasons") or out.get("fit_reasons")),
        "red_flags": _str_list(out.get("red_flags")),
        "kid_safe": bool(ks),
        "tags": [t.lower() for t in _str_list(out.get("tags"), 8)],
    }


def decide(cat: Category, v: dict) -> tuple[bool, str]:
    if not v["kid_safe"] and (cat.kid_safe or prompts.child_rails(cat)):
        return False, "not kid-safe"
    if v["score"] < (cat.threshold or 0):
        return False, (v["red_flags"][0] if v["red_flags"] else v["summary"] or "below threshold")
    return True, v["summary"]


JUDGE_TRANSCRIPT_MAX = 24000  # chars of transcript sent to the judge (GPU budget)
JUDGE_COMMENTS_MAX = 30  # comments (each clipped to 300 chars)


def _mark_error(cand_id: int, why: str) -> None:
    with session() as s:
        c = s.get(Candidate, cand_id)
        if c is not None:
            c.status, c.error = "error", why
            s.add(c)
            s.commit()


def pending(limit: int = 20) -> list[Candidate]:
    """Enriched candidates ready for judging; orphans (category/video gone) are marked error and skipped."""
    with session() as s:
        rows = s.exec(select(Candidate, Category.id, Video.id)
                      .join(Category, Category.id == Candidate.category_id, isouter=True)
                      .join(Video, Video.id == Candidate.video_id, isouter=True)
                      .where(Candidate.status == "enriched", Candidate.dismissed == False)  # noqa: E712
                      .order_by(col(Candidate.created_at)).limit(limit * 3)).all()
    out = []
    for c, cat_id, vid in rows:
        if cat_id is None or vid is None:
            _mark_error(c.id, "orphaned: category or video deleted")
            continue
        out.append(c)
    return out[:limit]


def judge_one(rt: Runtime, cand: Candidate) -> Optional[str]:
    """Returns 'accepted' / 'rejected', or None if the LLM failed (candidate stays enriched)."""
    started = monotonic()
    st = rt.settings()
    with session() as s:
        cat = s.get(Category, cand.category_id)
        v = s.get(Video, cand.video_id)
        if not cat or not v:
            _mark_error(cand.id, "orphaned: category or video deleted")
            return None
        lessons = data.lessons_for(s, cat, v.channel_id)
        from .memory import select_memory
        core, lessons = select_memory(s, cat.profile_id, lessons)
        trust = data.trust_for(s, cat.profile_id, v.channel_id) if v.channel_id else None
        queue = data.queue_context(s, cat.id, cat.profile_id)
        rejections = data.rejections_for(s, cat.id, cat.profile_id)
    msgs = prompts.judge_messages(
        cat, v, lessons, trust, min(JUDGE_TRANSCRIPT_MAX, int(st["throttle"].get("transcript_max_chars") or 40000)),
        min(JUDGE_COMMENTS_MAX, int(st["throttle"].get("max_comments") or 40)), queue, rejections, core=core)
    rt.state("judging", f"Judging: {v.title}", cat, video_id=v.id, title=v.title)
    rt.event("judge", f"🤔 Judging: {v.title}", "info", cat, video_id=v.id,
             payload={"candidate_id": cand.id, "lessons": len(lessons), "trust": trust, "comments": len(v.comments or []),
                      "transcript_chars": len(v.transcript or "")})
    images = None
    # Thumbnail comes from the i.ytimg.com CDN; still skip it while YouTube is in backoff or someone is watching.
    yt_quiet = (rt.backoff_until is not None and rt.backoff_until > now()) or rt.playing(th=st["throttle"]) is not None
    if st["llm"].get("vision") and not yt_quiet:
        img = (rt.thumb or fetch_thumb)(v.id)
        images = [img] if img else None
    kw = dict(json_schema=prompts.JUDGE_SCHEMA, max_tokens=3000, schema_name="verdict", abort=rt.check)
    try:
        out = rt.llm.chat_json(msgs, images=images, **kw, role='video_verdict')
    except Interrupt:
        raise
    except Exception as e:  # noqa: BLE001
        if not images:
            rt.llm_failed(f"judging {v.title}", e, category=cat, video_id=v.id)
            return None
        log.info("vision judge failed (%s); retrying without image", e)
        try:
            out = rt.llm.chat_json(msgs, images=None, **kw, role='video_verdict')
        except Interrupt:
            raise
        except Exception as e2:  # noqa: BLE001
            rt.llm_failed(f"judging {v.title}", e2, category=cat, video_id=v.id)
            return None
    log.info("judge %s completed in %.2fs", v.id, monotonic() - started)
    verdict = normalize(out)
    ok, why = decide(cat, verdict)
    with session() as s:
        c = s.get(Candidate, cand.id)
        if c is None or c.status != "enriched":
            return None  # changed under us (e.g. deleted / manually accepted)
        c.status = "accepted" if ok else "rejected"
        c.score, c.summary, c.reasons = verdict["score"], verdict["summary"], verdict["reasons"]
        c.red_flags, c.tags, c.judged_at, c.error = verdict["red_flags"], verdict["tags"], now(), ""
        s.add(c)
        s.commit()
    payload = {**verdict, "threshold": cat.threshold, "accepted": ok, "candidate_id": cand.id}
    if ok:
        rt.event("accept", f"✅ Accepted ({verdict['score']}): {v.title} — {verdict['summary']}"[:600], "success",
                 cat, video_id=v.id, payload=payload)
    else:
        rt.event("reject", f"❌ Rejected ({verdict['score']}): {v.title} — {why}"[:600], "info", cat,
                 video_id=v.id, payload=payload)
    return "accepted" if ok else "rejected"
