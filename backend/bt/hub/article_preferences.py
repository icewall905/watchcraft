"""Article consumers of the shared feedback, evidence and memory pipeline."""
import logging
import time

from sqlmodel import select

from ..agent import memory, prompts
from ..db import session
from ..models import Feedback
from .models import ContentItem, ItemVerdict
from .master import views

log = logging.getLogger(__name__)
ARTICLE_TYPES = ('news', 'article', 'web')


def context(s, pid: int) -> str:
    """Use the same scoped core and evidence-labelled corrections as video consumers."""
    lessons = memory.active(s, pid, content_type='article')
    core, corrections = memory.select_memory(s, pid, lessons, content_type='article')
    parts = ['ARTICLE PREFERENCES: apply only to news, articles and web articles. '
             'Explicit instructions are authoritative; inferred rules retain their evidence strength. '
             'A rating concerns this article and does not endorse every subject or claim it contains.']
    if core:
        parts.append('SHARED CORE MEMORY\n' + core)
    if corrections:
        parts.append(prompts.lessons_block(corrections))
    # Recent direct corrections affect selection immediately while the shared learner works.
    rows = s.exec(select(Feedback, ContentItem).join(ContentItem, ContentItem.id == Feedback.item_id)
                  .where(Feedback.profile_id == pid, Feedback.content_type == 'article',
                         views.visible(pid, ContentItem.id))
                  .order_by(Feedback.id.desc()).limit(100)).all()
    latest = {}
    for feedback, item in rows:
        latest.setdefault(item.id, (feedback, item))
    examples = []
    for feedback, item in list(latest.values())[:20]:
        if feedback.rating == 0 and not feedback.text.strip() and not feedback.chips:
            continue
        examples.append(f'- {item.title[:180]}: rating {feedback.rating}; '
                        f'reasons: {", ".join(str(c)[:80] for c in feedback.chips[:12])}; '
                        f'note: {feedback.text[:500]}')
    if examples:
        parts.append('RECENT ARTICLE FEEDBACK (evidence, not blanket rules):\n' + '\n'.join(examples))
    return '\n\n'.join(parts) if core or corrections or examples else ''


def changed(pid: int) -> None:
    """Revisit recent article decisions with current feedback; never change forced choices."""
    from .master import schedule
    with session() as s:
        rows = s.exec(select(ItemVerdict).join(ContentItem, ContentItem.id == ItemVerdict.item_id)
                      .where(ItemVerdict.profile_id == pid, ContentItem.type.in_(ARTICLE_TYPES),
                             ContentItem.first_seen >= time.time() - 7 * 86400,
                             ItemVerdict.forced.is_(False), views.visible(pid, ContentItem.id))).all()
        for verdict in rows:
            verdict.judged_by = 'rules'
            s.add(verdict)
        s.commit()
    schedule.mark(pid)
