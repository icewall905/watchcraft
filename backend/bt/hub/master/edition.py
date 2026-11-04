"""The daily edition: one calm page per profile, built from featured verdicts.

Built in the morning by the nightly pass and patched after later runs. Patching never moves
what the reader has already seen (`Edition.shown_item_ids`); it only fills free places. Story
summaries are written from the items this profile can see, never from other profiles' finds.
"""
import copy
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlmodel import Session, select

from ...db import get_settings
from .. import ai
from ..models import Alert, ContentItem, Curator, Edition, ItemState, ItemVerdict, StoryItem
from . import views

log = logging.getLogger(__name__)

STORY_TYPES = ('news', 'article')
# Section order and size. Small on purpose: the page should be read in one sitting.
SECTIONS = [('lead', 'Today\'s story', 1), ('stories', 'Stories', 5), ('reads', 'Worth reading', 6),
            ('memes', 'Something light', 8)]
INTRO_WORDS = 60
STORY_SUMMARIES = 4  # model-written story summaries per morning


def today(now: float | None = None) -> str:
    """The household's calendar day, in the same time zone as the nightly memory pass."""
    tz = ZoneInfo(get_settings()['memory']['timezone'])
    return datetime.fromtimestamp(now or time.time(), tz).date().isoformat()


def _featured(s: Session, pid: int) -> list[tuple[ItemVerdict, ContentItem, int | None]]:
    rows = s.exec(select(ItemVerdict, ContentItem, StoryItem.story_id)
                  .join(ContentItem, ContentItem.id == ItemVerdict.item_id)
                  .outerjoin(StoryItem, StoryItem.item_id == ItemVerdict.item_id)
                  .where(ItemVerdict.profile_id == pid, ItemVerdict.tier == 'feature', views.visible(pid, ItemVerdict.item_id))
                  .order_by(ItemVerdict.final.desc())).all()
    return list(rows)


def _sources(s: Session, pid: int, story_ids: list[int]) -> dict[int, int]:
    """Distinct publishers per story among the items this profile may see."""
    if not story_ids:
        return {}
    rows = s.exec(select(StoryItem.story_id, func.count(func.distinct(ContentItem.publisher)))
                  .join(ContentItem, ContentItem.id == StoryItem.item_id)
                  .join(ItemVerdict, (ItemVerdict.item_id == StoryItem.item_id) & (ItemVerdict.profile_id == pid))
                  .where(StoryItem.story_id.in_(story_ids), ItemVerdict.tier != 'reject')
                  .group_by(StoryItem.story_id)).all()
    return dict(rows)


def _unavailable(s: Session, pid: int) -> set[int]:
    """Reader actions apply immediately, including to entries already shown today."""
    return set(s.exec(select(ItemState.item_id).where(
        ItemState.profile_id == pid,
        (ItemState.read_at.is_not(None)) | ItemState.dismissed | ItemState.archived,
    )).all())


def _previously_shown(s: Session, pid: int, now: float) -> set[int]:
    previous = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day < today(now))).all()
    return {iid for ed in previous for iid in ed.shown_item_ids}


def desired(s: Session, pid: int, now: float | None = None) -> dict[str, list[dict]]:
    """What the edition would hold if built from scratch now, best first."""
    now = now if now is not None else time.time()
    # A new calendar day gets a new selection. Merely placing an item yesterday
    # does not consume it: only entries actually delivered to this reader do.
    unavailable_ids = _unavailable(s, pid)
    previously_shown = _previously_shown(s, pid, now)
    rows = []
    for v, item, story in _featured(s, pid):
        if item.id in unavailable_ids or v.overruled == 'demoted' or (item.id in previously_shown and v.overruled != 'promoted'):
            continue
        stamp = item.published_at if item.published_at is not None else item.first_seen
        age = max(0, now - stamp) / 86400
        lifetime = 7 if item.type in ('news', 'meme') else 30
        if age > lifetime and v.overruled != 'promoted':
            continue
        # Relevance still matters, while yesterday's high score cannot crowd out
        # comparably good fresh reporting indefinitely. Tie breaks are stable.
        rows.append((v.final - age * 4, stamp, item.id, v, item, story))
    rows.sort(key=lambda row: row[:3], reverse=True)
    out: dict[str, list[dict]] = {key: [] for key, _, _ in SECTIONS}
    for _, _, _, v, item, story in rows:
        entry = {'item_id': item.id, **({'story_id': story} if story else {})}
        if item.type == 'meme':
            out['memes'].append(entry)
        elif item.type in STORY_TYPES and story:
            (out['lead'] if not out['lead'] else out['stories']).append(entry)
        else:
            out['reads'].append(entry)
    return out


def _alive(s: Session, pid: int, ids: list[int], now: float) -> set[int]:
    """Items still fit to show: not rejected, dismissed or demoted by the reader since they were placed."""
    if not ids:
        return set()
    previously_shown = _previously_shown(s, pid, now)
    promoted = set(s.exec(select(ItemVerdict.item_id).where(ItemVerdict.profile_id == pid, ItemVerdict.item_id.in_(ids),
                                                          ItemVerdict.overruled == 'promoted')).all())
    return set(s.exec(select(ItemVerdict.item_id).where(ItemVerdict.profile_id == pid, ItemVerdict.item_id.in_(ids),
                                                        ItemVerdict.tier != 'reject', ItemVerdict.overruled != 'demoted',
                                                        views.visible(pid, ItemVerdict.item_id))).all()) - _unavailable(s, pid) - (previously_shown - promoted)


def merge(old: list[dict], new: dict[str, list[dict]], shown: set[int], alive: set[int]) -> list[dict]:
    """Keep seen entries where they are, then fill each section's free places with the best new ones."""
    before = {sec['key']: sec for sec in old}
    # Prose written earlier today follows its item into whichever section it lands in.
    prose = {e['item_id']: {k: e[k] for k in ('summary', 'summary_refs') if k in e}
             for sec in old for e in sec.get('entries', [])}
    used: set[int] = set()
    sections = []
    for key, title, size in SECTIONS:
        kept = [e for e in before.get(key, {}).get('entries', []) if e['item_id'] in shown and e['item_id'] in alive]
        used |= {e['item_id'] for e in kept}
        sections.append({'key': key, 'title': title, 'entries': kept, 'size': size})
    taken_stories = {e.get('story_id') for sec in sections for e in sec['entries'] if e.get('story_id')}
    for sec in sections:
        # Lead and stories draw from one ranked list: an empty lead takes the best story still
        # free, and a new best story that cannot lead (the seen lead stays) joins the stories.
        offered = new.get('lead', []) + new.get('stories', []) if sec['key'] in ('lead', 'stories') else new.get(sec['key'], [])
        for e in offered:
            if len(sec['entries']) >= sec['size']:
                break
            if e['item_id'] in used or (e.get('story_id') and e['story_id'] in taken_stories):
                continue
            sec['entries'].append({**e, **prose.get(e['item_id'], {})})
            used.add(e['item_id'])
            if e.get('story_id'):
                taken_stories.add(e['story_id'])
    return [{k: v for k, v in sec.items() if k != 'size'} for sec in sections]


def _extras(s: Session, pid: int, now: float) -> list[dict]:
    """Quiet lines: research written since yesterday and alerts still waiting."""
    out = []
    reports = s.exec(select(Curator.id).where(Curator.profile_id == pid, Curator.kind == 'research',
                                              Curator.report_at >= now - 86400, Curator.report != '')).all()
    if reports:
        out.append({'key': 'research', 'title': 'From your research', 'curator_ids': list(reports)})
    alerts = s.exec(select(func.count()).select_from(Alert).where(Alert.profile_id == pid, Alert.state == 'new')).one()
    if alerts:
        out.append({'key': 'alerts', 'title': 'Alerts', 'count': alerts})
    return out


def patch(s: Session, pid: int, now: float | None = None, run_id: str = '') -> Edition:
    """Bring today's edition up to date without moving anything already seen. No model calls. Commits."""
    now = now or time.time()
    day = today(now)
    ed = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day == day)).first() or Edition(profile_id=pid, day=day)
    # Work on a copy: the JSON column only saves a new value, never edits made in place.
    old = [sec for sec in copy.deepcopy(ed.sections) if sec.get('key') in {k for k, _, _ in SECTIONS}]
    placed = [e['item_id'] for sec in old for e in sec.get('entries', [])]
    sections = merge(old, desired(s, pid, now), set(ed.shown_item_ids), _alive(s, pid, placed, now))
    story_ids = [e['story_id'] for sec in sections for e in sec['entries'] if e.get('story_id')]
    counts = _sources(s, pid, story_ids)
    for sec in sections:
        for e in sec['entries']:
            if e.get('story_id'):
                e['sources'] = counts.get(e['story_id'], 1)
    current_ids = {entry['item_id'] for sec in sections for entry in sec['entries']}
    if set(ed.intro_refs) - current_ids:
        ed.intro, ed.intro_refs = '', []
    ed.sections = sections + _extras(s, pid, now)
    if run_id and run_id not in ed.source_run_ids:
        ed.source_run_ids = [*ed.source_run_ids, run_id][-50:]
    ed.updated_at = now
    s.add(ed)
    s.commit()
    s.refresh(ed)
    return ed


def _entry_items(s: Session, ed: Edition, keys: tuple[str, ...]) -> list[ContentItem]:
    ids = [e['item_id'] for sec in ed.sections if sec.get('key') in keys for e in sec.get('entries', [])]
    found = {i.id: i for i in s.exec(select(ContentItem).where(ContentItem.id.in_(ids))).all()} if ids else {}
    return [found[i] for i in ids if i in found]


def write(s: Session, pid: int, ed: Edition, busy=lambda: False) -> dict:
    """Morning prose: a cited intro over the lead and stories, and short summaries of stories
    with several sources. Each step is optional; on any failure the edition reads fine without it."""
    out = {'intro': False, 'summaries': 0}
    if not ai.enabled():
        return out
    top = _entry_items(s, ed, ('lead', 'stories', 'reads'))[:8]
    if top and not ed.intro and not busy():
        try:
            ed.intro = ai._cited(top, 'Write the opening overview of these sources and why they matter. No greeting or uncited opening/closing. '
                                      'Every sentence must end with source citations before its final punctuation, '
                                      'for example: A source reports a new release [1].', INTRO_WORDS)
            ed.intro_refs = [i.id for i in top]
            out['intro'] = True
        except Exception as e:  # noqa: BLE001 — the page has a plain greeting instead
            log.info('edition intro failed: %s', e)
    written = 0
    sections = copy.deepcopy(ed.sections)
    for sec in sections:
        if sec.get('key') not in ('lead', 'stories'):
            continue
        for entry in sec['entries']:
            if written >= STORY_SUMMARIES or busy():
                break
            if entry.get('summary') or entry.get('sources', 1) < 2:
                continue
            members = list(s.exec(select(ContentItem).join(StoryItem, StoryItem.item_id == ContentItem.id)
                                  .join(ItemVerdict, (ItemVerdict.item_id == ContentItem.id) & (ItemVerdict.profile_id == pid))
                                  .where(StoryItem.story_id == entry['story_id'], ItemVerdict.tier != 'reject')
                                  .order_by(ItemVerdict.final.desc()).limit(6)).all())
            try:
                entry['summary'] = ai._cited(members, 'Summarise what these reports say about one event, noting where '
                                                      'they agree or differ.', 70)
                entry['summary_refs'] = [i.id for i in members]
                written += 1
            except Exception as e:  # noqa: BLE001 — the lead headline stands alone
                log.info('story summary failed: %s', e)
    out['summaries'] = written
    ed.sections = sections  # a new value, so the JSON column is saved
    s.add(ed)
    s.commit()
    return out
