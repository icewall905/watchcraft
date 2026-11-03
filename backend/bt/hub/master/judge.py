"""Model judging for the uncertain band: the rules pass decides the obvious, a model reads the rest.

It never blocks browsing: without a model, while the video agent is thinking, or on any
failure, the rules verdict stands. Retrieved text is untrusted and goes through ai._call.
"""
import logging
import time
from datetime import datetime, timezone

from sqlmodel import Session, select

from ...db import get_agent_control, get_agent_state
from .. import ai
from ..models import ContentItem, Curator, HubPreferenceCore, ItemVerdict
from . import PASS_VERSION, rules, score

log = logging.getLogger(__name__)

BATCH = 15
LIGHT_LIMIT = 30          # items a post-run pass may send to the model
FRESH = 48 * 3600.0       # only recent items are worth a model's time
BAND = (score.KEEP_MIN - 10, score.FEATURE_MIN + 20)  # finals where rules alone are unsure
VIDEO_THINKING = {'planning', 'judging', 'learning', 'transcript', 'comments'}
STATE_FRESH = 600.0       # an agent state older than this is not evidence it is still busy
MODEL_WEIGHT = {'quality': 0.7, 'relevance': 0.6}  # the rest stays with the rules' signals

SCHEMA = {'type': 'object', 'properties': {'verdicts': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'n': {'type': 'integer'},
    'quality': {'type': 'integer', 'minimum': 0, 'maximum': 100},
    'relevance': {'type': 'integer', 'minimum': 0, 'maximum': 100},
    'reason': {'type': 'string'}},
    'required': ['n', 'quality', 'relevance', 'reason']}}}, 'required': ['verdicts']}

SYSTEM = ('You are the editor of a calm personal magazine. For each item, rate QUALITY 0-100 (is it worth a '
          'thoughtful reader\'s time: substance, originality, craft; low for spam, rage bait, clickbait, reposts, '
          'low-effort memes, thin rewrites, ads) and RELEVANCE 0-100 to this reader\'s interests below. The reason is '
          'one short, concrete sentence about the item itself. Be strict: most items should not score above 70.')


def video_busy(now: float | None = None) -> bool:
    """The shared GPU is thinking for the video curator, or AI work is paused: leave it alone."""
    if get_agent_control().get('paused'):
        return True
    st = get_agent_state()
    if st.get('state') not in VIDEO_THINKING:
        return False
    try:
        at = datetime.fromisoformat(str(st.get('updated_at'))).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return False
    return (now or time.time()) - at < STATE_FRESH


def available(now: float | None = None) -> bool:
    return ai.enabled() and not video_busy(now)


def reader(s: Session, pid: int) -> str:
    """What this profile cares about, in words: learned taste first, then its curators' briefs."""
    core = s.get(HubPreferenceCore, pid)
    parts = [core.text.strip()] if core and core.text.strip() else []
    for c in s.exec(select(Curator).where(Curator.profile_id == pid, Curator.enabled == True)).all():  # noqa: E712
        brief = '; '.join(x for x in (c.name, (c.instructions or '')[:300],
                                      'keywords: ' + ', '.join(c.keywords[:10]) if c.keywords else '',
                                      'avoid: ' + ', '.join(c.exclusions[:10]) if c.exclusions else '') if x)
        parts.append(f'- {brief}')
    taste = score.load_taste(s, pid)
    if taste.liked:
        parts.append('Often keeps items about: ' + ', '.join(taste.liked[:10]))
    if taste.disliked:
        parts.append('Often dismisses items about: ' + ', '.join(taste.disliked[:10]))
    return '\n'.join(parts)[:3000] or 'No stated interests yet: judge general quality.'


def candidates(s: Session, pid: int, now: float, limit: int) -> list[tuple[ItemVerdict, ContentItem]]:
    """Unsure, unforced, recent verdicts the model has not read yet, most promising first."""
    rows = s.exec(select(ItemVerdict, ContentItem).join(ContentItem, ContentItem.id == ItemVerdict.item_id)
                  .where(ItemVerdict.profile_id == pid, ItemVerdict.judged_by == 'rules', ItemVerdict.overruled == '',
                         ItemVerdict.tier.in_(['feature', 'keep', 'set_aside']),
                         ItemVerdict.final >= BAND[0], ItemVerdict.final <= BAND[1],
                         ContentItem.first_seen >= now - FRESH)
                  .order_by(ItemVerdict.final.desc()).limit(limit * 3)).all()
    # Rules that set an item aside for a fixed reason are not a question of taste.
    fixed = {'stale', 'image not captured'}
    return [(v, i) for v, i in rows if not fixed & set(v.reasons)][:limit]


def _row(item: ContentItem) -> dict:
    meta = item.meta or {}
    extra = []
    media = meta.get('media') if isinstance(meta.get('media'), dict) else {}
    if media.get('alt'):
        extra.append(f'image description: {media["alt"]}')
    if meta.get('community'):
        extra.append(f'community: {meta["community"]}')
    pop = rules.engagement(meta)
    if pop is not None:
        extra.append(f'reactions seen: {int(pop)}')
    return {'type': item.type, 'title': item.title, 'publisher': item.publisher, 'url': item.url,
            'summary': item.summary or (item.body or '')[:600], 'coverage': '; '.join(extra)}


def _score(value) -> int | None:
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return None


def judge(s: Session, pid: int, now: float | None = None, limit: int = LIGHT_LIMIT) -> dict:
    """Send the uncertain band to the model in batches; stop early if the GPU is wanted. Commits per batch."""
    now = now or time.time()
    out = {'judged': 0, 'skipped': 0, 'stopped': ''}
    if not ai.enabled():
        out['stopped'] = 'no model'
        return out
    todo = candidates(s, pid, now, limit)
    if not todo:
        return out
    interests = reader(s, pid)
    for start in range(0, len(todo), BATCH):
        if video_busy():
            out['stopped'] = 'video busy'
            out['skipped'] = len(todo) - start
            break
        batch = todo[start:start + BATCH]
        try:
            res = ai._call(SYSTEM, f'Reader interests:\n{ai._esc(interests)}\n\n{ai._data([_row(i) for _, i in batch])}',
                           SCHEMA, max_tokens=1800, role='worker')
        except Exception as e:  # noqa: BLE001 — the rules verdict stands
            log.info('master judge failed: %s', e)
            out['stopped'] = 'model error'
            out['skipped'] = len(todo) - start
            break
        for row in res.get('verdicts', []):
            n = row.get('n') if isinstance(row, dict) else None
            if not isinstance(n, int) or not 1 <= n <= len(batch):
                continue
            q, r = _score(row.get('quality')), _score(row.get('relevance'))
            if q is None or r is None:
                continue  # a malformed row leaves that item's rules verdict alone
            v, _ = batch[n - 1]
            v.quality = round(MODEL_WEIGHT['quality'] * q + (1 - MODEL_WEIGHT['quality']) * v.quality, 1)
            v.relevance = round(MODEL_WEIGHT['relevance'] * r + (1 - MODEL_WEIGHT['relevance']) * v.relevance, 1)
            v.final = score.final(v.quality, v.relevance)
            reason = str(row.get('reason') or '').strip()[:200]
            v.reasons = ([f'editor: {reason}'] if reason else []) + [x for x in v.reasons if not x.startswith('editor:')][:7]
            v.judged_by, v.pass_version, v.judged_at = 'llm', PASS_VERSION, now
            s.add(v)
            out['judged'] += 1
        s.commit()
    return out
