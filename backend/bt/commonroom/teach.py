"""Owner feedback as curator guidance: lesson text for the judge and a taste vector for ranking."""
import array
import math

from sqlmodel import Session, select

from .models import CRBlock, CRFeedback, CRPostVector

DOWN, UP = 25, 15


def lessons(s: Session, thread_id: int) -> list[str]:
    rows = []
    for verdict, limit in (('down', DOWN), ('up', UP)):
        rows += s.exec(select(CRFeedback).where(CRFeedback.thread_id == thread_id, CRFeedback.verdict == verdict)
                       .order_by(CRFeedback.created_at.desc()).limit(limit)).all()
    out = []
    for f in rows:
        title = ' '.join((f.title or '').split())[:160] or 'untitled post'
        why = ', '.join(str(r)[:40] for r in (f.reasons or [])[:5])
        note = ' '.join((f.note or '').split())[:300]
        out.append(f'{"👍" if f.verdict == "up" else "👎"} "{title}"' + (f' in {f.community}' if f.community else '') +
                   (f' — {why}' if why else '') + (f' — owner says: {note}' if note else ''))
    return out


def prompt(s: Session, thread) -> str:
    """Lesson block for the judge. Titles come from the web: the caller passes this inside <data>."""
    from ..knowledge.render import section_text
    lines = lessons(s, thread.id)
    woven = section_text(s, thread.profile_id, ['commonroom']) or ''
    text = ''
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


def taste(s: Session, thread_id: int, model: str) -> list[float] | None:
    """Mean liked vector minus half the mean disliked vector, or None without feedback."""
    def mean(verdict):
        ids = s.exec(select(CRFeedback.post_id).where(CRFeedback.thread_id == thread_id, CRFeedback.verdict == verdict)
                     .order_by(CRFeedback.created_at.desc()).limit(200)).all()
        if not ids:
            return None
        acc, n = None, 0
        for row in s.exec(select(CRPostVector).where(CRPostVector.post_id.in_(ids), CRPostVector.model == model)).all():
            v = _vec(row.vector, row.dim)
            if v is None:
                continue
            acc = list(v) if acc is None else [a + b for a, b in zip(acc, v)] if len(acc) == len(v) else acc
            n += 1
        return [a / n for a in acc] if acc and n else None
    up, down = mean('up'), mean('down')
    if up is None and down is None:
        return None
    if up is None:
        return [-0.5 * d for d in down]
    if down is None or len(down) != len(up):
        return up
    return [u - 0.5 * d for u, d in zip(up, down)]


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
