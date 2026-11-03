"""Rank what the rules let through, then tier it under daily quotas so the front page stays small."""
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlmodel import Session, select

from .. import engine
from ..models import ContentItem, CuratorDiscovery, ItemState

FEATURE_MIN = 60.0   # final score an item needs to be featured (calibrate on real data: V-048)
KEEP_MIN = 45.0      # below this it is set aside
FRESH = 86400.0      # only items found in the last day can be featured
# Most featured items per type in one day; the rest stay browsable but off the front page.
DAILY_FEATURE = {'meme': 8, 'news': 6, 'article': 6}
DAILY_FEATURE_OTHER = 4
PER_PUBLISHER = 2
TASTE_POINTS = 8.0
REPUTATION_POINTS = 15.0


@dataclass
class Taste:
    """Profile-wide signals, loaded once per pass."""
    liked: list[str] = field(default_factory=list)
    disliked: list[str] = field(default_factory=list)
    publishers: dict[str, float] = field(default_factory=dict)  # -1..1 kept vs dismissed


def load_taste(s: Session, pid: int) -> Taste:
    liked, disliked = engine.taste(s, pid)
    kept, dropped = Counter(), Counter()
    rows = s.exec(select(ContentItem.publisher, ItemState.saved, ItemState.favorite, ItemState.rating, ItemState.dismissed)
                  .join(ContentItem, ContentItem.id == ItemState.item_id).where(ItemState.profile_id == pid)).all()
    for publisher, saved, favorite, rating, dismissed in rows:
        if not publisher:
            continue
        if saved or favorite or rating == 1:
            kept[publisher] += 1
        elif dismissed or rating == -1:
            dropped[publisher] += 1
    # Three neutral pseudo-votes keep one save or one dismissal from swinging a source.
    publishers = {p: (kept[p] - dropped[p]) / (kept[p] + dropped[p] + 3) for p in set(kept) | set(dropped)}
    return Taste(liked, disliked, publishers)


def discovery_scores(s: Session, pid: int, ids: list[int]) -> dict[int, float]:
    if not ids:
        return {}
    return {iid: score for iid, score in s.exec(
        select(CuratorDiscovery.item_id, func.max(CuratorDiscovery.score))
        .where(CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.in_(ids))
        .group_by(CuratorDiscovery.item_id)).all() if score is not None}


def relevance(item: ContentItem, scout: float | None, taste: Taste) -> tuple[float, list[str]]:
    score = 50.0 if scout is None else float(scout)
    reasons = []
    words = set(re.split(r'[^a-z0-9]+', f'{item.title} {item.summary}'.lower()))
    up = [w for w in taste.liked if w in words]
    down = [w for w in taste.disliked if w in words]
    if up and not down:
        score += TASTE_POINTS
        reasons.append(f'you keep "{up[0]}"')
    elif down and not up:
        score -= TASTE_POINTS
        reasons.append(f'you dismiss "{down[0]}"')
    rep = taste.publishers.get(item.publisher or '')
    if rep:
        score += REPUTATION_POINTS * rep
        reasons.append('a source you keep' if rep > 0 else 'a source you dismiss')
    return max(0.0, min(100.0, score)), reasons


def final(quality: float, rel: float) -> float:
    return round(0.55 * quality + 0.45 * rel, 1)


@dataclass
class Candidate:
    item: ContentItem
    quality: float
    relevance: float
    final: float
    reasons: list[str]
    tier: str = 'keep'          # forced tier from rules or the user, else decided by tier()
    forced: bool = False
    story_id: int | None = None


def tier(cands: list[Candidate], now: float | None = None) -> None:
    """Assign tiers in place: best fresh items are featured within per-type, per-source and
    per-story limits; weak ones are set aside. Forced tiers (rules, the user) are left alone."""
    now = now or time.time()
    per_type, per_publisher, stories = Counter(), Counter(), set()
    # What the user promoted is on the front page too, so it uses up the same room.
    for c in cands:
        if c.forced and c.tier == 'feature':
            per_type[c.item.type] += 1
            if c.item.publisher:
                per_publisher[c.item.publisher] += 1
            if c.story_id is not None:
                stories.add(c.story_id)
    for c in sorted((c for c in cands if not c.forced), key=lambda c: -c.final):
        if c.final < KEEP_MIN:
            c.tier = 'set_aside'
            c.reasons.append('below the bar')
            continue
        c.tier = 'keep'
        if c.story_id is not None and c.story_id in stories:
            c.reasons.append('folded into its story')
            continue
        # Only a featured item stands for its story: an old or middling lead must not
        # hide fresh coverage that clears the bar.
        if c.final < FEATURE_MIN or now - c.item.first_seen > FRESH:
            continue
        cap = DAILY_FEATURE.get(c.item.type, DAILY_FEATURE_OTHER)
        if per_type[c.item.type] >= cap:
            c.reasons.append('daily limit')
            continue
        if c.item.publisher and per_publisher[c.item.publisher] >= PER_PUBLISHER:
            c.reasons.append('enough from this source today')
            continue
        c.tier = 'feature'
        per_type[c.item.type] += 1
        if c.item.publisher:
            per_publisher[c.item.publisher] += 1
        if c.story_id is not None:
            stories.add(c.story_id)
