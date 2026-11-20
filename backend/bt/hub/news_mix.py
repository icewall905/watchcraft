"""Per-visit For you ordering for News.

Each visit (a seed plus the time it started) gets a different, stable mix: stories
already shown on earlier visits sink by how recently and how often they were seen,
so a rolling week of stock resurfaces unseen reporting instead of the same top page.
Ordering within one visit never depends on impressions recorded during that visit.
"""
import base64
import hashlib
import json
import time
from collections import Counter, deque

from sqlalchemy import func
from sqlmodel import Session, select

from .master.cluster import same_story, title_key
from .models import CuratorDiscovery, NewsImpression

HALF_LIFE_HOURS = 36.0   # freshness halves every 1.5 days across the rolling week
MAX_RECORD = 300
INTEREST_WINDOW = 10     # one interest (curator) may hold at most INTEREST_QUOTA of any 10 consecutive stories
INTEREST_QUOTA = 4       # ... while other interests still have stories to offer
LOOKAHEAD = 40           # strongest remaining stories compared per pick, plus each interest's best
# Community threads about a story are discussion, not another outlet reporting it.
DISCUSSION_PROVIDERS = frozenset({'hackernews', 'lemmy', 'mastodon', 'mastodon_trends', 'lobsters', 'reddit'})


def is_discussion(item: dict) -> bool:
    provider = (item.get('provider') or '').lower()
    return provider in DISCUSSION_PROVIDERS or provider.startswith('reddit') or item.get('type') in ('discussion', 'post')


def coverage(items: list[dict]) -> dict:
    """Honest per-story counts: distinct outlets, all articles, and separate discussion threads."""
    talk = [i for i in items if is_discussion(i)]
    outlets = {(i.get('publisher') or i.get('provider') or '').strip().lower() for i in items if not is_discussion(i)}
    outlets.discard('')
    return {'article_count': len(items), 'publisher_count': len(outlets), 'discussion_count': len(talk)}


def group_rows(rows: list[dict]) -> list[dict]:
    """Fold rows (newest first; id, title, published_at, first_seen, story_id) into stories.

    Stored StoryItem links win; unclustered items fall back to conservative title overlap.
    A token index keeps the fallback near-linear for thousands of items."""
    stored: dict[int, list[dict]] = {}
    loose: list[dict] = []
    for r in rows:
        (stored.setdefault(r['story_id'], []) if r.get('story_id') is not None else loose).append(r)
    groups: list[dict] = [{'_items': members, '_story_id': sid} for sid, members in stored.items()]
    fallback: list[dict] = []
    index: dict[str, list[int]] = {}
    for r in loose:
        key = title_key(r.get('title') or '')
        candidates = sorted({gi for w in key for gi in index.get(w, ())})
        for gi in candidates:
            g = fallback[gi]
            if any(same_story(key, prior) for prior in g['_keys']):
                g['_items'].append(r); g['_keys'].append(key)
                break
        else:
            gi = len(fallback)
            fallback.append({'_keys': [key], '_items': [r], '_story_id': None})
        for w in key:
            slot = index.setdefault(w, [])
            if not slot or slot[-1] != gi:
                slot.append(gi)
    groups.extend(fallback)
    out = []
    for g in groups:
        its = sorted(g['_items'], key=lambda x: -(x.get('published_at') or x.get('first_seen') or 0))
        sid = g['_story_id']
        out.append({'key': f'story:{sid}' if sid is not None else f"item:{min(i['id'] for i in its)}",
                    'story_id': sid, 'title': its[0].get('title') or '', 'items': its,
                    'latest': its[0].get('published_at') or its[0].get('first_seen') or 0})
    out.sort(key=lambda g: (-g['latest'], g['key']))
    return out


def encode_cursor(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')


def decode_cursor(cursor: str) -> dict:
    """Opaque page token back to its fields; ValueError when it was not one of ours."""
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
    except Exception as e:
        raise ValueError('invalid cursor') from e
    if not isinstance(data, dict):
        raise ValueError('invalid cursor')
    return data


def params_tag(*parts) -> str:
    """Ties a cursor to the query that issued it, so a stale token cannot page a different list."""
    return hashlib.blake2b('|'.join(map(str, parts)).encode(), digest_size=6).hexdigest()


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


def _found(item: dict) -> float:
    return item.get('first_seen') or 0


def rank(groups: list[dict], *, seen: dict[int, tuple[float, int]], seed: str, visit_at: float,
         previous_visit: float | None) -> list[dict]:
    """Order story groups for one visit: fresh, unseen, readable and varied by publisher/interest.

    Deterministic for (seed, visit_at): scores use only what existed when the visit began.
    Stories that arrived during the visit follow in arrival order, so later pages never shift."""
    scored, late = [], []
    for g in groups:
        items = sorted(g['items'], key=lambda i: _lead_rank(i, visit_at))
        if all(_read_before(i, visit_at) for i in items):
            continue  # finished stories belong to Latest, not a fresh mix
        base = [i for i in items if _found(i) <= visit_at]
        shown = [seen[i['id']] for i in items if i['id'] in seen]
        found = max(_found(i) for i in items)
        g = {**g, 'items': items, 'seen': bool(shown),
             'new': bool(previous_visit is not None and found > previous_visit and not shown)}
        if not base:
            late.append((min(_found(i) for i in items), g))
            continue
        lead = base[0]
        latest = max((i.get('published_at') or _found(i)) for i in base)
        age_h = max(0.0, (visit_at - latest) / 3600)
        publishers = {i.get('publisher') or i.get('provider') for i in base}
        summary = lead.get('enrichment', {}).get('summary') if isinstance(lead.get('enrichment'), dict) else None
        quality = (1 + (0.25 if isinstance(summary, str) and summary.strip() else 0)
                   + (0.15 if lead.get('local_image') else 0) + 0.1 * min(len(publishers) - 1, 3))
        factor = min((_seen_factor(last, n, visit_at) for last, n in shown), default=1.0)
        score = 0.5 ** (age_h / HALF_LIFE_HOURS) * quality * factor * _jitter(seed, g['key'])
        interests = tuple(sorted({c for i in base for c in (i.get('curator_ids') or ())}))
        scored.append((score, g, lead.get('publisher') or lead.get('provider') or '',
                       tuple(lead.get('curator_ids') or ()), interests))
    scored.sort(key=lambda x: (-x[0], x[1]['key']))
    out = _rotate(scored)
    late.sort(key=lambda x: (x[0], x[1]['key']))
    out.extend(g for _, g in late)
    return out


def _rotate(scored: list[tuple]) -> list[dict]:
    """Greedy pick so one outlet or one interest cannot fill a screen while others have stories.

    Each pick compares the strongest LOOKAHEAD remaining stories plus every interest's best one,
    which keeps the walk near-linear over thousands of stories."""
    n = len(scored)
    taken = [False] * n
    queues: dict[int, deque] = {}
    for k, entry in enumerate(scored):
        for c in entry[4]:
            queues.setdefault(c, deque()).append(k)
    out: list[dict] = []
    recent_pubs: list[str] = []
    recent_curators: list[tuple] = []
    recent_interests: deque = deque()
    in_window: Counter = Counter()
    head = 0

    def adjusted(k: int) -> float:
        score, _, pub, cur, interests = scored[k]
        if pub and pub in recent_pubs[-3:]:
            score *= 0.55
        if cur and cur in recent_curators[-2:]:
            score *= 0.8
        if interests and all(in_window[c] >= INTEREST_QUOTA for c in interests):
            score *= 0.2
        return score

    while len(out) < n:
        while head < n and taken[head]:
            head += 1
        candidates, k = [], head
        while k < n and len(candidates) < LOOKAHEAD:
            if not taken[k]:
                candidates.append(k)
            k += 1
        for q in queues.values():
            while q and taken[q[0]]:
                q.popleft()
            if q:
                candidates.append(q[0])
        best = max(candidates, key=lambda k: (adjusted(k), -k))
        taken[best] = True
        _, g, pub, cur, interests = scored[best]
        out.append(g); recent_pubs.append(pub); recent_curators.append(cur)
        recent_interests.append(interests); in_window.update(interests)
        if len(recent_interests) > INTEREST_WINDOW:
            in_window.subtract(recent_interests.popleft())
    return out
