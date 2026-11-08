"""Per-visit For you ordering for News.

Each visit (a seed plus the time it started) gets a different, stable mix: stories
already shown on earlier visits sink by how recently and how often they were seen,
so a rolling week of stock resurfaces unseen reporting instead of the same top page.
Ordering within one visit never depends on impressions recorded during that visit.
"""
import hashlib
import time

from sqlalchemy import func
from sqlmodel import Session, select

from .models import CuratorDiscovery, NewsImpression

HALF_LIFE_HOURS = 36.0   # freshness halves every 1.5 days across the rolling week
MAX_RECORD = 300


def record(s: Session, pid: int, item_ids: list[int], visit_at: float, now: float | None = None) -> int:
    """Store impressions for items this profile can see; one visit counts once per item."""
    now = time.time() if now is None else now
    visit_at = min(max(visit_at, now - 7 * 86400), now)
    ids = list(dict.fromkeys(int(i) for i in item_ids))[:MAX_RECORD]
    if not ids:
        return 0
    allowed = set(s.exec(select(CuratorDiscovery.item_id).where(
        CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.in_(ids))).all())
    existing = {r.item_id: r for r in s.exec(select(NewsImpression).where(
        NewsImpression.profile_id == pid, NewsImpression.item_id.in_(allowed))).all()}
    for iid in allowed:
        row = existing.get(iid)
        if row is None:
            s.add(NewsImpression(profile_id=pid, item_id=iid, first_shown=now, last_shown=now, visits=1))
            continue
        if row.last_shown < visit_at:
            row.prev_shown = row.last_shown
            row.visits += 1
        row.last_shown = now
        s.add(row)
    s.commit()
    return len(allowed)


def prior(s: Session, pid: int, item_ids: list[int], visit_at: float) -> tuple[dict[int, tuple[float, int]], float | None]:
    """Impressions from before this visit, plus when the previous visit last showed anything."""
    seen: dict[int, tuple[float, int]] = {}
    for chunk in range(0, len(item_ids), 900):
        rows = s.exec(select(NewsImpression).where(
            NewsImpression.profile_id == pid, NewsImpression.item_id.in_(item_ids[chunk:chunk + 900]))).all()
        for r in rows:
            if r.last_shown < visit_at:
                seen[r.item_id] = (r.last_shown, r.visits)
            elif r.first_shown < visit_at and r.prev_shown:
                # Re-shown during this visit: rank by what was known when the visit began.
                seen[r.item_id] = (r.prev_shown, max(1, r.visits - 1))
    previous = s.exec(select(func.max(NewsImpression.last_shown)).where(
        NewsImpression.profile_id == pid, NewsImpression.last_shown < visit_at)).one()
    touched = s.exec(select(func.max(NewsImpression.prev_shown)).where(
        NewsImpression.profile_id == pid, NewsImpression.last_shown >= visit_at,
        NewsImpression.prev_shown < visit_at)).one()
    previous = max((t for t in (previous, touched) if t), default=None)
    return seen, previous


def _jitter(seed: str, key: str) -> float:
    digest = hashlib.blake2b(f'{seed}|{key}'.encode(), digest_size=8).digest()
    return 0.7 + 0.6 * (int.from_bytes(digest, 'big') / 2 ** 64)


def _read_before(item: dict, visit_at: float) -> bool:
    # Reads during this visit must not reorder it: returning from an article keeps the same list.
    read_at = (item.get('state') or {}).get('read_at')
    return bool(read_at) and read_at < visit_at


def _lead_rank(item: dict, visit_at: float):
    summary = item.get('enrichment', {}).get('summary') if isinstance(item.get('enrichment'), dict) else None
    return (_read_before(item, visit_at), not (isinstance(summary, str) and summary.strip()), not item.get('local_image'),
            -(item.get('published_at') or item.get('first_seen') or 0))


def _seen_factor(last_shown: float, visits: int, visit_at: float) -> float:
    hours = max(0.0, (visit_at - last_shown) / 3600)
    base = 0.12 if hours < 6 else 0.25 if hours < 24 else 0.5
    return max(0.03, base * 0.7 ** max(0, visits - 1))


def rank(groups: list[dict], *, seen: dict[int, tuple[float, int]], seed: str, visit_at: float,
         previous_visit: float | None) -> list[dict]:
    """Order story groups for one visit: fresh, unseen, readable and varied by publisher/interest."""
    scored = []
    for g in groups:
        items = sorted(g['items'], key=lambda i: _lead_rank(i, visit_at))
        if all(_read_before(i, visit_at) for i in items):
            continue  # finished stories belong to Latest, not a fresh mix
        lead = items[0]
        age_h = max(0.0, (visit_at - (g.get('latest') or visit_at)) / 3600)
        publishers = {i.get('publisher') or i.get('provider') for i in items}
        summary = lead.get('enrichment', {}).get('summary') if isinstance(lead.get('enrichment'), dict) else None
        quality = (1 + (0.25 if isinstance(summary, str) and summary.strip() else 0)
                   + (0.15 if lead.get('local_image') else 0) + 0.1 * min(len(publishers) - 1, 3))
        shown = [seen[i['id']] for i in items if i['id'] in seen]
        factor = min((_seen_factor(last, n, visit_at) for last, n in shown), default=1.0)
        found = max((i.get('first_seen') or 0) for i in items)
        g = {**g, 'items': items, 'seen': bool(shown),
             'new': bool(previous_visit is not None and found > previous_visit and not shown)}
        score = 0.5 ** (age_h / HALF_LIFE_HOURS) * quality * factor * _jitter(seed, g['key'])
        scored.append((score, g, lead.get('publisher') or lead.get('provider') or '', tuple(lead.get('curator_ids') or ())))
    scored.sort(key=lambda x: (-x[0], x[1]['key']))
    # Greedy rotation over the strongest candidates so one outlet or interest cannot fill a screen.
    pool, tail = scored[:400], scored[400:]
    out: list[dict] = []
    recent_pubs: list[str] = []
    recent_curators: list[tuple] = []
    while pool:
        def adjusted(entry):
            score, _, pub, cur = entry
            if pub and pub in recent_pubs[-3:]:
                score *= 0.55
            if cur and cur in recent_curators[-2:]:
                score *= 0.8
            return score
        best = max(range(len(pool)), key=lambda k: (adjusted(pool[k]), -k))
        _, g, pub, cur = pool.pop(best)
        out.append(g); recent_pubs.append(pub); recent_curators.append(cur)
    out.extend(g for _, g, _, _ in tail)
    return out
