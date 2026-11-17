"""Kid-safe gate for Common Room. Fail closed: no model, an error or a malformed answer approves nothing.

Posts need an explicit kid_safe=true for their text; a picture is shown to a child only as captured
bytes that passed the vision check (CRAsset). Comments are judged per comment and an unsafe comment
drops its whole branch."""
import logging

from sqlmodel import select

from .. import curation
from ..db import session
from ..hub import ai, kidsafe
from .models import CRAsset, CRPost

log = logging.getLogger(__name__)
IMG_TYPES = ('image/jpeg', 'image/png', 'image/gif', 'image/webp')
MAX_KID_COMMENTS = 40
BODY_SEEN = 3000  # characters of body the judge reads (ai._data caps body at 3000); a child is shown no more


def check_posts(post_ids: list[int]) -> tuple[set[int], set[int]]:
    """(passed, failed) post ids. Posts whose batch got no answer are in neither and stay unchecked."""
    return _check(post_ids)


def _judge(items: list[dict], images: list[str] | None = None) -> set[int] | None:
    """1-based indexes the model explicitly marked kid_safe; None when the model gave no usable answer."""
    with session() as s:
        rails = kidsafe.household_rails(s)  # one verdict serves every child: it must suit each one's rails
    system = kidsafe.SYSTEM + (' ' + kidsafe.IMAGE_RULE if images else '') + rails
    try:
        res = ai._call(system, ai._data(items), kidsafe.SCHEMA, max_tokens=900, images=images, role='worker')
    except Exception as e:  # noqa: BLE001 — unchecked means unsafe
        log.info('Common Room kid check failed: %s', e)
        return None
    if not isinstance(res, dict) or not isinstance(res.get('verdicts'), list):
        return None
    ok = set()
    for row in res.get('verdicts', []) if isinstance(res, dict) else []:
        n, safe, _ = kidsafe.normalize(row)
        if safe and n is not None and 1 <= n <= len(items):
            ok.add(n)
    return ok


def _picture(post: CRPost) -> bytes | None:
    for m in post.media or []:
        url = m.get('url') if m.get('kind') == 'image' else m.get('poster')
        if not url:
            continue
        try:
            return curation.fetch(url, kinds=IMG_TYPES, limit=5_000_000)
        except Exception:  # noqa: BLE001
            return None
    return None


def _check(post_ids: list[int]) -> tuple[set[int], set[int]]:
    if not post_ids:
        return set(), set()
    with session() as s:
        posts = list(s.exec(select(CRPost).where(CRPost.id.in_(post_ids))).all())
        for p in posts:
            s.expunge(p)
    todo = [p for p in posts if p.kid_ok is None and not p.nsfw]
    passed = {p.id for p in posts if p.kid_ok == 1}
    enabled = ai.enabled()
    # NSFW never reaches a child; everything else gets a verdict only from an answered batch.
    verdicts: dict[int, int] = {p.id: 0 for p in posts if p.kid_ok is None and p.nsfw}
    for start in range(0, len(todo), kidsafe.BATCH) if enabled else ():
        batch = todo[start:start + kidsafe.BATCH]
        ok = _judge([{'type': 'post', 'title': p.title, 'publisher': f'{p.platform} {p.community}',
                      'summary': p.body[:600], 'body': p.body[:BODY_SEEN]} for p in batch])
        if ok is None:
            continue
        for i, p in enumerate(batch, 1):
            verdicts[p.id] = 1 if i in ok else 0
    passed |= {pid for pid, v in verdicts.items() if v}
    # Pictures: only for posts whose text passed, captured first so the model sees what a child would.
    pictures: list[tuple[int, bytes]] = []
    if enabled and kidsafe._vision():
        for p in posts:
            if p.id in passed and p.kid_image_ok is None and p.media:
                data = _picture(p)
                if data:
                    pictures.append((p.id, data))
    image_ok: dict[int, int] = {}
    from ..hub.moodboards import vision_image
    for start in range(0, len(pictures), kidsafe.IMAGE_BATCH):
        batch = pictures[start:start + kidsafe.IMAGE_BATCH]
        try:
            images = [vision_image(d) for _, d in batch]
        except Exception:  # noqa: BLE001
            continue
        ok = _judge([{'type': 'image', 'title': 'Post picture'} for _ in batch], images=images)
        if ok is None:
            continue
        for i, (pid, _) in enumerate(batch, 1):
            image_ok[pid] = 1 if i in ok else 0
    from ..hub.api import _image_mime
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        for pid, v in verdicts.items():
            p = s.get(CRPost, pid)
            if p and p.kid_ok is None:
                p.kid_ok = v
                s.add(p)
        for pid, data in pictures:
            p = s.get(CRPost, pid)
            if not p or pid not in image_ok:
                continue
            p.kid_image_ok = image_ok.get(pid, 0)
            s.add(p)
            mime = _image_mime(data)
            if p.kid_image_ok and mime and not s.get(CRAsset, pid):
                s.add(CRAsset(post_id=pid, mime=mime, data=data))
        s.commit()
    failed = {p.id for p in posts if p.kid_ok == 0} | {pid for pid, v in verdicts.items() if not v}
    return passed, failed


def filter_comments(tree: list[dict]) -> list[dict]:
    """Child copy of a comment tree: each comment judged, an unsafe one removes its branch."""
    flat: list[dict] = []

    def walk(nodes, depth):
        for c in nodes:
            if len(flat) >= MAX_KID_COMMENTS:
                return
            flat.append(c)
            walk(c.get('children') or [], depth + 1)
    walk(tree, 0)
    safe: set[int] = set()
    if ai.enabled():
        for start in range(0, len(flat), kidsafe.BATCH):
            batch = flat[start:start + kidsafe.BATCH]
            # As body: ai._data keeps 3000 characters of body (summary only 600), so all 1500 shown are judged.
            ok = _judge([{'type': 'comment', 'title': '', 'body': str(c.get('body') or '')[:1500]} for c in batch])
            safe |= {id(batch[i - 1]) for i in ok or ()}

    def keep(nodes):
        # A child sees only what was judged.
        return [{**c, 'body': str(c.get('body') or '')[:1500], 'children': keep(c.get('children') or [])}
                for c in nodes if id(c) in safe]
    return keep(tree)
