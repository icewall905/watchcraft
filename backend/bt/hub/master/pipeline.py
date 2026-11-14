"""One deterministic curator pass for one profile: rules, folding, scoring, tiers, stored verdicts."""
import time

from sqlmodel import Session, select

from ..models import ContentItem, CuratorDiscovery, ItemState, ItemVerdict
from . import LOCK, PASS_VERSION, cluster, edition, prefs, rules, score, pass_version

WINDOW = 7 * 86400.0
MAX_ITEMS = 2000


def visible_recent(s: Session, pid: int, now: float) -> list[ContentItem]:
    """Hub items this profile's curators found (or it touched) recently. Videos have their own curator."""
    found = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)
    touched = select(ItemState.item_id).where(ItemState.profile_id == pid)
    visible = ((ContentItem.id.in_(found)) | (ContentItem.id.in_(touched)))
    recent = select(ContentItem).where(visible, ContentItem.first_seen >= now - WINDOW,
                                       ContentItem.type != 'video')
    # Reserve a third of the bounded pass for each reading family. A busy meme
    # source must not prevent the article curators' finds from being judged.
    # Spare places return to the newest pool, so quiet families waste no budget.
    families = (ContentItem.type.in_(('news', 'article')),
                ContentItem.type == 'meme',
                ContentItem.type.not_in(('news', 'article', 'meme')))
    chosen = {}
    for family in families:
        for item in s.exec(recent.where(family).order_by(ContentItem.first_seen.desc(), ContentItem.id.desc())
                           .limit(MAX_ITEMS // len(families))).all():
            chosen[item.id] = item
    remaining = MAX_ITEMS - len(chosen)
    if remaining:
        fill = recent.where(ContentItem.id.not_in(list(chosen))) if chosen else recent
        for item in s.exec(fill.order_by(ContentItem.first_seen.desc(), ContentItem.id.desc()).limit(remaining)).all():
            chosen[item.id] = item
    return sorted(chosen.values(), key=lambda item: (item.first_seen, item.id), reverse=True)


def run(s: Session, pid: int, now: float | None = None) -> dict:
    """Judge the profile's recent hub items and store a verdict for each. Commits."""
    with LOCK:
        return _run(s, pid, now or time.time())


def _run(s: Session, pid: int, now: float) -> dict:
    items = visible_recent(s, pid, now)
    ids = [i.id for i in items]
    profile_rules = rules.load(s, pid)
    taste = score.load_taste(s, pid)
    article_taste = score.load_taste(s, pid, article=True)
    lessons = prefs.nudges(s, pid, now)
    examples, waiting, uncaptured = cluster.analyze_memes(s, items, now)
    stories = cluster.assign_stories(s, items)
    s.commit()  # stories and meme identity are shared and settled; do not hold the write lock while scoring
    scouts = score.discovery_scores(s, pid, ids)
    states = {st.item_id: st for st in s.exec(select(ItemState).where(ItemState.profile_id == pid, ItemState.item_id.in_(ids))).all()}
    prior = {v.item_id: v for v in s.exec(select(ItemVerdict).where(ItemVerdict.profile_id == pid, ItemVerdict.item_id.in_(ids))).all()}
    delivered_before = edition._previously_shown(s, pid, now)

    cands: list[score.Candidate] = []
    for item in items:
        if item.id in waiting:
            continue
        old = prior.get(item.id)
        verdict = rules.check(item, examples.get(item.id), profile_rules, now)
        rel, why = score.relevance(item, scouts.get(item.id), article_taste if item.type in ("news", "article", "web") else taste)
        rel, why = score.apply_lessons(s, pid, item, rel, why, now, lessons)
        quality = verdict.quality
        editor = []
        if old and old.judged_by == 'llm' and old.pass_version == pass_version(item.type) and not verdict.reject:
            quality, rel = old.quality, old.relevance  # a model's refinement outlives the rules pass
            editor = [x for x in old.reasons if x.startswith('editor:')]
        c = score.Candidate(item, quality, rel, score.final(quality, rel), editor + verdict.reasons + why, story_id=stories.get(item.id))
        state = states.get(item.id)
        if verdict.reject or (state and state.dismissed):
            c.tier, c.forced = 'reject', True
            if state and state.dismissed:
                c.reasons.append('dismissed')
        elif old and old.overruled:
            c.tier, c.forced = ('feature' if old.overruled == 'promoted' else 'set_aside'), True
            c.reasons.append(f'you {old.overruled} it')
        elif verdict.set_aside or item.id in uncaptured:
            c.tier, c.forced = 'set_aside', True
            if item.id in uncaptured:
                c.reasons.append('image not captured')
        elif item.id in delivered_before:
            c.tier, c.forced = 'keep', True
            c.reasons.append('shown in an earlier edition')
        cands.append(c)
    score.tier(cands, now)

    counts = {'feature': 0, 'keep': 0, 'set_aside': 0, 'reject': 0, 'waiting': len(waiting), 'aged_out': 0}
    # Featured items that left the window are no longer judged; take them off the front page.
    window = select(ContentItem.id).where(ContentItem.first_seen >= now - WINDOW)
    for v in s.exec(select(ItemVerdict).where(ItemVerdict.profile_id == pid, ItemVerdict.tier == 'feature',
                                              ItemVerdict.item_id.not_in(window))).all():
        v.tier, v.reasons, v.judged_at = 'keep', [*v.reasons, 'aged out'][:8], now
        s.add(v)
        counts['aged_out'] += 1
    for c in cands:
        v = prior.get(c.item.id) or ItemVerdict(profile_id=pid, item_id=c.item.id)
        llm = v.judged_by == 'llm' and v.pass_version == pass_version(c.item.type)
        v.quality, v.relevance, v.final, v.tier, v.forced = c.quality, c.relevance, c.final, c.tier, c.forced
        v.reasons = list(dict.fromkeys(c.reasons))[:8]
        v.judged_by, v.pass_version, v.judged_at = ('llm' if llm else 'rules'), pass_version(c.item.type), now
        s.add(v)
        counts[c.tier] += 1
    s.commit()
    return counts
