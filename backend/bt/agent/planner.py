"""Plan (LLM search queries, cached per category) + discover (search / channel tab / RSS) new candidates."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import select

from ..db import get_kv, save_setting, session
from ..models import Candidate, Category, Channel, ProfileChannel, now
from . import channel_sources, data, prompts
from .runtime import Interrupt, Runtime, iso, parse_ts

REPLAN_MIN_S = 1800  # never re-plan a category more often than this
PLAN_MAX_AGE_S = 12 * 3600  # re-plan when the cached plan is older than this
TRIED_KEEP = 40
PLAN_VERSION = 2  # subscription sources now require a playlist-specific selection
CHANNEL_N = 30
CHANNEL_DEPTH_MAX = 90  # newest-first channel tab: how far back a dry channel source pages
REST_BASE_S = 3600  # a source that found nothing new at every depth rests 1h, 2h, 4h ... 48h
REST_MAX_S = 48 * 3600
RSS_REST_MAX_S = 3 * 3600  # subscription feeds are cheap and freshness matters most there
YIELD_KEEP = 300


def source_key(kind: str, ref: str) -> str:
    return f"{kind}:{(ref or '').strip().lower()}"


def source_state(plan: dict, kind: str, ref: str) -> dict:
    return (plan.get("yield") or {}).get(source_key(kind, ref)) or {}


def resting(plan: dict, kind: str, ref: str, at: Optional[datetime] = None) -> Optional[datetime]:
    """When a source keeps returning only already-known results, it rests instead of being re-fetched."""
    until = parse_ts(source_state(plan, kind, ref).get("until"))
    return until if until and until > (at or now()) else None


def record_yield(plan: dict, kind: str, ref: str, new: int, step: int, max_depth: int,
                 max_rest_s: int = REST_MAX_S) -> dict:
    """YouTube always returns the same top results, so a dry source pages deeper on its next turn;
    past `max_depth` it goes back to the top and rests with exponential backoff. After a rest only the
    top is re-checked (deep pages cost one request per page). New results reset it."""
    stats = plan.setdefault("yield", {})
    key = source_key(kind, ref)
    st = stats.pop(key, None) or {"dry": 0, "rests": 0, "depth": 0, "until": None}
    if new > 0:
        st.update(dry=0, rests=0, until=None)
    else:
        st["dry"] += 1
        if step and not st["rests"] and st["depth"] + step <= max_depth:
            st["depth"] += step
        else:
            st["depth"] = 0
            st["until"] = iso(now() + timedelta(seconds=min(max_rest_s, REST_BASE_S * 2 ** st["rests"])))
            st["rests"] += 1
    st["at"] = iso(now())
    stats[key] = st  # most recently used last; trim the oldest
    for old in list(stats)[:-YIELD_KEEP]:
        del stats[old]
    return st


def plan_key(cat_id: int) -> str:
    return f"plan:{cat_id}"


def plan_context(cat: Category) -> str:
    """A changed playlist brief or source constraint needs a fresh source selection."""
    fields = ("profile_id", "name", "kind", "brief", "seed_queries", "seed_channels", "include_keywords",
              "exclude_keywords", "min_duration_s", "max_duration_s", "language", "kid_safe")
    return hashlib.sha256(json.dumps({k: getattr(cat, k) for k in fields}, sort_keys=True).encode()).hexdigest()


def load_plan(cat_id: int) -> dict:
    p = get_kv(plan_key(cat_id), {}) or {}
    p.setdefault("queries", [])
    p.setdefault("channels", [])
    p.setdefault("tried", [])
    p.setdefault("cursor", 0)
    p.setdefault("planned_at", None)
    p["channel_failures"] = channel_sources.active(p)
    with session() as s:
        cat = s.get(Category, cat_id)
    if p.get("version") != PLAN_VERSION or (cat and p.get("context") != plan_context(cat)):
        p.update(version=PLAN_VERSION, cursor=0, planned_at=None, queries=[], channels=[], subscriptions=[])
        if cat:
            p["context"] = plan_context(cat)
    return p


def save_plan(cat_id: int, plan: dict) -> None:
    plan["version"] = PLAN_VERSION
    if "context" not in plan:
        with session() as s:
            cat = s.get(Category, cat_id)
        if cat:
            plan["context"] = plan_context(cat)
    save_setting(plan_key(cat_id), plan)


def sources(cat: Category, plan: dict, subs: list[str], *, include_resting: bool = False) -> list[tuple[str, str]]:
    """Ordered rotation of discovery sources: (kind, ref) with kind in query|channel|rss."""
    out, seen = [], set()

    def add(kind, ref):
        ref = (ref or "").strip()
        key = (kind, ref.lower())
        if ref and key not in seen and (include_resting or not resting(plan, kind, ref)):
            seen.add(key)
            out.append((kind, ref))

    qs = [q for q in (plan.get("queries") or []) if not prompts.future_dated(q)]
    chans = [ref for ref in list(cat.seed_channels or []) + list(plan.get("channels") or [])
             if channel_sources.available(cat, plan, ref)]
    seeds = [q for q in (cat.seed_queries or []) if not prompts.future_dated(q)]
    # Profile subscriptions are suggestions for the planner, not sources for every playlist.
    eligible = set(subs)
    selection = plan.get("subscriptions") or []
    selected = [ref for ref in selection if isinstance(ref, str) and ref in eligible
                and channel_sources.available(cat, plan, ref)][:4] if isinstance(selection, list) else []
    # interleave: planner query, seed query, channel, rss ...
    for i in range(max(len(qs), len(seeds), len(chans), len(selected))):
        if i < len(qs):
            add("query", qs[i])
        if i < len(seeds):
            add("query", seeds[i])
        if i < len(chans):
            add("channel", chans[i])
        if i < len(selected):
            add("channel" if cat.kind == "shorts" else "rss", selected[i])
    return out


def make_plan(rt: Runtime, cat: Category, plan: dict) -> bool:
    """Ask the LLM for fresh queries. Returns True on success (plan updated in place)."""
    with session() as s:
        from .memory import select_memory
        core, lessons = select_memory(s, cat.profile_id, data.lessons_for(s, cat))
        unavailable = channel_sources.context(plan)
        msgs = prompts.planner_messages(
            cat, lessons, data.recent_titles(s, cat.id, "accepted"),
            data.rejections_for(s, cat.id, cat.profile_id), data.recent_dislikes(s, cat.profile_id),
            plan.get("tried", [])[-TRIED_KEEP:], data.queue_context(s, cat.id, cat.profile_id), core=core,
            search_yield=data.discovery_yield(s, cat.id), subscriptions=data.subscription_context(s, cat.profile_id),
            unavailable_channels=unavailable, known_channels=data.channel_alternatives(s, unavailable))
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
    # Re-running a tried query only helps once it has rested (it then restarts at the top).
    fresh = [q for q in queries if q.lower() not in tried] or [q for q in queries if not resting(plan, "query", q)]
    channels = [c.strip() for c in out.get("channels") or [] if isinstance(c, str) and c.strip()]
    suppressed = [ref for ref in channels if not channel_sources.available(cat, plan, ref)
                  or channel_sources.key(cat, ref) is None]
    channels = [ref for ref in channels if ref not in suppressed]
    with session() as s:
        eligible = {pc.channel_id for pc in data.subscribed_channels(s, cat.profile_id)}
    selected = out.get("subscriptions") or []
    subscriptions = list(dict.fromkeys(ref for ref in selected if isinstance(ref, str) and ref in eligible
                                     and channel_sources.available(cat, plan, ref)))[:4] if isinstance(selected, list) else []
    plan.update(queries=fresh[:6], channels=channels[:4], subscriptions=subscriptions,
                version=PLAN_VERSION, context=plan_context(cat), planned_at=iso(now()), cursor=0)
    rt.event("plan", f"🧭 Plan for {cat.name}: " + "; ".join(f'"{q}"' for q in plan["queries"]), "info", cat,
             payload={"queries": plan["queries"], "channels": plan["channels"], "subscriptions": subscriptions,
                      "suppressed_channels": suppressed})
    return True


def _screen(rt: Runtime, cat: Category, entries: list[dict]) -> dict[str, str]:
    """Cheap relevance pass on titles before research: {video_id: why} for clearly off-topic results.
    Fails open: without an answer every result goes on to the full review."""
    if not entries or not rt.llm_ok():
        return {}
    try:
        out = rt.llm.chat_json(prompts.screen_messages(cat, entries), json_schema=prompts.SCREEN_SCHEMA,
                               schema_name="screen", max_tokens=1500, temperature=0.1, abort=rt.check, role="worker")
    except Interrupt:
        raise
    except Exception as e:  # noqa: BLE001
        rt.llm_failed(f"screening results for {cat.name}", e, category=cat)
        return {}
    off = {}
    for row in out.get("off_topic") or []:
        n = row.get("n") if isinstance(row, dict) else None
        if isinstance(n, int) and 1 <= n <= len(entries):
            off[entries[n - 1]["id"]] = str(row.get("why") or "").strip()[:200] or "not about this playlist"
    return off


def _insert(rt: Runtime, cat: Category, entries: list[dict], source: str) -> dict:
    """Prefilter, screen and insert found candidates. Returns counts."""
    hide_shorts = bool(rt.settings()["extras"].get("hide_shorts", True))
    skipped, reasons, keep = 0, {}, []

    def skip(why):
        nonlocal skipped
        skipped += 1
        reasons[why.split(" (")[0]] = reasons.get(why.split(" (")[0], 0) + 1

    with session() as s:
        ctx = data.profile_ctx(s, cat.profile_id)
        existing = data.candidate_ids(s, cat.id)
        rejected = data.rejected_titles(s, cat.id)
        for e in entries:
            why = data.prefilter(e, cat, ctx, hide_shorts, existing, rejected)
            if why:
                skip(why)
            else:
                keep.append(e)
                existing.add(e["id"])
    off = _screen(rt, cat, keep)
    added, screened, new = 0, 0, []
    with session() as s:
        room = data.FOUND_CAP - data.status_counts(s, cat.id).get("found", 0)
        for e in keep:
            if e["id"] not in off and room <= 0:
                skip("backlog full")
                continue
            rt.yt.upsert_video(s, e)
            cand = Candidate(category_id=cat.id, video_id=e["id"], status="found", source=source)
            if e["id"] in off:
                # Recorded so the same result is not screened again and planning sees the poor yield.
                cand.status, cand.red_flags, cand.judged_at = "rejected", [f"off-topic: {off[e['id']]}"], now()
                screened += 1
            else:
                new.append(cand)
                added += 1
                room -= 1
            s.add(cand)
        s.commit()
        ids = [c.id for c in new]
    return {"found": len(entries), "added": added, "skipped": skipped, "screened": screened,
            "skip_reasons": reasons, "candidate_ids": ids}


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
        rests = [u for kind, ref in sources(cat, plan, subs, include_resting=True) if (u := resting(plan, kind, ref))]
        if rests:
            # Every source returned only known results at every depth; wait for new uploads.
            until = iso(min(rests))
            if plan.get("rest_noted") != until:
                plan["rest_noted"] = until
                save_plan(cat.id, plan)
                rt.event("plan", f"😴 Every source for {cat.name} is exhausted for now; checking again after {until}",
                         "info", cat, payload={"until": until})
            return None
        detail = ("channel sources are temporarily unavailable; add a search query or another channel"
                  if channel_sources.active(plan) else "add seed queries or check the LLM")
        rt.event("plan", f"🤷 No usable sources for {cat.name} yet ({detail})", "warn", cat)
        return None
    i = plan["cursor"] % len(srcs)
    kind, ref = srcs[i]
    plan["cursor"] = i + 1
    rt.check()
    th = rt.throttle()
    with session() as s:
        data.touch_category(s, cat.id)
    yt = rt.yt
    start = int(source_state(plan, kind, ref).get("depth") or 0)
    step, max_depth = 0, 0
    if kind == "query":
        rl.wait_turn("search", cat)
        rt.state("searching", f'Searching "{ref}"', cat, query=ref)
        rt.event("search", f'🔎 Searching "{ref}" for {cat.name}', "info", cat, payload={"query": ref})
        if ref not in plan["tried"]:
            plan["tried"] = (plan["tried"] + [ref])[-TRIED_KEEP:]
        save_plan(cat.id, plan)
        n = int(th.get("search_results") or 15)
        shorts = cat.kind == "shorts"
        entries = _call(rt, rl, lambda: yt.search(ref, n=n, origin="worker", start=start, shorts=shorts))
        step, max_depth = n, yt.SEARCH_DEPTH_MAX
        source = f"search: {ref}"
    elif kind == "channel":
        rl.wait_turn("channel", cat)
        rt.state("searching", f"Browsing channel {ref}", cat, query=ref)
        rt.event("search", f"📺 Browsing channel {ref} for {cat.name}", "info", cat, payload={"channel": ref})
        save_plan(cat.id, plan)
        tab = "shorts" if cat.kind == "shorts" else None
        step, max_depth = CHANNEL_N, CHANNEL_DEPTH_MAX
        try:
            res = _call(rt, rl, lambda: yt.channel_videos(ref, n=CHANNEL_N, origin="worker", tab=tab, start=start))
        except _Skip as e:
            if isinstance(e.__cause__, yt.YtUnavailable):
                _channel_unavailable(rt, cat, plan, ref)
            if ref in plan.get("channels", []):
                plan["channels"] = [c for c in plan["channels"] if c != ref]
                save_plan(cat.id, plan)
            raise e
        ch, entries = res if isinstance(res, tuple) else ({}, res)
        channel_sources.succeeded(cat, plan, ref)
        save_plan(cat.id, plan)
        _save_channel(ch)
        source = f"channel: {ch.get('name') or ref}"
    else:  # rss
        rl.wait_turn("rss", cat)
        rt.state("searching", f"Checking subscription feed {ref}", cat, query=ref)
        rt.event("rss", f"📰 Checking new uploads from subscribed channel {ref}", "info", cat,
                 payload={"channel_id": ref})
        save_plan(cat.id, plan)
        def rss_or_channel():
            try:
                return rt.feeds.channel_feed(ref)
            except yt.YtUnavailable:
                # A public channel can work while its RSS endpoint returns 404.
                # This is one bounded fallback, with its own channel request budget.
                rl.failed()
                with session() as s:
                    pc = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == cat.profile_id,
                                                             ProfileChannel.channel_id == ref)).first()
                    if pc:
                        pc.last_feed_check = now()
                        s.add(pc)
                        s.commit()
                rl.wait_turn("channel", cat)
                rt.event("rss", f"RSS unavailable for {ref}; checking its public channel tab", "info", cat)
                if cat.kind == "shorts":
                    return yt.channel_videos(ref, n=30, origin="worker", tab="shorts")
                return yt.channel_videos(ref, n=30, origin="worker")

        try:
            res = _call(rt, rl, rss_or_channel)
        except _Skip as e:
            if isinstance(e.__cause__, yt.YtUnavailable):
                _channel_unavailable(rt, cat, plan, ref)
            raise
        ch, entries = res if isinstance(res, tuple) else ({}, res)
        with session() as s:
            pc = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == cat.profile_id,
                                                     ProfileChannel.channel_id == ref)).first()
            if pc:
                pc.last_feed_check = now()
                s.add(pc)
                s.commit()
        source = f"rss: {ref}"
    counts = _insert(rt, cat, list(entries or []), source)
    # Unseen results, including ones the screen rejected or the full backlog postponed.
    new = counts["added"] + counts["screened"] + counts["skip_reasons"].get("backlog full", 0)
    st = record_yield(plan, kind, ref, new, step, max_depth, RSS_REST_MAX_S if kind == "rss" else REST_MAX_S)
    if st["until"]:
        plan["cursor"] = i  # this source leaves the rotation; the next one moves into its slot
    save_plan(cat.id, plan)
    counts.update(source=source, start=start, next_start=st["depth"], resting_until=st["until"])
    rt.event("search", f"➕ {counts['added']} new of {counts['found']} results for {cat.name}"
             + (f" ({counts['skipped']} filtered)" if counts["skipped"] else "")
             + (f" ({counts['screened']} off-topic)" if counts["screened"] else ""),
             "info", cat, payload={**counts, "query": ref, "kind": kind})
    return counts


def _channel_unavailable(rt: Runtime, cat: Category, plan: dict, ref: str) -> None:
    retry_at = channel_sources.unavailable(cat, plan, ref)
    save_plan(cat.id, plan)
    if retry_at:
        rt.event("plan", f"Temporarily skipping unavailable channel {ref} for {cat.name}; retry after {retry_at}",
                 "warn", cat, payload={"channel": channel_sources.key(cat, ref), "retry_at": retry_at})


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
