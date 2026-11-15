"""Kid-safe lock for Hub items shown to child profiles.

Every item a child can reach needs a current, safe `KidSafeVerdict`. The verdict is written only
by an explicit `kid_safe: true` from the model; a missing model, an error or a malformed answer
writes nothing, so the item stays hidden (fail closed). Pictures need the vision model: an
image-led item (a meme) without a checked picture is never shown, and other items show their
picture only once it passed.
"""
import logging
import time

from sqlalchemy import event, inspect as sa_inspect, delete
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from ..db import get_settings
from ..models import Profile
from . import ai
from .models import CapturedAsset, ContentItem, CuratorDiscovery, ItemState, KidSafeVerdict

log = logging.getLogger(__name__)

VERSION = 1
BODY = 6000  # characters of body the judge reads; a child is shown no more than this
BATCH = 8
IMAGE_BATCH = 4
IMAGE_LED = {'meme'}

TEXT_RULE = ('KID-SAFE (child profile, about 10-12 years old): mark kid_safe true only if a parent would be happy for '
             'a 10-12 year old to read this alone. Age-appropriate news, science, nature, sport, games, hobbies, '
             'history and how-tos are fine. Mark kid_safe false for: sexual content, nudity or innuendo; gore, '
             'graphic violence, torture or detailed crime, murder or abuse; self-harm or suicide; drugs, alcohol, '
             'smoking, vaping or gambling promotion; hate, slurs or harassment; swearing or crude language; horror '
             'or disturbing imagery; dating or romance; dangerous stunts or challenges a child could copy; scams, '
             'get-rich schemes or adult-only products. When unsure, mark kid_safe false.')
IMAGE_RULE = ('The attached pictures belong to the numbered items, in order. Pictures must also pass this bar: '
              'keep only images a parent would happily show a 6-year-old. Reject violence, weapons, blood or '
              'injury, horror or scary imagery, nudity, swimwear or suggestive poses, romance or kissing, drugs, '
              'alcohol, smoking or gambling, rude gestures, or text with adult language. When unsure, mark kid_safe false.')
SYSTEM = ('You are a strict child-safety reviewer for a family app. For each numbered item decide whether it may be '
          'shown to a child profile. ' + TEXT_RULE)
SCHEMA = {'type': 'object', 'properties': {'verdicts': {'type': 'array', 'items': {
    'type': 'object', 'properties': {'n': {'type': 'integer'}, 'kid_safe': {'type': 'boolean'},
                                     'reason': {'type': 'string'}},
    'required': ['n', 'kid_safe', 'reason']}}}, 'required': ['verdicts']}


def child_ids(s: Session) -> list[int]:
    return list(s.exec(select(Profile.id).where(Profile.role == 'child')).all())


def safe_ids():
    """Subquery of item ids a child may see."""
    return select(KidSafeVerdict.item_id).where(KidSafeVerdict.safe == True, KidSafeVerdict.version == VERSION)  # noqa: E712


def ok(col):
    return col.in_(safe_ids())


def is_safe(s: Session, item_id: int) -> bool:
    v = s.get(KidSafeVerdict, item_id)
    return bool(v and v.safe and v.version == VERSION)


def image_ok(s: Session, item_id: int) -> bool:
    v = s.get(KidSafeVerdict, item_id)
    return bool(v and v.safe and v.version == VERSION and v.image_checked)


def safe_set(s: Session, ids) -> set[int]:
    ids = list(ids)
    if not ids:
        return set()
    return set(s.exec(select(KidSafeVerdict.item_id).where(KidSafeVerdict.item_id.in_(ids), KidSafeVerdict.safe == True,  # noqa: E712
                                                            KidSafeVerdict.version == VERSION)).all())


def image_set(s: Session, ids) -> set[int]:
    ids = list(ids)
    if not ids:
        return set()
    return set(s.exec(select(KidSafeVerdict.item_id).where(KidSafeVerdict.item_id.in_(ids), KidSafeVerdict.safe == True,  # noqa: E712
                                                            KidSafeVerdict.version == VERSION,
                                                            KidSafeVerdict.image_checked == True)).all())  # noqa: E712


def normalize(row) -> tuple[int | None, bool, str]:
    """(n, safe, reason). Anything but a literal true is unsafe."""
    if not isinstance(row, dict):
        return None, False, ''
    n = row.get('n')
    return (n if isinstance(n, int) and not isinstance(n, bool) else None), row.get('kid_safe') is True, str(row.get('reason') or '')[:200]


def pending(s: Session, limit: int = 40, pids: list[int] | None = None, vision: bool | None = None) -> list[ContentItem]:
    """Items any child can reach that lack a current verdict, newest first. Image-led items wait
    while there is no vision model, so they cannot crowd out what can be judged."""
    pids = child_ids(s) if pids is None else pids
    if not pids:
        return []
    reach = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id.in_(pids)).union(
        select(ItemState.item_id).where(ItemState.profile_id.in_(pids)))
    done = select(KidSafeVerdict.item_id).where(KidSafeVerdict.version == VERSION)
    q = select(ContentItem).where(ContentItem.id.in_(reach), ContentItem.id.not_in(done), ContentItem.type != 'video')
    if not (_vision() if vision is None else vision):
        q = q.where(ContentItem.type.not_in(IMAGE_LED))
    return list(s.exec(q.order_by(ContentItem.first_seen.desc()).limit(limit)).all())


def _vision() -> bool:
    return ai.enabled() and bool(get_settings()['llm'].get('vision'))


def _picture(s: Session, item: ContentItem) -> str | None:
    asset = s.get(CapturedAsset, (item.id, 'image'))
    if not asset:
        return None
    try:
        from .moodboards import vision_image
        return vision_image(asset.data)
    except Exception:  # noqa: BLE001 — unreadable picture: judged as if it had none
        return None


def _row(item: ContentItem) -> dict:
    meta = item.meta or {}
    media = meta.get('media') if isinstance(meta.get('media'), dict) else {}
    extra = '; '.join(x for x in (f'image description: {media["alt"]}' if media.get('alt') else '',
                                   f'community: {meta["community"]}' if meta.get('community') else '') if x)
    return {'type': item.type, 'title': item.title, 'publisher': item.publisher, 'url': item.url,
            'summary': item.summary or (item.body or '')[:600], 'body': (item.body or '')[:BODY], 'coverage': extra}


def _store(s: Session, item: ContentItem, safe: bool, reason: str, image_checked: bool, now: float):
    v = s.get(KidSafeVerdict, item.id) or KidSafeVerdict(item_id=item.id)
    v.safe, v.reason, v.image_checked, v.version, v.judged_at = safe, reason, image_checked, VERSION, now
    s.add(v)


def judge_pending(s: Session, limit: int = 40, pids: list[int] | None = None, now: float | None = None) -> dict:
    """Judge what children can reach. Commits per batch. Errors leave items unjudged (hidden)."""
    now = now or time.time()
    out = {'judged': 0, 'safe': 0, 'skipped': 0, 'stopped': ''}
    vision = _vision()
    todo = pending(s, limit, pids, vision)
    if not todo:
        return out
    if not ai.enabled():
        out['stopped'], out['skipped'] = 'no model', len(todo)
        return out
    texts, pictures = [], []
    for item in todo:
        if (item.meta or {}).get('sensitive'):
            _store(s, item, False, 'marked sensitive by its source', False, now)
            out['judged'] += 1
            continue
        pic = _picture(s, item) if vision else None
        if pic:
            pictures.append((item, pic))
        elif item.type in IMAGE_LED:
            out['skipped'] += 1  # no checked picture, no meme for a child
        else:
            texts.append((item, None))
    s.commit()
    batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)] + \
              [pictures[i:i + IMAGE_BATCH] for i in range(0, len(pictures), IMAGE_BATCH)]
    for batch in batches:
        images = [p for _, p in batch if p]
        system = SYSTEM + (' ' + IMAGE_RULE if images else '')
        try:
            res = ai._call(system, ai._data([_row(i) for i, _ in batch]), SCHEMA, max_tokens=900,
                           images=images or None, role='worker')
        except Exception as e:  # noqa: BLE001 — fail closed: nothing stored, items stay hidden
            log.info('kid-safe judge failed: %s', e)
            out['stopped'] = 'model error'
            out['skipped'] += len(batch)
            continue
        seen = set()
        for row in res.get('verdicts', []) if isinstance(res, dict) else []:
            n, safe, reason = normalize(row)
            if n is None or n in seen or not 1 <= n <= len(batch):
                continue
            seen.add(n)
            item, pic = batch[n - 1]
            _store(s, item, safe, reason, bool(pic) and safe, now)
            out['judged'] += 1
            out['safe'] += int(safe)
        out['skipped'] += len(batch) - len(seen)
        s.commit()
    return out


# A verdict is about the content the judge read. When that content changes (a capture replaces
# the body, a later fetch fills a summary, a picture is captured or swapped) the verdict is
# dropped, so the item is hidden from children until it is judged again. Every write to these
# rows goes through the ORM, so a flush hook sees them all.
JUDGED_FIELDS = ('title', 'summary', 'body', 'image', 'enrichment')


def _changed(obj, fields) -> bool:
    state = sa_inspect(obj)
    return any(state.attrs[f].history.has_changes() for f in fields if f in state.attrs)


@event.listens_for(OrmSession, 'before_flush')
def _invalidate(session, _ctx, _instances):
    stale: set[int] = set()
    for obj in session.dirty:
        if isinstance(obj, ContentItem) and obj.id is not None and _changed(obj, JUDGED_FIELDS):
            stale.add(obj.id)
        elif isinstance(obj, CapturedAsset) and _changed(obj, ('data',)):
            stale.add(obj.item_id)
    for obj in session.new:
        if isinstance(obj, CapturedAsset) and obj.item_id is not None:
            stale.add(obj.item_id)
    stale -= {None}
    if stale:
        session.execute(delete(KidSafeVerdict).where(KidSafeVerdict.item_id.in_(stale)))
