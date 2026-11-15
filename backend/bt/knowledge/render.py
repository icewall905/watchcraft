"""Read side of the living knowledge: the text each consumer reads, and score nudges from statements."""
from __future__ import annotations

from types import SimpleNamespace

from sqlmodel import Session, col, select

from .models import SECTIONS, ProfileKnowledge

LABEL = {'explicit': 'told by user', 'established': 'established', 'tentative': 'tentative'}
WEIGHT = {'explicit': 1.5, 'established': 1.2, 'tentative': 0.8}
# Which knowledge sections each hub item type is scored against ('' = every type).
NUDGE_SCOPE = {'overall': '', 'news': 'news', 'articles': 'article', 'memes': 'meme', 'web': 'web'}
VIDEO = ('overall', 'videos', 'shorts')
ARTICLE = ('overall', 'news', 'articles')
HUB = ('overall', 'news', 'articles', 'memes', 'web')


def current(s: Session, pid: int) -> ProfileKnowledge | None:
    return s.exec(select(ProfileKnowledge).where(ProfileKnowledge.profile_id == pid)
                  .order_by(col(ProfileKnowledge.id).desc())).first()


def statement_line(st: dict) -> str:
    direction = 'Prefer' if int(st.get('polarity') or 1) > 0 else 'Avoid'
    return f"- {direction}: {str(st.get('text') or '').strip()} [{LABEL.get(st.get('confidence'), 'tentative')}]"


def render(sections: list[dict], keys=None, board_id: int | None = None) -> str:
    parts = []
    for sec in sections or []:
        if keys is not None and sec.get('key') not in keys:
            continue
        rows = [st for st in sec.get('statements') or []
                if board_id is None or st.get('board_id') in (None, board_id)]
        if sec.get('key') == 'moodboards' and board_id is None:
            rows = [st for st in rows if st.get('board_id') is None] + [st for st in rows if st.get('board_id') is not None]
        if rows:
            parts.append(f"## {sec.get('title') or SECTIONS.get(sec.get('key'), sec.get('key'))}\n"
                         + '\n'.join(statement_line(st) for st in rows))
    return '\n\n'.join(parts)


def section_text(s: Session, pid: int, keys, board_id: int | None = None) -> str | None:
    """Knowledge for these sections, or None when the profile has no knowledge yet (callers fall back)."""
    pk = current(s, pid)
    if pk is None:
        return None
    body = render(pk.sections, set(keys), board_id)
    if not body:
        return ''
    return ('LEARNED KNOWLEDGE ABOUT THIS PERSON (woven nightly from their feedback; [told by user] statements '
            'are authoritative, [tentative] ones are weak evidence):\n' + body)


def nudges(s: Session, pid: int) -> list:
    """Statements shaped like HubLessons, so score.apply_lessons can keep nudging after lessons are merged."""
    pk = current(s, pid)
    if pk is None:
        return []
    out = []
    for sec in pk.sections or []:
        scope = NUDGE_SCOPE.get(sec.get('key'))
        if scope is None:
            continue
        for st in sec.get('statements') or []:
            at = float(st.get('updated') or st.get('since') or pk.created_at)
            out.append(SimpleNamespace(id=st.get('sid'), text=str(st.get('text') or ''), scope=scope,
                                       polarity=1 if int(st.get('polarity') or 1) > 0 else -1,
                                       weight=WEIGHT.get(st.get('confidence'), 0.8),
                                       last_evidence_at=at, created_at=at, active=True))
    return out
