"""What the owner enjoys, learned from what they do rather than what the judge thought.

engagement(): one -1..1 score per pick from votes, stars, reasons, opens, dwell, link clicks, plays, comment
reading and quiet skips. The hourly sweep stores it on CRPick.engaged and derives:
- priors: smoothed enjoyment per kind, community, platform and source, relative to the owner's average, which
  nudge the keep rule, the pre-judge ranking and the feed order;
- per-source counts on CRThreadSource (shown/liked/skipped) and a throttle for sources the owner keeps skipping
  (polled less, held to a higher bar; never removed; the owner can undo it);
- once a day, a short taste memo the judge reads: rules distilled from the most and least enjoyed picks of all
  the owner's subs, because what is fun travels across subs."""
import logging
import math
import time

from sqlalchemy import func
from sqlmodel import select

from ..db import get_kv, save_setting, session
from .models import CREvent, CRFeedback, CRPick, CRPost, CRSourcePost, CRThread, CRThreadSource

log = logging.getLogger(__name__)
SWEEP_EVERY_S = 3600
MEMO_EVERY_S = 86400
WINDOW_S = 30 * 86400
SKIP_AFTER_S = 2 * 86400      # seen this long ago and never touched: quietly skipped
PRIOR_N = 10                  # picks of pseudo-evidence at the owner's average before a group counts
PRIOR_SCALE = 25              # keep-rule points per unit of predicted engagement ...
PRIOR_MAX = 10                # ... capped both ways
LIKED = 0.15                  # engagement at or above this counts as enjoyed
SKIPPED = -0.1                # at or below this counts as skipped / disliked
THROTTLE_MIN_SHOWN = 15
THROTTLE_STEPS = ((-0.5, 3), (-0.35, 2), (-0.2, 1))   # mean engagement → throttle level
THROTTLE_PENALTY = 5          # keep-rule points per throttle level
OWNER_KEPT = 'kept by you'    # throttle_why of a source the owner un-throttled: the sweep leaves it alone
MEMO_MIN = 8                  # engaged picks needed before a memo is written
MEMO_SIDE = 30
GOOD_REASONS = {'funny', 'great discussion', 'learned something', 'more like this'}
_last = {'sweep': 0.0}


def engagement(verdict: str, reasons: list | None, saved: bool, opened: bool, dwell_ms: int, counts: dict,
               seen_at: float, now: float) -> float:
    if verdict == 'down':
        return -1.0
    if verdict == 'meh':
        return -0.6
    e = 0.0
    if verdict == 'up':
        e += 1.0
    if saved:
        e += 0.9
    if GOOD_REASONS & set(reasons or ()):
        e += 0.2
    if opened:
        e += 0.2
    e += min(0.6, 0.3 * min(1, counts.get('out', 0)) + 0.3 * min(1, counts.get('play', 0))
             + 0.3 * min(1, counts.get('comments', 0)) + 0.2 * min(1, counts.get('article', 0)))
    if dwell_ms:
        e += min(0.4, 0.4 * math.log1p(dwell_ms / 1000) / math.log1p(60))
    if e == 0 and seen_at and now - seen_at > SKIP_AFTER_S:
        e = -0.15
    return max(-1.0, min(1.0, round(e, 3)))


def _shrunk(groups: dict[str, list[float]], mean: float) -> dict[str, float]:
    """Each group's lift over the owner's average, pulled toward zero until it has evidence."""
    return {k: round((sum(v) - mean * len(v)) / (len(v) + PRIOR_N), 4) for k, v in groups.items() if k}


def priors(pid: int, s=None) -> dict:
    return get_kv(f'commonroom:joy:{pid}', {}, s=s)


def predict(pri: dict, kind: str = '', community: str = '', platform: str = '', sources=()) -> float:
    """Predicted lift in engagement for a post, from the learned priors (0 without evidence)."""
    if not pri:
        return 0.0
    src = [pri.get('source', {}).get(str(x), 0.0) for x in sources or ()]
    return (pri.get('kind', {}).get(kind or '', 0.0) + pri.get('community', {}).get((community or '').lower(), 0.0)
            + 0.5 * pri.get('platform', {}).get(platform or '', 0.0) + (sum(src) / len(src) if src else 0.0))


def bonus(pri: dict, **kw) -> float:
    """Keep-rule points from the priors: at most PRIOR_MAX either way."""
    return max(-PRIOR_MAX, min(PRIOR_MAX, PRIOR_SCALE * predict(pri, **kw)))


def throttle_level(shown: int, mean: float, current: int) -> int:
    if shown < THROTTLE_MIN_SHOWN:
        return 0
    for cut, level in THROTTLE_STEPS:
        if mean <= cut:
            return level
    return 0 if mean >= 0 else current   # between: hold, so a source does not flap


def _profile(s, pid: int, now: float) -> dict:
    picks = list(s.exec(select(CRPick, CRPost.community, CRPost.platform).join(CRPost, CRPost.id == CRPick.post_id)
                        .where(CRPick.profile_id == pid, CRPick.picked_at >= now - WINDOW_S)).all())
    if not picks:
        return {}
    ids = [k.id for k, _, _ in picks]
    fb = {(f.thread_id, f.post_id): f for f in s.exec(select(CRFeedback).where(CRFeedback.profile_id == pid)).all()}
    counts: dict[int, dict] = {}
    for pick_id, kind, n in s.exec(select(CREvent.pick_id, CREvent.kind, func.count()).where(
            CREvent.profile_id == pid, CREvent.pick_id.in_(ids)).group_by(CREvent.pick_id, CREvent.kind)).all():
        counts.setdefault(pick_id, {})[kind] = n
    srcs: dict[int, list[int]] = {}
    for sid, post_id in s.exec(select(CRSourcePost.source_id, CRSourcePost.post_id).where(
            CRSourcePost.post_id.in_([k.post_id for k, _, _ in picks]))).all():
        srcs.setdefault(post_id, []).append(sid)
    rows = []
    for k, community, platform in picks:
        f = fb.get((k.thread_id, k.post_id))
        e = engagement(f.verdict if f else '', f.reasons if f else [], k.saved, bool(k.opened_at), k.dwell_ms,
                       counts.get(k.id, {}), k.seen_at, now)
        touched = bool(k.seen_at or f or k.opened_at or k.saved or counts.get(k.id))
        if abs((k.engaged or 0) - e) > 1e-6:
            k.engaged = e
            s.add(k)
        if touched:
            rows.append((k, e, (community or '').lower(), platform or '', srcs.get(k.post_id, [])))
    if not rows:
        return {}
    mean = sum(r[1] for r in rows) / len(rows)
    by: dict[str, dict[str, list[float]]] = {'kind': {}, 'community': {}, 'platform': {}, 'source': {}}
    for k, e, community, platform, sids in rows:
        by['kind'].setdefault(k.kind, []).append(e)
        by['community'].setdefault(community, []).append(e)
        by['platform'].setdefault(platform, []).append(e)
        for sid in sids:
            by['source'].setdefault(str(sid), []).append(e)
    week = [r for r in rows if r[0].picked_at >= now - 7 * 86400]
    out = {name: _shrunk(groups, mean) for name, groups in by.items()}
    out.update({'at': now, 'mean': round(mean, 4), 'n': len(rows),
                'hit_rate': round(sum(1 for r in week if r[1] >= LIKED) / len(week), 3) if week else None,
                'week': len(week)})
    _sources(s, pid, rows)
    return out


def _sources(s, pid: int, rows: list):
    """shown/liked/skipped per (sub, source) and the throttle that follows from them."""
    stats: dict[tuple[int, int], list[float]] = {}
    for k, e, _, _, sids in rows:
        for sid in sids:
            stats.setdefault((k.thread_id, sid), []).append(e)
    tids = list(s.exec(select(CRThread.id).where(CRThread.profile_id == pid)).all())
    for link in s.exec(select(CRThreadSource).where(CRThreadSource.thread_id.in_(tids or [0]))).all():
        es = stats.get((link.thread_id, link.source_id), [])
        shown, liked, skipped = len(es), sum(1 for e in es if e >= LIKED), sum(1 for e in es if e <= SKIPPED)
        level, why = link.throttle, link.throttle_why
        if why != OWNER_KEPT:
            mean = sum(es) / shown if shown else 0.0
            level = throttle_level(shown, mean, link.throttle)
            why = f'you skipped {skipped} of the last {shown} posts' if level else ''
        if (shown, liked, skipped, level, why) != (link.shown, link.liked, link.skipped, link.throttle, link.throttle_why):
            link.shown, link.liked, link.skipped, link.throttle, link.throttle_why = shown, liked, skipped, level, why
            s.add(link)


def throttles(s, thread_id: int) -> dict[int, int]:
    """source_id → throttle level for one sub's enabled sources."""
    return dict(s.exec(select(CRThreadSource.source_id, CRThreadSource.throttle).where(
        CRThreadSource.thread_id == thread_id, CRThreadSource.enabled == True,  # noqa: E712
        CRThreadSource.throttle > 0)).all())


def penalty(post_sources: list[int], throttled: dict[int, int]) -> int:
    """Extra keep-rule points a post needs when every source of this sub that brought it is throttled."""
    if not post_sources or not throttled:
        return 0
    levels = [throttled.get(sid, 0) for sid in post_sources]
    return THROTTLE_PENALTY * min(levels)


def sweep(now: float | None = None) -> int:
    """Hourly: refresh engagement, priors and source throttles for every profile; daily: the taste memo."""
    now = now or time.time()
    if now - _last['sweep'] < SWEEP_EVERY_S:
        return 0
    _last['sweep'] = now
    with session() as s:
        pids = list(s.exec(select(CRThread.profile_id).distinct()).all())
    done = 0
    for pid in pids:
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            pri = _profile(s, pid, now)
            s.commit()
        if pri:
            save_setting(f'commonroom:joy:{pid}', pri)
            done += 1
        try:
            distill(pid, now)
        except Exception:  # noqa: BLE001 — retried tomorrow
            log.exception('Common Room taste memo failed for profile %s', pid)
    return done


# ---------- taste memo

MEMO_SCHEMA = {'type': 'object', 'properties': {'rules': {'type': 'array', 'items': {'type': 'string'},
                                                           'maxItems': 12}}, 'required': ['rules']}
MEMO_SYSTEM = ('You study what one person enjoys in their personal social feed, which replaces browsing Reddit. '
               'You get posts they clearly enjoyed (voted up, starred, opened and read, followed links, watched) '
               'and posts they skipped, found boring or voted down. Write 6-12 short, concrete rules (max 20 '
               'words each) about what makes a post fun FOR THIS PERSON and what bores them: formats, tone, '
               'kinds (showcase, humor, discussion, news...), depth, communities, kinds of conversation. Prefer '
               'patterns that hold across several posts over single topics. No generic advice.')


def memo(pid: int, s=None) -> dict:
    return get_kv(f'commonroom:taste:{pid}', {}, s=s)


def distill(pid: int, now: float | None = None, force: bool = False) -> list[str]:
    from ..hub import ai
    now = now or time.time()
    old = memo(pid)
    if (not force and now - float(old.get('at') or 0) < MEMO_EVERY_S) or not ai.enabled():
        return old.get('rules') or []
    with session() as s:
        base = select(CRPick, CRPost, CRThread.name).join(CRPost, CRPost.id == CRPick.post_id) \
            .join(CRThread, CRThread.id == CRPick.thread_id) \
            .where(CRPick.profile_id == pid, CRPick.picked_at >= now - WINDOW_S)
        good = list(s.exec(base.where(CRPick.engaged >= LIKED).order_by(CRPick.engaged.desc(), CRPick.id.desc())
                           .limit(MEMO_SIDE)).all())
        bad = list(s.exec(base.where(CRPick.engaged <= SKIPPED).order_by(CRPick.engaged, CRPick.id.desc())
                          .limit(MEMO_SIDE)).all())
    if len(good) + len(bad) < MEMO_MIN or not good:
        return old.get('rules') or []

    def row(pick, post, sub, enjoyed):
        return {'type': 'enjoyed' if enjoyed else 'skipped', 'title': post.title,
                'publisher': f'{post.platform} {post.community} · sub {sub} · {pick.kind or "?"}'.strip(),
                'summary': ((post.meta or {}).get('summary') or post.body or '')[:300], 'url': post.url,
                **({'comments': post.comments} if post.comments else {})}
    items = [row(*g, True) for g in good] + [row(*b, False) for b in bad]
    res = ai._call(MEMO_SYSTEM, ai._data(items), MEMO_SCHEMA, max_tokens=900, role='commonroom_curate')
    rules = [' '.join(str(r).split())[:200] for r in (res.get('rules') or [])[:12] if str(r).strip()] \
        if isinstance(res, dict) else []
    if rules:
        save_setting(f'commonroom:taste:{pid}', {**old, 'rules': rules, 'at': now})
    return rules or old.get('rules') or []


def set_owner_note(pid: int, text: str):
    save_setting(f'commonroom:taste:{pid}', {**memo(pid), 'owner': ' '.join(text.split())[:1500]})


def prompt_lines(pid: int, s=None) -> str:
    """The memo and learned kind lifts as judge guidance (inside <data>: the rules quote web titles)."""
    m, pri = memo(pid, s=s), priors(pid, s=s)
    out = []
    if (m.get('owner') or '').strip():
        out.append("The owner's own words about what they enjoy (these win over anything else): " + m['owner'])
    if m.get('rules'):
        out.append('What this owner enjoys, learned from what they actually read, star and skip:\n- '
                   + '\n- '.join(m['rules']))
    kinds = sorted(((k, v) for k, v in (pri.get('kind') or {}).items() if abs(v) >= 0.02), key=lambda t: -t[1])
    if kinds:
        word = lambda v: 'loves' if v >= 0.1 else 'likes' if v > 0 else 'mostly skips' if v <= -0.1 else 'is lukewarm on'  # noqa: E731
        out.append('By kind, the owner ' + '; '.join(f'{word(v)} {k}' for k, v in kinds) + '.')
    return '\n'.join(out)
