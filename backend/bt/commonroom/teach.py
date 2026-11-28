"""Owner feedback as curator guidance: lesson text for the judge and a taste vector for ranking."""
import array
import math

from sqlmodel import Session, select

from .models import CRBlock, CRFeedback, CRPick, CRPostVector

DOWN, UP = 12, 10   # recent per-sub examples; the taste memo carries the long-run pattern


def lessons(s: Session, thread_id: int) -> list[str]:
    rows = []
    for verdicts, limit in ((('down', 'meh'), DOWN), (('up',), UP)):
        rows += s.exec(select(CRFeedback).where(CRFeedback.thread_id == thread_id, CRFeedback.verdict.in_(verdicts))
                       .order_by(CRFeedback.created_at.desc()).limit(limit)).all()
    kinds = dict(s.exec(select(CRPick.post_id, CRPick.kind).where(
        CRPick.thread_id == thread_id, CRPick.post_id.in_([f.post_id for f in rows]))).all()) if rows else {}
    out = []
    for f in rows:
        title = ' '.join((f.title or '').split())[:160] or 'untitled post'
        kind = kinds.get(f.post_id) or ''
        why = ', '.join(str(r)[:40] for r in (f.reasons or [])[:5])
        note = ' '.join((f.note or '').split())[:300]
        mark = {'up': '👍', 'meh': '😐 on-topic but boring:'}.get(f.verdict, '👎')
        out.append(f'{mark} "{title}"' + (f' in {f.community}' if f.community else '') +
                   (f' ({kind})' if kind else '') +
                   (f' — {why}' if why else '') + (f' — owner says: {note}' if note else ''))
    return out


def prompt(s: Session, thread) -> str:
    """Lesson block for the judge. Titles come from the web: the caller passes this inside <data>."""
    from ..knowledge.render import section_text
    from . import joy
    lines = lessons(s, thread.id)
    woven = section_text(s, thread.profile_id, ['commonroom']) or ''
    text = joy.prompt_lines(thread.profile_id, s=s)
    text += '\n\n' if text else ''
    if lines:
        text += ('Owner feedback on earlier picks in this thread (learn the pattern: score similar posts the same '
                 'way):\n' + '\n'.join(lines))
    blocks = s.exec(select(CRBlock).where(CRBlock.profile_id == thread.profile_id).limit(100)).all()
    if blocks:
        text += '\nMuted by the owner: ' + ', '.join(f'{b.kind} {b.value}' for b in blocks)
    return text + ('\n\n' + woven if woven else '')


def _vec(blob: bytes, dim: int):
    v = array.array('f')
    try:
        v.frombytes(bytes(blob))
    except ValueError:
        return None
    return v if len(v) == dim else None


def _mean(s, model: str, weights: dict[int, float]) -> list[float] | None:
    """Weighted mean of post vectors (post_id → weight)."""
    if not weights:
        return None
    acc, total = None, 0.0
    for row in s.exec(select(CRPostVector).where(CRPostVector.post_id.in_(list(weights)), CRPostVector.model == model)).all():
        v = _vec(row.vector, row.dim)
        if v is None or (acc is not None and len(acc) != len(v)):
            continue
        w = weights[row.post_id]
        acc = [w * x for x in v] if acc is None else [a + w * x for a, x in zip(acc, v)]
        total += w
    return [a / total for a in acc] if acc and total else None


def _side(s, model: str, where) -> tuple[list[float] | None, list[float] | None]:
    """(liked, disliked) mean vectors from engagement, with explicit votes as a floor until the sweep runs."""
    liked: dict[int, float] = {}
    disliked: dict[int, float] = {}
    for post_id, e in s.exec(select(CRPick.post_id, CRPick.engaged).where(where(CRPick), CRPick.engaged != 0)
                             .order_by(CRPick.id.desc()).limit(400)).all():
        if e > 0:
            liked[post_id] = max(liked.get(post_id, 0), e)
        elif e < 0:
            disliked[post_id] = max(disliked.get(post_id, 0), -e)
    for post_id, verdict in s.exec(select(CRFeedback.post_id, CRFeedback.verdict).where(where(CRFeedback))
                                   .order_by(CRFeedback.created_at.desc()).limit(200)).all():
        if verdict == 'up':
            liked[post_id] = 1.0
        elif verdict in ('down', 'meh'):
            disliked[post_id] = 1.0 if verdict == 'down' else 0.6
    return _mean(s, model, liked), _mean(s, model, disliked)


def _combine(up, down):
    if up is None and down is None:
        return None
    if up is None:
        return [-0.5 * d for d in down]
    if down is None or len(down) != len(up):
        return up
    return [u - 0.5 * d for u, d in zip(up, down)]


def taste(s: Session, thread_id: int, model: str, profile_id: int | None = None) -> list[float] | None:
    """What the owner enjoys as a direction in embedding space: liked minus half disliked, weighted by
    engagement. The sub's own taste counts double the owner's taste across all subs (fun travels)."""
    sub = _combine(*_side(s, model, lambda m: m.thread_id == thread_id))
    whole = _combine(*_side(s, model, lambda m: m.profile_id == profile_id)) if profile_id else None
    if sub is None or whole is None or len(sub) != len(whole):
        return sub if sub is not None else whole
    return [(2 * a + b) / 3 for a, b in zip(sub, whole)]


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
