"""Master curator endpoints: the daily edition, what was set aside, overrules, stories,
explanations and the learned preferences the curator reads."""
import re
import time
from contextlib import nullcontext
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field as Input
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..access import require_hub
from ..api.core import profile_id
from ..db import session, get_kv, save_setting
from .api import can_see, item_query, serialize
from .master import edition, schedule, views
from . import frontpage, kidsafe
from .models import (Alert, Briefing, BriefingIssue, CapturedAsset, Collection, CollectionItem, ContentItem, Curator, CuratorDiscovery,
                     Edition, HubLesson, HubPreferenceCore, ItemState, ItemVerdict, MemeExample, StoryItem, Topic, TopicItem)

router = APIRouter(prefix='/hub', tags=['The Hub'], dependencies=[Depends(require_hub)])
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


def _kid_entry(e: dict, found: dict[int, dict]) -> dict:
    """Edition entry for a child: editorial text drops out when it cites an item the child cannot see."""
    refs = e.get('summary_refs') or []
    out = {k: v for k, v in e.items() if k != 'summary_refs'}
    out['item'] = found[e['item_id']]
    if any(r not in found for r in refs):
        for k in ('summary', 'why', 'note'):
            out.pop(k, None)
    elif refs:
        out['summary_refs'] = _refs(found, refs)
    return out


def _excerpt(text: str, limit: int = 280) -> str:
    """Plain teaser of generated prose: no citations or markdown, cut at a word."""
    text = re.sub(r'\s*\[\d+(?:\s*,\s*\d+)*\]', '', text or '')
    text = re.sub(r'^\s*(?:#+|[-•]|\d+\.)\s+|[*_`>]+', '', text, flags=re.M)
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0].rstrip(',;:') + '…'


def _set_aside_count(s: Session, pid: int, now: float) -> int:
    return s.exec(select(func.count()).select_from(ItemVerdict).join(ContentItem, ContentItem.id == ItemVerdict.item_id)
                  .where(ItemVerdict.profile_id == pid, ItemVerdict.tier == 'set_aside', views.visible(pid, ItemVerdict.item_id),
                         ContentItem.first_seen >= now - SET_ASIDE_DAYS * 86400)).one()


def _record_delivery(pid: int, ed_id: int, shown: list[int]) -> None:
    """Merge delivery into the latest row after sending the prepared response."""
    with edition.EDITION_LOCK, session() as s:
        ed = s.get(Edition, ed_id)
        if ed is None or ed.profile_id != pid or not set(shown) - set(ed.shown_item_ids):
            return
        ed.shown_item_ids = list(dict.fromkeys([*ed.shown_item_ids, *shown]))
        s.add(ed)
        s.commit()


@router.get('/today')
def today(background: BackgroundTasks, preview: bool = False, stories: int = 8, reads: int = 6, memes: int = 4,
          prepared: bool = False, pid: int = Depends(profile_id)):
    """Render a prepared edition with current visibility and profile state."""
    now = time.time()
    with (nullcontext() if prepared else edition.EDITION_LOCK), session() as s:
        day = edition.today(now)
        previous = False
        if prepared:
            ed = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day == day)).first()
            if ed is None:
                frontpage.request_refresh(pid)
                yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
                ed = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day == yesterday)).first()
                previous = ed is not None
            if ed is None:
                return {'day': day, 'built_at': 0, 'updated_at': 0, 'intro': '', 'intro_refs': [],
                        'sections': [], 'set_aside': 0, 'curated': False, 'preparing': True}
        else:
            try:
                ed = edition.patch(s, pid, now)
            except IntegrityError:
                s.rollback()
                ed = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day == day)).first()
                if ed is None:
                    raise
        selected = ed.sections
        if prepared and preview:
            caps = {'lead': 1, 'stories': stories, 'reads': reads, 'memes': memes}
            selected = [
                {**sec, 'entries': sec['entries'][:max(0, min(caps.get(sec['key'], 0),
                                                             edition.SECTION_SIZES.get(sec['key'], 1)))]}
                if 'entries' in sec else sec for sec in selected
                if sec.get('key') in caps
            ]
        entry_ids = [e['item_id'] for sec in selected for e in sec.get('entries', [])]
        refs = [*ed.intro_refs, *(r for sec in selected for e in sec.get('entries', []) for r in e.get('summary_refs', []))]
        visible = views.visible_set(s, pid, set(entry_ids) | set(refs))
        if prepared and visible:
            visible &= set(s.exec(select(ContentItem.id).where(ContentItem.id.in_(visible),
                                                               views.curated(pid, ContentItem.id))).all())
        found = _items(s, pid, list(visible))
        showable = found.keys() if not prepared else {
            iid for iid, item in found.items()
            if not any(item['state'].get(key) for key in ('read_at', 'dismissed', 'archived'))
        }
        kid = views.child(pid)
        # A child never reads editorial text written partly from items it may not see.
        intro, intro_refs = ed.intro, ed.intro_refs
        if (kid or prepared) and any(r not in found for r in intro_refs):
            intro, intro_refs = '', []
        sections = []
        for sec in selected:
            out = {k: v for k, v in sec.items() if k != 'entries'}
            if 'entries' in sec:
                out['entries'] = [_kid_entry(e, found) if kid or prepared else
                                  {**{k: v for k, v in e.items() if k not in ('summary_refs',)},
                                   'item': found[e['item_id']],
                                   **({'summary_refs': _refs(found, e['summary_refs'])} if e.get('summary_refs') else {})}
                                  for e in sec['entries'] if e['item_id'] in showable]
            if sec.get('key') == 'research':
                # A child's teaser would be written partly from sources it may not see.
                out['curators'] = [{'id': c.id, 'name': c.name, 'subject': c.subject, 'report_at': c.report_at,
                                    'excerpt': '' if kid else _excerpt(c.report)}
                                   for c in s.exec(select(Curator).where(Curator.id.in_(sec.get('curator_ids', [])),
                                                                         Curator.profile_id == pid)).all()]
            if sec.get('key') == 'briefings':
                if kid:
                    continue
                rows = s.exec(select(BriefingIssue, Briefing).join(Briefing, Briefing.id == BriefingIssue.briefing_id)
                              .where(BriefingIssue.id.in_(sec.get('issue_ids', [])), Briefing.profile_id == pid)
                              .order_by(BriefingIssue.created_at.desc())).all()
                out['issues'] = [{'id': i.id, 'briefing_id': b.id, 'name': b.name, 'created_at': i.created_at,
                                  'ai': i.ai, 'excerpt': _excerpt(i.content)} for i, b in rows]
                if not out['issues']:
                    continue
            sections.append(out)
        delivered = entry_ids
        if preview and not prepared:
            size = edition.SECTION_SIZES
            limits = {'lead': 1, 'stories': max(0, min(stories, size['stories'])),
                      'reads': max(0, min(reads, size['reads'])), 'memes': max(0, min(memes, size['memes']))}
            delivered = [e['item_id'] for sec in ed.sections
                         for e in sec.get('entries', [])[:limits.get(sec['key'], 0)]]
        shown = [i for i in delivered if i in showable]
        if not previous and set(shown) - set(ed.shown_item_ids):
            if prepared:
                background.add_task(_record_delivery, pid, ed.id, shown)
            else:
                ed.shown_item_ids = list(dict.fromkeys([*ed.shown_item_ids, *shown]))
                s.add(ed)
                s.commit()
        dirty = prepared and any(i not in showable for i in entry_ids)
        if dirty:
            frontpage.request_refresh(pid)
        return {'day': ed.day, 'built_at': ed.built_at, 'updated_at': ed.updated_at,
                'intro': intro, 'intro_refs': _refs(found, intro_refs), 'sections': sections,
                'preparing': previous or dirty, 'previous': previous,
                'rerun': get_kv(f'watchcraft_master_manual:{pid}', {}, s=s),
                'set_aside': 0 if kid else _set_aside_count(s, pid, now), 'curated': views.has_verdicts(s, pid)}


@router.post('/today/regenerate')
def regenerate(pid: int = Depends(profile_id)):
    """Rebuild today's selection now, then ask the master curator to re-judge and rewrite the prose."""
    from ..db import get_settings
    with session() as s:
        edition.regenerate(s, pid)
    if not get_settings()['hub_master'].get('enabled', True):
        return {'rebuilt': True, 'rerun': None}
    return {'rebuilt': True, 'rerun': rerun(pid)}


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
        if s.exec(select(func.count()).select_from(HubLesson).where(HubLesson.profile_id == pid,
                                                                    HubLesson.active == True)).one() >= 200:  # noqa: E712
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
        lesson = _own_lesson(s, pid, lid)
        from ..knowledge import teachings
        teachings.tombstone_deleted(s, pid, 'hub_lesson', lesson.id)  # deleting is rejecting: never relearn it
        s.delete(lesson)
        _refresh_preference_core(s, pid)
        s.commit()
    schedule.mark(pid)
    return {'ok': True}


# ---------- Front Page overview: what is waiting on each curated page

OVERVIEW_GROUPS = 10
OVERVIEW_FRESH = 10 * 60  # a pre-built snapshot younger than this is served as is
OVERVIEW_PREVIEWS = 6
OVERVIEW_SCAN = 1500  # newest waiting items considered per page; counts stay exact
ALMANAC_DAYS = 7
# Overview panel → the parental switch that opens its page.
OVERVIEW_SWITCH = {'wire': 'wire', 'finds': 'finds', 'memes': 'commons', 'stacks': 'stacks',
                   'almanac': 'almanac', 'alerts': 'alerts'}


def _pictured(s: Session, pid: int, ids: list[int], kid: bool) -> set[int]:
    """Items whose captured picture this profile may see."""
    if not ids:
        return set()
    images = set(s.exec(select(CapturedAsset.item_id).where(CapturedAsset.kind == 'image', CapturedAsset.item_id.in_(ids))).all())
    if kid:
        images &= kidsafe.image_set(s, images, pid)
    return images


def _panel(s: Session, pid: int, kid: bool, rows: list[tuple[int, float]], now: float,
           groups: list[tuple[str, str, str, list[int]]], total: int | None = None) -> dict:
    """rows: (item_id, when) newest first. groups: (key, label, href, item_ids)."""
    ids = [i for i, _ in rows]
    when = dict(rows)
    pictured = _pictured(s, pid, ids[:400], kid)
    tiers = dict(s.exec(select(ItemVerdict.item_id, ItemVerdict.tier).where(
        ItemVerdict.profile_id == pid, ItemVerdict.item_id.in_(ids[:400]))).all()) if ids else {}
    rank = sorted(ids[:400], key=lambda i: (i in pictured, tiers.get(i) == 'feature', when[i]), reverse=True)
    picks = rank[:OVERVIEW_PREVIEWS]
    items = {i.id: i for i in s.exec(select(ContentItem).where(ContentItem.id.in_(picks))).all()} if picks else {}
    previews = [{'item_id': i, 'title': items[i].title, 'meta': items[i].publisher or items[i].author or '',
                 'summary': _teaser(items[i]), 'has_image': i in pictured, 'at': when[i]} for i in picks if i in items]
    out_groups = []
    for key, label, href, members in sorted(groups, key=lambda g: -len(g[3]))[:OVERVIEW_GROUPS]:
        cover = next((m for m in sorted(members, key=lambda m: -when.get(m, 0)) if m in pictured), None)
        out_groups.append({'key': key, 'label': label, 'href': href, 'count': len(members), 'image_item_id': cover})
    return {'waiting': total if total is not None else len(ids),
            'new_today': sum(1 for _, t in rows if t >= now - 86400),
            'groups': out_groups, 'previews': previews}


def _teaser(item: ContentItem) -> str:
    """Short plain summary for a Front Page card: the archived AI summary, else the source's own."""
    ai = (item.enrichment or {}).get('summary')
    text = ai if isinstance(ai, str) and ai.strip() else item.summary or ''
    text = re.sub(r'<[^>]+>', ' ', text)
    return _excerpt(text, 240) if text.strip() != item.title.strip() else ''


def _by(pairs, ids: set[int]) -> dict:
    out: dict = {}
    for gid, iid in pairs:
        if iid in ids:
            out.setdefault(gid, []).append(iid)
    return out


@router.get('/today/overview')
def today_overview(prepared: bool = False, pid: int = Depends(profile_id)):
    """The Front Page summary, pre-built by the scheduler (frontpage.tick); built now when stale."""
    # Children always get a live build (cheap for their small feeds): a switch or kid-safe verdict
    # changed since the snapshot must never show them a title.
    kid = views.child(pid)
    snap = {} if kid else get_kv(f'frontpage_overview:{pid}', {})
    if snap.get('pages') is not None and time.time() - snap.get('generated_at', 0) < OVERVIEW_FRESH:
        return snap
    if prepared and not kid:
        frontpage.request_refresh(pid)
        return snap if snap.get('pages') is not None else {'generated_at': 0, 'pages': {}}
    data = build_overview(pid)
    save_setting(f'frontpage_overview:{pid}', data)
    return data


def build_overview(pid: int) -> dict:
    """Per curated page: how much is waiting (unread, not set aside), how much arrived today, the
    sub-categories it falls into and a few pictured previews. Each page obeys its parental switch."""
    from ..models import Profile
    from .. import parental
    now = time.time()
    with session() as s:
        kid = views.child(pid)
        prof = s.get(Profile, pid)
        allowed = {k for k, sw in OVERVIEW_SWITCH.items() if parental.allowed(prof, sw)}
        st = ItemState
        done = select(st.item_id).where(st.profile_id == pid, or_(st.read_at != None, st.dismissed == True))  # noqa: E711
        found_at = func.max(CuratorDiscovery.found_at)
        rows = s.exec(select(ContentItem.id, ContentItem.type, found_at)
                      .join(CuratorDiscovery, CuratorDiscovery.item_id == ContentItem.id)
                      .where(CuratorDiscovery.profile_id == pid, views.curated(pid, ContentItem.id), ContentItem.id.not_in(done))
                      .group_by(ContentItem.id).order_by(found_at.desc())).all()
        news = [(i, t) for i, kind, t in rows if kind in ('news', 'article')]
        memes = [(i, t) for i, kind, t in rows if kind == 'meme']
        finds = [(i, t) for i, kind, t in rows if kind not in ('news', 'article', 'meme')]
        curators = {c.id: c for c in s.exec(select(Curator).where(Curator.profile_id == pid)).all()}

        def curator_groups(part, href):
            ids = {i for i, _ in part[:OVERVIEW_SCAN]}
            pairs = s.exec(select(CuratorDiscovery.curator_id, CuratorDiscovery.item_id).where(
                CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.in_(ids))).all() if ids else []
            return [(f'curator:{c}', curators[c].name, href(c), m) for c, m in _by(pairs, ids).items() if c in curators]

        pages: dict[str, dict] = {}
        if 'wire' in allowed:
            ids = {i for i, _ in news[:OVERVIEW_SCAN]}
            topics = {t.id: t for t in s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news', Topic.muted == False)).all()}  # noqa: E712
            pairs = s.exec(select(TopicItem.topic_id, TopicItem.item_id).where(TopicItem.topic_id.in_(topics), TopicItem.item_id.in_(ids))).all() if ids and topics else []
            groups = [(f'topic:{t}', topics[t].name, f'/news?topic={t}', m) for t, m in _by(pairs, ids).items()]
            if len(groups) < 3:
                groups += curator_groups(news, lambda c: f'/discover?curator={c}&type=news,article')
            pages['wire'] = _panel(s, pid, kid, news[:OVERVIEW_SCAN], now, groups, len(news))
        if 'finds' in allowed:
            pages['finds'] = _panel(s, pid, kid, finds[:OVERVIEW_SCAN], now,
                                    curator_groups(finds, lambda c: f'/discover?curator={c}&tab=unread'), len(finds))
        if 'memes' in allowed:
            ids = {i for i, _ in memes[:OVERVIEW_SCAN]}
            ex = s.exec(select(MemeExample.community, MemeExample.item_id).where(
                MemeExample.item_id.in_(ids), MemeExample.has_media == True, MemeExample.dup_of == None)).all() if ids else []  # noqa: E711,E712
            keep = {i for _, i in ex}
            part = [(i, t) for i, t in memes if i in keep or i not in ids]
            groups = [(f'community:{c}', c, f'/memes?community={c}', m) for c, m in _by([(c, i) for c, i in ex if c], keep).items()]
            pages['memes'] = _panel(s, pid, kid, part[:OVERVIEW_SCAN], now, groups, len(part))
        if 'stacks' in allowed:
            lib = s.exec(select(st.item_id, func.coalesce(st.saved_at, 0)).where(
                st.profile_id == pid, st.read_at == None, or_(st.archived == False, st.archived == None),  # noqa: E711,E712
                or_(st.saved == True, st.favorite == True, st.read_later == True), views.visible(pid, st.item_id))  # noqa: E712
                .order_by(func.coalesce(st.saved_at, 0).desc())).all()
            ids = {i for i, _ in lib[:OVERVIEW_SCAN]}
            cols = {c.id: c for c in s.exec(select(Collection).where(Collection.profile_id == pid)).all()}
            pairs = s.exec(select(CollectionItem.collection_id, CollectionItem.item_id).where(
                CollectionItem.collection_id.in_(cols), CollectionItem.item_id.in_(ids))).all() if ids and cols else []
            groups = [(f'collection:{c}', f'{cols[c].icon} {cols[c].name}'.strip(), f'/library?view=collections&collection={c}', m)
                      for c, m in _by(pairs, ids).items()]
            later = s.exec(select(st.item_id).where(st.profile_id == pid, st.read_later == True, st.item_id.in_(ids))).all() if ids else []  # noqa: E712
            if later:
                groups.append(('later', 'Read later', '/library?view=later', list(later)))
            pages['stacks'] = _panel(s, pid, kid, list(lib[:OVERVIEW_SCAN]), now, groups, len(lib))
        if 'almanac' in allowed:
            topics = {t.id: t for t in s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'knowledge', Topic.muted == False)).all()}  # noqa: E712
            added = s.exec(select(TopicItem.topic_id, TopicItem.item_id, TopicItem.added_at).where(
                TopicItem.topic_id.in_(topics), TopicItem.added_at >= now - ALMANAC_DAYS * 86400,
                views.visible(pid, TopicItem.item_id)).order_by(TopicItem.added_at.desc())).all() if topics else []
            latest: dict[int, float] = {}
            for _, i, t in added:
                latest.setdefault(i, t)
            ids = set(latest)
            groups = [(f'topic:{t}', topics[t].name, f'/knowledge/{t}', m) for t, m in _by([(t, i) for t, i, _ in added], ids).items()]
            pages['almanac'] = _panel(s, pid, kid, list(latest.items())[:OVERVIEW_SCAN], now, groups)
        if 'alerts' in allowed:
            alerts = s.exec(select(Alert).where(Alert.profile_id == pid, Alert.state == 'new',
                                                or_(Alert.snooze_until == None, Alert.snooze_until < now))  # noqa: E711
                            .order_by(Alert.created_at.desc())).all()
            alerts = [a for a in alerts if not kid or a.item_id in views.visible_set(s, pid, [a.item_id])]
            pages['alerts'] = {'waiting': len(alerts), 'new_today': sum(1 for a in alerts if a.created_at >= now - 86400),
                               'groups': [], 'previews': [{'item_id': a.item_id, 'title': a.title, 'meta': a.severity,
                                                           'has_image': False, 'at': a.created_at} for a in alerts[:OVERVIEW_PREVIEWS]]}
        return {'generated_at': now, 'pages': pages}
