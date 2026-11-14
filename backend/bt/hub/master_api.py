"""Master curator endpoints: the daily edition, what was set aside, overrules, stories,
explanations and the learned preferences the curator reads."""
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as Input
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..access import require_hub
from ..api.core import profile_id
from ..db import session, get_kv, save_setting
from .api import can_see, item_query, serialize
from .master import edition, schedule, views
from .models import ContentItem, Curator, Edition, HubLesson, HubPreferenceCore, ItemVerdict, StoryItem, Topic

router = APIRouter(prefix='/hub', tags=['WatchCraft Hub'], dependencies=[Depends(require_hub)])
SET_ASIDE_DAYS = 7


def _verdict_dict(v: ItemVerdict | None) -> dict | None:
    if not v:
        return None
    return {'tier': v.tier, 'quality': v.quality, 'relevance': v.relevance, 'final': v.final, 'reasons': v.reasons,
            'judged_by': v.judged_by, 'overruled': v.overruled, 'judged_at': v.judged_at}


def _items(s: Session, pid: int, ids: list[int]) -> dict[int, dict]:
    rows = list(s.exec(select(ContentItem).where(ContentItem.id.in_(ids))).all()) if ids else []
    verdicts = {v.item_id: v for v in s.exec(select(ItemVerdict).where(ItemVerdict.profile_id == pid, ItemVerdict.item_id.in_(ids))).all()} if ids else {}
    out = {}
    for d in serialize(s, pid, rows):
        d['verdict'] = _verdict_dict(verdicts.get(d['id']))
        out[d['id']] = d
    return out


def _refs(found: dict[int, dict], refs: list[int]) -> list[dict]:
    """Citation [n] → the item it points to, or None where this profile cannot open it."""
    return [{'n': n, **({'item_id': i, 'title': found[i]['title'], 'url': found[i]['url']} if i in found else {'item_id': None})}
            for n, i in enumerate(refs, 1)]


def _set_aside_count(s: Session, pid: int, now: float) -> int:
    return s.exec(select(func.count()).select_from(ItemVerdict).join(ContentItem, ContentItem.id == ItemVerdict.item_id)
                  .where(ItemVerdict.profile_id == pid, ItemVerdict.tier == 'set_aside', views.visible(pid, ItemVerdict.item_id),
                         ContentItem.first_seen >= now - SET_ASIDE_DAYS * 86400)).one()


@router.get('/today')
def today(preview: bool = False, stories: int = 8, reads: int = 6, memes: int = 4,
          pid: int = Depends(profile_id)):
    """Today's edition; preview clients record only the section entries they render."""
    now = time.time()
    with edition.EDITION_LOCK, session() as s:
        day = edition.today(now)
        try:
            # Fill the expanded edition immediately, retaining entries already delivered today.
            ed = edition.patch(s, pid, now)
        except IntegrityError:  # another process built it first
            s.rollback()
            ed = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day == day)).first()
            if ed is None:
                raise
        entry_ids = [e['item_id'] for sec in ed.sections for e in sec.get('entries', [])]
        refs = [*ed.intro_refs, *(r for sec in ed.sections for e in sec.get('entries', []) for r in e.get('summary_refs', []))]
        visible = views.visible_set(s, pid, set(entry_ids) | set(refs))
        found = _items(s, pid, list(visible))
        sections = []
        for sec in ed.sections:
            out = {k: v for k, v in sec.items() if k != 'entries'}
            if 'entries' in sec:
                out['entries'] = [{**{k: v for k, v in e.items() if k not in ('summary_refs',)},
                                   'item': found[e['item_id']],
                                   **({'summary_refs': _refs(found, e['summary_refs'])} if e.get('summary_refs') else {})}
                                  for e in sec['entries'] if e['item_id'] in found]
            if sec.get('key') == 'research':
                out['curators'] = [{'id': c.id, 'name': c.name, 'subject': c.subject, 'report_at': c.report_at}
                                   for c in s.exec(select(Curator).where(Curator.id.in_(sec.get('curator_ids', [])),
                                                                         Curator.profile_id == pid)).all()]
            sections.append(out)
        delivered = entry_ids
        if preview:
            size = edition.SECTION_SIZES
            limits = {'lead': 1, 'stories': max(0, min(stories, size['stories'])),
                      'reads': max(0, min(reads, size['reads'])), 'memes': max(0, min(memes, size['memes']))}
            delivered = [e['item_id'] for sec in ed.sections
                         for e in sec.get('entries', [])[:limits.get(sec['key'], 0)]]
        shown = [i for i in delivered if i in found]
        if set(shown) - set(ed.shown_item_ids):
            ed.shown_item_ids = list(dict.fromkeys([*ed.shown_item_ids, *shown]))
            s.add(ed)
            s.commit()
        return {'day': ed.day, 'built_at': ed.built_at, 'updated_at': ed.updated_at,
                'intro': ed.intro, 'intro_refs': _refs(found, ed.intro_refs), 'sections': sections,
                'rerun': get_kv(f'watchcraft_master_manual:{pid}', {}, s=s),
                'set_aside': _set_aside_count(s, pid, now), 'curated': views.has_verdicts(s, pid)}


@router.post('/today/rerun')
def rerun(pid: int = Depends(profile_id)):
    """Durably request a fresh selection, without interrupting an active editorial pass."""
    from ..db import get_settings
    from sqlalchemy.dialects.sqlite import insert
    from .models import MasterQueue
    if not get_settings()['hub_master'].get('enabled', True):
        raise HTTPException(409, 'Enable the master curator in settings first.')
    now = time.time()
    with session() as s:
        key = f'watchcraft_master_manual:{pid}'
        state = get_kv(key, {}, s=s)
        if state.get('requested_at', 0) > state.get('completed_at', 0):
            return state
        state = {'requested_at': now, 'completed_at': state.get('completed_at', 0)}
        save_setting(key, state, s=s)
        s.exec(insert(MasterQueue).values(profile_id=pid, first_at=now, due_at=now, lease_until=0, gen=1, attempts=0)
               .on_conflict_do_update(index_elements=['profile_id'], set_={'due_at': now, 'gen': MasterQueue.gen + 1, 'attempts': 0}))
        s.commit()
    return state


@router.get('/today/set-aside')
def set_aside(type: str = '', offset: int = 0, limit: int = 30, pid: int = Depends(profile_id)):
    """What the curator left out this week, with its reasons, so the reader can check and overrule."""
    limit = max(1, min(limit, 100))
    now = time.time()
    with session() as s:
        stmt = (select(ContentItem.id).join(ItemVerdict, ItemVerdict.item_id == ContentItem.id)
                .where(ItemVerdict.profile_id == pid, ItemVerdict.tier == 'set_aside', views.visible(pid, ItemVerdict.item_id),
                       ContentItem.first_seen >= now - SET_ASIDE_DAYS * 86400))
        types = [t.strip() for t in type.split(',') if t.strip()]
        if types:
            stmt = stmt.where(ContentItem.type.in_(types))
        total = s.exec(select(func.count()).select_from(stmt.subquery())).one()
        ids = list(s.exec(stmt.order_by(ContentItem.first_seen.desc(), ContentItem.id.desc()).offset(max(0, offset)).limit(limit)).all())
        found = _items(s, pid, ids)
        return {'items': [found[i] for i in ids if i in found], 'total': total}


class OverruleIn(BaseModel):
    action: str = Input(pattern='^(promote|demote|clear)$')


@router.post('/items/{iid}/overrule')
def overrule(iid: int, body: OverruleIn, pid: int = Depends(profile_id)):
    """The reader knows best: show this after all, take it off the front page, or hand it back to the curator."""
    with session() as s:
        if not can_see(s, pid, iid):
            raise HTTPException(404, 'Item not found')
        v = s.get(ItemVerdict, (pid, iid))
        if v is None and body.action == 'clear':
            return None  # nothing to hand back
        v = v or ItemVerdict(profile_id=pid, item_id=iid)
        if body.action == 'clear':
            v.overruled, v.overruled_at, v.forced = '', None, False  # the queued pass re-judges it
        else:
            v.overruled = 'promoted' if body.action == 'promote' else 'demoted'
            v.overruled_at = time.time()
            v.tier, v.forced = ('feature' if body.action == 'promote' else 'set_aside'), True
            v.reasons = [f'you {v.overruled} it', *[r for r in v.reasons if not r.startswith('you ')]][:8]
        v.judged_at = time.time()
        s.add(v)
        s.commit()
        edition.patch(s, pid)
        out = _verdict_dict(s.get(ItemVerdict, (pid, iid)))
    schedule.mark(pid)  # re-tier around the change, and let the next pass learn from it
    return out


@router.get('/items/{iid}/why')
def explain(iid: int, pid: int = Depends(profile_id)):
    """Why the curator showed, kept or set this item aside."""
    with session() as s:
        if not can_see(s, pid, iid):
            raise HTTPException(404, 'Item not found')
        out = {'verdict': _verdict_dict(s.get(ItemVerdict, (pid, iid))), 'story': None}
        link = s.get(StoryItem, iid)
        if link:
            out['story'] = {'id': link.story_id, 'sources': edition._sources(s, pid, [link.story_id]).get(link.story_id, 1)}
        return out


@router.get('/stories/{sid}')
def story(sid: int, pid: int = Depends(profile_id)):
    """Curated, visible sources for one story, matching the news feed's filters."""
    with session() as s:
        muted = [k.lower() for t in s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news', Topic.muted)).all()
                 for k in (t.keywords or [t.name])]
        rows = list(s.exec(item_query(pid, section='news')
                           .join(StoryItem, StoryItem.item_id == ContentItem.id)
                           .outerjoin(ItemVerdict, (ItemVerdict.item_id == ContentItem.id) & (ItemVerdict.profile_id == pid))
                           .where(StoryItem.story_id == sid, views.curated(pid, ContentItem.id))
                           .order_by(func.coalesce(ItemVerdict.final, 50).desc(),
                                     func.coalesce(ContentItem.published_at, ContentItem.first_seen).desc())).all())
        rows = [item for item in rows if not any(word in f'{item.title} {item.summary}'.lower() for word in muted)]
        ids = [item.id for item in rows]
        found = _items(s, pid, ids)
        if not found:
            raise HTTPException(404, 'Story not found')
        items = [found[i] for i in ids if i in found]
        return {'id': sid, 'title': items[0]['title'], 'items': items}


# ---------- learned preferences

class LessonIn(BaseModel):
    text: str = Input(min_length=3, max_length=300)
    scope: str = Input(default='', pattern='^(|meme|news|article|web|project|document|advisory)$')
    polarity: Literal[1, -1] = 1


class LessonPatch(BaseModel):
    text: str | None = Input(default=None, min_length=3, max_length=300)
    polarity: Literal[1, -1] | None = None
    active: bool | None = None


def _lesson(l: HubLesson) -> dict:
    return l.model_dump(exclude={'source_item_ids'})


def _refresh_preference_core(s: Session, pid: int) -> None:
    from .master.prefs import rebuild_core
    rebuild_core(s, pid)


@router.get('/curator/preferences')
def preferences(pid: int = Depends(profile_id)):
    with session() as s:
        core = s.get(HubPreferenceCore, pid)
        lessons = s.exec(select(HubLesson).where(HubLesson.profile_id == pid).order_by(HubLesson.active.desc(), HubLesson.weight.desc(),
                                                                                  HubLesson.updated_at.desc())).all()
        return {'summary': core.text if core else '', 'built_at': core.built_at if core else None,
                'lessons': [_lesson(l) for l in lessons]}


@router.post('/curator/preferences')
def add_preference(body: LessonIn, pid: int = Depends(profile_id)):
    with session() as s:
        if s.exec(select(func.count()).select_from(HubLesson).where(HubLesson.profile_id == pid)).one() >= 200:
            raise HTTPException(422, 'Remove a preference before adding another.')
        l = HubLesson(profile_id=pid, text=body.text.strip(), scope=body.scope, polarity=body.polarity, user_edited=True)
        s.add(l); s.flush()
        _refresh_preference_core(s, pid)
        s.commit(); s.refresh(l)
        out = _lesson(l)
    schedule.mark(pid)
    return out


def _own_lesson(s: Session, pid: int, lid: int) -> HubLesson:
    l = s.get(HubLesson, lid)
    if not l or l.profile_id != pid:
        raise HTTPException(404, 'Preference not found')
    return l


@router.patch('/curator/preferences/{lid}')
def edit_preference(lid: int, body: LessonPatch, pid: int = Depends(profile_id)):
    with session() as s:
        l = _own_lesson(s, pid, lid)
        if body.text is not None:
            l.text = body.text.strip()
        if body.polarity is not None:
            l.polarity = body.polarity
        if body.active is not None:
            l.active = body.active
        l.user_edited, l.updated_at = True, time.time()
        s.add(l)
        _refresh_preference_core(s, pid)
        s.commit(); s.refresh(l)
        out = _lesson(l)
    schedule.mark(pid)
    return out


@router.delete('/curator/preferences/{lid}')
def delete_preference(lid: int, pid: int = Depends(profile_id)):
    with session() as s:
        s.delete(_own_lesson(s, pid, lid))
        _refresh_preference_core(s, pid)
        s.commit()
    schedule.mark(pid)
    return {'ok': True}
