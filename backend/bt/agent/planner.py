"""Plan (LLM search queries, cached per category) + discover (search / channel tab / RSS) new candidates."""
from __future__ import annotations

from typing import Optional

from sqlmodel import select

from ..db import get_kv, save_setting, session
from ..models import Candidate, Category, Channel, ProfileChannel, now
from . import data, prompts
from .runtime import Interrupt, Runtime, iso, parse_ts

REPLAN_MIN_S = 1800  # never re-plan a category more often than this
PLAN_MAX_AGE_S = 12 * 3600  # re-plan when the cached plan is older than this
TRIED_KEEP = 40


def plan_key(cat_id: int) -> str:
    return f"plan:{cat_id}"


def load_plan(cat_id: int) -> dict:
    p = get_kv(plan_key(cat_id), {}) or {}
    p.setdefault("queries", [])
    p.setdefault("channels", [])
    p.setdefault("tried", [])
    p.setdefault("cursor", 0)
    p.setdefault("planned_at", None)
    return p


def save_plan(cat_id: int, plan: dict) -> None:
    save_setting(plan_key(cat_id), plan)


def sources(cat: Category, plan: dict, subs: list[str]) -> list[tuple[str, str]]:
    """Ordered rotation of discovery sources: (kind, ref) with kind in query|channel|rss."""
    out, seen = [], set()

    def add(kind, ref):
        ref = (ref or "").strip()
        key = (kind, ref.lower())
        if ref and key not in seen:
            seen.add(key)
            out.append((kind, ref))

    qs = list(plan.get("queries") or [])
    chans = list(cat.seed_channels or []) + list(plan.get("channels") or [])
    seeds = list(cat.seed_queries or [])
    # interleave: planner query, seed query, channel, rss ...
    for i in range(max(len(qs), len(seeds), len(chans), len(subs))):
        if i < len(qs):
            add("query", qs[i])
        if i < len(seeds):
            add("query", seeds[i])
        if i < len(chans):
            add("channel", chans[i])
        if i < len(subs):
            add("rss", subs[i])
    return out


def make_plan(rt: Runtime, cat: Category, plan: dict) -> bool:
    """Ask the LLM for fresh queries. Returns True on success (plan updated in place)."""
    with session() as s:
        from .memory import select_memory
        core, lessons = select_memory(s, cat.profile_id, data.lessons_for(s, cat))
        msgs = prompts.planner_messages(
            cat, lessons, data.recent_titles(s, cat.id, "accepted"),
            data.rejections_for(s, cat.id, cat.profile_id), data.recent_dislikes(s, cat.profile_id),
            plan.get("tried", [])[-TRIED_KEEP:], data.queue_context(s, cat.id, cat.profile_id), core=core,
            search_yield=data.discovery_yield(s, cat.id))
    rt.state("planning", f"Planning searches for {cat.name}", cat)
    rt.event("plan", f"🧭 Planning new searches for {cat.name}", "info", cat)
    try:
        out = rt.llm.chat_json(msgs, json_schema=prompts.PLAN_SCHEMA, max_tokens=2500, schema_name="plan",
                               abort=rt.check)
    except Interrupt:
        raise
    except Exception as e:  # noqa: BLE001
        rt.llm_failed(f"planning {cat.name}", e, category=cat)
        return False
    tried = {t.lower() for t in plan.get("tried", [])}
    queries = [q.strip() for q in out.get("queries") or [] if isinstance(q, str) and q.strip()]
    fresh = [q for q in queries if q.lower() not in tried] or queries
    channels = [c.strip() for c in out.get("channels") or [] if isinstance(c, str) and c.strip()]
    plan.update(queries=fresh[:6], channels=channels[:4], planned_at=iso(now()), cursor=0)
    rt.event("plan", f"🧭 Plan for {cat.name}: " + "; ".join(f'"{q}"' for q in plan["queries"]), "info", cat,
             payload={"queries": plan["queries"], "channels": plan["channels"]})
    return True


def _insert(rt: Runtime, cat: Category, entries: list[dict], source: str) -> dict:
    """Prefilter + insert found candidates. Returns counts."""
    hide_shorts = bool(rt.settings()["extras"].get("hide_shorts", True))
    added, skipped, reasons, new = 0, 0, {}, []
    with session() as s:
        ctx = data.profile_ctx(s, cat.profile_id)
        existing = data.candidate_ids(s, cat.id)
        room = data.FOUND_CAP - data.status_counts(s, cat.id).get("found", 0)
        for e in entries:
            why = data.prefilter(e, cat, ctx, hide_shorts, existing)
            if why is None and room <= 0:
                why = "backlog full"
            if why:
                skipped += 1
                reasons[why.split(" (")[0]] = reasons.get(why.split(" (")[0], 0) + 1
                continue
            rt.yt.upsert_video(s, e)
            cand = Candidate(category_id=cat.id, video_id=e["id"], status="found", source=source)
            s.add(cand)
            new.append(cand)
            existing.add(e["id"])
            added += 1
            room -= 1
        s.commit()
        ids = [c.id for c in new]
    return {"found": len(entries), "added": added, "skipped": skipped, "skip_reasons": reasons,
            "candidate_ids": ids}


def discover(rt: Runtime, rl, cat: Category) -> Optional[dict]:
    """Run ONE discovery source for `cat` (rotating). May re-plan via LLM first. Returns counts, or None if no source exists."""
    plan = load_plan(cat.id)
    with session() as s:
        subs = [pc.channel_id for pc in data.subscribed_channels(s, cat.profile_id)]
    srcs = sources(cat, plan, subs)
    planned = parse_ts(plan.get("planned_at"))
    age = (now() - planned).total_seconds() if planned else None
    want_plan = (age is None or age > PLAN_MAX_AGE_S
                 or (plan["cursor"] >= len(srcs) and age > REPLAN_MIN_S))
    if want_plan and rt.llm_ok():
        rt.check()
        if make_plan(rt, cat, plan):
            srcs = sources(cat, plan, subs)
        save_plan(cat.id, plan)
    if not srcs:
        rt.event("plan", f"🤷 No search ideas for {cat.name} yet (add seed queries or check the LLM)", "warn", cat)
        return None
    i = plan["cursor"] % len(srcs)
    kind, ref = srcs[i]
    plan["cursor"] = i + 1
    rt.check()
    th = rt.throttle()
    with session() as s:
        data.touch_category(s, cat.id)
    yt = rt.yt
    if kind == "query":
        rl.wait_turn("search", cat)
        rt.state("searching", f'Searching "{ref}"', cat, query=ref)
        rt.event("search", f'🔎 Searching "{ref}" for {cat.name}', "info", cat, payload={"query": ref})
        if ref not in plan["tried"]:
            plan["tried"] = (plan["tried"] + [ref])[-TRIED_KEEP:]
        save_plan(cat.id, plan)
        entries = _call(rt, rl, lambda: yt.search(ref, n=int(th.get("search_results") or 15), origin="worker"))
        source = f"search: {ref}"
    elif kind == "channel":
        rl.wait_turn("channel", cat)
        rt.state("searching", f"Browsing channel {ref}", cat, query=ref)
        rt.event("search", f"📺 Browsing channel {ref} for {cat.name}", "info", cat, payload={"channel": ref})
        save_plan(cat.id, plan)
        try:
            res = _call(rt, rl, lambda: yt.channel_videos(ref, n=30, origin="worker"))
        except _Skip as e:
            if ref in plan.get("channels", []):
                plan["channels"] = [c for c in plan["channels"] if c != ref]
                save_plan(cat.id, plan)
            raise e
        ch, entries = res if isinstance(res, tuple) else ({}, res)
        _save_channel(ch)
        source = f"channel: {ch.get('name') or ref}"
    else:  # rss
        rl.wait_turn("rss", cat)
        rt.state("searching", f"Checking subscription feed {ref}", cat, query=ref)
        rt.event("rss", f"📰 Checking new uploads from subscribed channel {ref}", "info", cat,
                 payload={"channel_id": ref})
        save_plan(cat.id, plan)
        res = _call(rt, rl, lambda: rt.feeds.channel_feed(ref))
        ch, entries = res if isinstance(res, tuple) else ({}, res)
        with session() as s:
            pc = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == cat.profile_id,
                                                     ProfileChannel.channel_id == ref)).first()
            if pc:
                pc.last_feed_check = now()
                s.add(pc)
                s.commit()
        source = "rss"
    counts = _insert(rt, cat, list(entries or []), source)
    counts.update(source=source)
    rt.event("search", f"➕ {counts['added']} new of {counts['found']} results for {cat.name}"
             + (f" ({counts['skipped']} filtered)" if counts["skipped"] else ""),
             "info", cat, payload={**counts, "query": ref, "kind": kind})
    return counts


class _Skip(Exception):
    """A non-blocking YouTube error for a discovery source (already reported)."""


def _call(rt: Runtime, rl, fn):
    yt = rt.yt
    try:
        out = fn()
    except yt.YtBlocked:
        raise
    except yt.YtError as e:
        rl.failed()
        rt.event("error", f"⚠️ YouTube error: {e}"[:500], "warn", payload={"error": str(e)[:500]})
        raise _Skip(str(e)) from e
    rl.success()
    return out


def _save_channel(ch: Optional[dict]) -> None:
    if not ch or not ch.get("id"):
        return
    with session() as s:
        row = s.get(Channel, ch["id"]) or Channel(id=ch["id"])
        for k in ("name", "handle", "avatar"):
            if ch.get(k):
                setattr(row, k, ch[k])
        row.updated_at = now()
        s.add(row)
        s.commit()

