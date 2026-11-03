"""Fold copies: memes get their near-duplicate check before they can be shown, and news items
join stories, so one event from six outlets is one entry with "+5 sources", not six cards."""
import math
import re
import time

from sqlmodel import Session, select

from .. import ai, memes, semantic
from ..models import CaptureJob, ContentItem, ItemVector, MemeExample, Story, StoryItem

STORY_TYPES = ('news', 'article')
STORY_WINDOW = 72 * 3600   # a story stays open to new coverage this long after its latest item
STORY_CANDIDATES = 300      # open stories compared per item
VECTOR_SAME = 0.88          # cosine above which two headlines describe the same event
CAPTURE_GRACE = 2 * 3600    # a meme whose capture never finishes is analyzed anyway after this

_STORY_FILLER = ai.STOP | {
    'breaking', 'update', 'updates', 'latest', 'live', 'report', 'reports',
    'news', 'says', 'say', 'today', 'year', 'years'
}


def title_key(title: str) -> set[str]:
    """Return meaningful title terms for conservative story clustering."""
    words = re.findall(r'[a-z0-9]{3,}', (title or '').lower())
    return {w for w in words if w not in _STORY_FILLER and not w.isdigit()}


def same_story(key: set[str], prior: set[str]) -> bool:
    """Two headlines share at least two meaningful words and most of the shorter one."""
    shared = len(key & prior)
    denominator = max(1, min(len(key), len(prior)))
    return shared >= 2 and shared / denominator >= (0.75 if denominator <= 2 else 0.6)


# ---------- memes

def _capture_settled(s: Session, item: ContentItem, now: float) -> bool:
    """Capture has finished (or given up), or had long enough; waiting longer would hide the meme forever."""
    jobs = s.exec(select(CaptureJob.status).where(CaptureJob.item_id == item.id)).all()
    if jobs and all(j in ('complete', 'partial', 'failed') for j in jobs):
        return True
    # Without a job, capture may be off; with one still running, give it longer.
    return now - item.first_seen > (3 * CAPTURE_GRACE if jobs else CAPTURE_GRACE)


def analyze_memes(s: Session, items: list[ContentItem], now: float | None = None) -> tuple[dict[int, MemeExample], set[int], set[int]]:
    """Duplicate identity for memes before they can be shown.

    Returns examples by item id, memes still waiting for their image to be captured, and memes
    whose image never arrived (they cannot be deduplicated or shown locally, so they are set aside).
    """
    now = now or time.time()
    out: dict[int, MemeExample] = {}
    waiting: set[int] = set()
    uncaptured: set[int] = set()
    found = [i for i in items if i.type == 'meme']
    known = {ex.item_id: ex for ex in s.exec(select(MemeExample).where(MemeExample.item_id.in_([i.id for i in found]))).all()}
    # Oldest first, so a repost is the duplicate of the original rather than the other way round.
    for item in sorted(found, key=lambda i: i.id):
        # analyze_item declines while an image is still uncaptured, and retries on a later call.
        ex = known.get(item.id) or memes.analyze_item(s, item)
        if ex:
            out[item.id] = ex
        elif _capture_settled(s, item, now):
            uncaptured.add(item.id)
        else:
            waiting.add(item.id)
    return out, waiting, uncaptured


# ---------- stories

def _vectors(s: Session, ids: list[int]) -> dict[int, list[float]]:
    model = semantic.config()['model']
    if not model or not ids:
        return {}
    out = {}
    for iid, raw, dim in s.exec(select(ItemVector.item_id, ItemVector.vector, ItemVector.dim)
                                .where(ItemVector.model == model, ItemVector.item_id.in_(ids))).all():
        vec = semantic._unpack(raw, dim)
        if vec is not None:
            out[iid] = vec
    return out


def _cosine(a, b) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(y * y for y in b)) or 1.0))


def assign_stories(s: Session, items: list[ContentItem]) -> dict[int, int]:
    """Put each news item into a story, opening one when nothing recent matches. Returns item → story."""
    news = sorted((i for i in items if i.type in STORY_TYPES), key=lambda i: i.published_at or i.first_seen)
    if not news:
        return {}
    have = dict(s.exec(select(StoryItem.item_id, StoryItem.story_id).where(StoryItem.item_id.in_([i.id for i in news]))).all())
    todo = [i for i in news if i.id not in have]
    if not todo:
        return have
    oldest = min(i.published_at or i.first_seen for i in todo) - STORY_WINDOW
    stories = list(s.exec(select(Story).where(Story.kind == 'news', Story.last_seen >= oldest)
                          .order_by(Story.last_seen.desc()).limit(STORY_CANDIDATES)).all())
    # Member headlines as term sets, computed once: (item id, terms, publisher).
    members: dict[int, list[tuple[int, set[str], str]]] = {st.id: [] for st in stories}
    if stories:
        for iid, sid, title, publisher in s.exec(
                select(StoryItem.item_id, StoryItem.story_id, ContentItem.title, ContentItem.publisher)
                .join(ContentItem, ContentItem.id == StoryItem.item_id).where(StoryItem.story_id.in_(list(members)))).all():
            members[sid].append((iid, title_key(title), publisher))
    vectors = _vectors(s, [i.id for i in todo] + [st.lead_item_id for st in stories if st.lead_item_id])

    for item in todo:
        when = item.published_at or item.first_seen
        key = title_key(item.title)
        vec = vectors.get(item.id)
        match = None
        # Most recently active first, and only so many: a long first pass stays linear.
        for st in stories[:STORY_CANDIDATES]:
            if abs(when - st.last_seen) > STORY_WINDOW:
                continue
            lead_vec = vectors.get(st.lead_item_id or 0)
            if vec is not None and lead_vec is not None and _cosine(vec, lead_vec) >= VECTOR_SAME:
                match = st
                break
            # Compare against each headline, not a growing union: one common word must not chain stories.
            if any(same_story(key, terms) for _, terms, _ in members[st.id]):
                match = st
                break
        if match is None:
            match = Story(kind='news', title=item.title, lead_item_id=item.id, first_seen=when, last_seen=when)
            s.add(match); s.flush()
            members[match.id] = []
        else:
            stories.remove(match)
        stories.insert(0, match)
        s.add(StoryItem(item_id=item.id, story_id=match.id))
        members[match.id].append((item.id, key, item.publisher))
        match.last_seen = max(match.last_seen, when)
        match.heat = float(len({p or str(i) for i, _, p in members[match.id]}))
        s.add(match)
        have[item.id] = match.id
    s.flush()
    return have
