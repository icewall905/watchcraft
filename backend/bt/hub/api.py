"""/api/hub — WatchCraft Hub endpoints. Contract: docs/HUB.md. All data is profile-scoped."""
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field as Input
from sqlalchemy import func, or_, text
from sqlmodel import Session, delete, select

from .. import curation
from ..access import hub_allowed, require_hub
from ..api.core import profile_id
from ..db import session
from ..models import AgentEvent
from . import ai, engine, scout
from .models import (Alert, Briefing, BriefingIssue, Collection, CollectionItem, ContentItem, Curator, CuratorDiscovery,
                     CuratorRun, CuratorSource, ItemState, SavedSearch, ScoutPlan, ScoutRun, Source, Topic, TopicItem)
from .providers import PROVIDERS, TYPE_KEYS, TYPES
from .templates import TEMPLATES

router = APIRouter(prefix='/hub', tags=['WatchCraft Hub'], dependencies=[Depends(require_hub)])
MAX_CURATORS = 50


# ---------- helpers

def owned(s: Session, model, oid: int, pid: int):
    row = s.get(model, oid)
    if not row or getattr(row, 'profile_id', None) != pid:
        raise HTTPException(404, f'{model.__name__} not found')
    return row


def visible_ids(pid: int):
    """Item ids this profile may see: found by its curators, or touched by it."""
    return select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid).union(
        select(ItemState.item_id).where(ItemState.profile_id == pid))


def can_see(s: Session, pid: int, item_id: int) -> bool:
    return bool(s.exec(select(CuratorDiscovery.id).where(CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id == item_id)).first()
                or s.get(ItemState, (pid, item_id)))


def state_dict(st: ItemState | None) -> dict:
    if not st:
        return {'saved': False, 'favorite': False, 'read_later': False, 'archived': False, 'dismissed': False,
                'read_at': None, 'notes': '', 'tags': [], 'rating': 0}
    return {k: getattr(st, k) for k in ('saved', 'favorite', 'read_later', 'archived', 'dismissed', 'read_at', 'notes', 'tags', 'rating')}


def serialize(s: Session, pid: int, items: list[ContentItem]) -> list[dict]:
    if not items:
        return []
    ids = [i.id for i in items]
    states = {st.item_id: st for st in s.exec(select(ItemState).where(ItemState.profile_id == pid, ItemState.item_id.in_(ids))).all()}
    finds: dict[int, list] = {}
    for d, name in s.exec(select(CuratorDiscovery, Curator.name).join(Curator, Curator.id == CuratorDiscovery.curator_id)
                          .where(CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.in_(ids))).all():
        finds.setdefault(d.item_id, []).append((d, name))
    out = []
    for i in items:
        f = sorted(finds.get(i.id, []), key=lambda x: -(x[0].score or 0))
        out.append({**i.model_dump(exclude={'body'}), 'state': state_dict(states.get(i.id)),
                    'score': f[0][0].score if f else None, 'reason': f[0][0].reason if f else '',
                    'curator_names': list(dict.fromkeys(n for _, n in f))})
    return out


def _clip_list(v, n=20, size=200) -> list[str]:
    return [str(x).strip()[:size] for x in (v or []) if str(x).strip()][:n]


# ---------- registry / counts

@router.get('/registry')
def registry():
    return {'types': [{'key': k, 'label': l, 'icon': i} for k, l, i in TYPES], 'providers': [p.info() for p in PROVIDERS.values()]}


@router.get('/counts')
def counts(pid: int = Depends(profile_id)):
    with session() as s:
        read = select(ItemState.item_id).where(ItemState.profile_id == pid, or_(ItemState.read_at != None, ItemState.dismissed == True))  # noqa: E711
        unread = s.exec(select(func.count(func.distinct(CuratorDiscovery.item_id))).where(
            CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.not_in(read))).one()
        alerts = s.exec(select(func.count()).select_from(Alert).where(Alert.profile_id == pid, Alert.state == 'new',
                                                                       or_(Alert.snooze_until == None, Alert.snooze_until < time.time()))).one()  # noqa: E711
        news = s.exec(select(func.count(func.distinct(ContentItem.id))).join(CuratorDiscovery, CuratorDiscovery.item_id == ContentItem.id).where(
            CuratorDiscovery.profile_id == pid, ContentItem.type.in_(['news', 'article']),
            func.coalesce(ContentItem.published_at, ContentItem.first_seen) >= time.time() - 86400)).one()
        return {'discover_unread': unread, 'alerts_new': alerts, 'news_today': news}


# ---------- items

def fts_query(q: str) -> str:
    """User text → safe FTS5 query: quoted phrases kept, -term excluded, others prefix-matched."""
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]*"', ' ', q)
    pos, neg = [], []
    for tok in re.findall(r'-?[\w.\-]+', rest):
        (neg if tok.startswith('-') and len(tok) > 1 else pos).append(tok.lstrip('-'))
    clean = lambda t: '"' + t.replace('"', '') + '"'  # noqa: E731
    parts = [clean(p) for p in phrases] + [clean(t) + '*' for t in pos if t]
    expr = ' AND '.join(parts)
    if expr and neg:
        expr += ' ' + ' '.join(f'NOT {clean(t)}' for t in neg)
    return expr


def item_query(pid: int, *, section='discover', q='', type='', provider='', curator: int | None = None, saved=False,
               unread=False, favorite=False, read_later=False, archived=False, min_score: float | None = None,
               since: float | None = None, until: float | None = None, collection: int | None = None, topic: int | None = None):
    st = ItemState
    stmt = select(ContentItem).outerjoin(st, (st.item_id == ContentItem.id) & (st.profile_id == pid))
    if section in ('library', 'all') or saved or favorite or read_later or archived or collection or topic:
        stmt = stmt.where(ContentItem.id.in_(visible_ids(pid)))
    else:
        stmt = stmt.where(ContentItem.id.in_(select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)))
        stmt = stmt.where(or_(st.dismissed == None, st.dismissed == False))  # noqa: E711
    if section == 'library':
        stmt = stmt.where(or_(st.saved == True, st.favorite == True, st.read_later == True, st.archived == True))
        if not archived:
            stmt = stmt.where(or_(st.archived == None, st.archived == False))  # noqa: E711
    if section == 'news':
        stmt = stmt.where(ContentItem.type.in_(['news', 'article']))
    if q:
        expr = fts_query(q)
        if expr:
            stmt = stmt.where(ContentItem.id.in_(text('SELECT rowid FROM content_fts WHERE content_fts MATCH :q').bindparams(q=expr)))
    if type:
        stmt = stmt.where(ContentItem.type.in_(type.split(',')))
    if provider:
        stmt = stmt.where(ContentItem.provider.in_(provider.split(',')))
    if curator or min_score is not None:
        sub = select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)
        if curator:
            sub = sub.where(CuratorDiscovery.curator_id == curator)
        if min_score is not None:
            sub = sub.where(CuratorDiscovery.score >= min_score)
        stmt = stmt.where(ContentItem.id.in_(sub))
    if saved: stmt = stmt.where(st.saved == True)
    if favorite: stmt = stmt.where(st.favorite == True)
    if read_later: stmt = stmt.where(st.read_later == True)
    if archived and section == 'library': stmt = stmt.where(st.archived == True)
    if unread: stmt = stmt.where(st.read_at == None)  # noqa: E711
    when = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
    if since and section == 'discover':
        # Discover is about what curators found recently, whatever the publication date.
        stmt = stmt.where(ContentItem.id.in_(select(CuratorDiscovery.item_id).where(
            CuratorDiscovery.profile_id == pid, CuratorDiscovery.found_at >= since)))
    elif since: stmt = stmt.where(when >= since)
    if until: stmt = stmt.where(when <= until)
    if collection:
        stmt = stmt.where(ContentItem.id.in_(select(CollectionItem.item_id).where(CollectionItem.collection_id == collection)))
    if topic:
        stmt = stmt.where(ContentItem.id.in_(select(TopicItem.item_id).where(TopicItem.topic_id == topic)))
    return stmt


@router.get('/items')
def items(section: str = 'discover', q: str = '', type: str = '', provider: str = '', curator: int | None = None,
          saved: bool = False, unread: bool = False, favorite: bool = False, read_later: bool = False, archived: bool = False,
          min_score: float | None = None, since: float | None = None, until: float | None = None, sort: str = 'newest',
          collection: int | None = None, topic: int | None = None, offset: int = 0, limit: int = 30, pid: int = Depends(profile_id)):
    limit = max(1, min(limit, 100))
    with session() as s:
        if collection: owned(s, Collection, collection, pid)
        if topic: owned(s, Topic, topic, pid)
        stmt = item_query(pid, section=section, q=q, type=type, provider=provider, curator=curator, saved=saved, unread=unread,
                          favorite=favorite, read_later=read_later, archived=archived, min_score=min_score, since=since,
                          until=until, collection=collection, topic=topic)
        total = s.exec(select(func.count()).select_from(stmt.subquery())).one()
        if sort == 'relevance':
            best = select(func.max(CuratorDiscovery.score)).where(CuratorDiscovery.item_id == ContentItem.id,
                                                                  CuratorDiscovery.profile_id == pid).scalar_subquery()
            stmt = stmt.order_by(best.desc().nulls_last(), ContentItem.first_seen.desc())
        elif sort == 'published':
            stmt = stmt.order_by(func.coalesce(ContentItem.published_at, ContentItem.first_seen).desc())
        elif sort == 'saved' and section == 'library':
            stmt = stmt.order_by(ItemState.saved_at.desc().nulls_last())
        else:
            stmt = stmt.order_by(ContentItem.first_seen.desc(), ContentItem.id.desc())
        rows = s.exec(stmt.offset(max(0, offset)).limit(limit)).all()
        return {'items': serialize(s, pid, list(rows)), 'total': total}


def related(s: Session, pid: int, item: ContentItem, n=6) -> list[ContentItem]:
    words = [w for w in re.findall(r'[\w-]{4,}', item.title.lower()) if w not in ai.STOP][:6]
    if not words:
        return []
    expr = ' OR '.join('"' + w + '"' for w in words)
    ids = [r[0] for r in s.exec(text('SELECT rowid FROM content_fts WHERE content_fts MATCH :q ORDER BY rank LIMIT 40').bindparams(q=expr)).all()]
    ids = [i for i in ids if i != item.id]
    if not ids:
        return []
    allowed = set(s.exec(select(ContentItem.id).where(ContentItem.id.in_(ids), ContentItem.id.in_(visible_ids(pid)))).all())
    keep = [i for i in ids if i in allowed][:n]
    found = {i.id: i for i in s.exec(select(ContentItem).where(ContentItem.id.in_(keep))).all()}
    return [found[i] for i in keep if i in found]


@router.get('/items/{iid}')
def item(iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        it = s.get(ContentItem, iid)
        if not it or not can_see(s, pid, iid):
            raise HTTPException(404, 'Item not found')
        out = serialize(s, pid, [it])[0]
        out['curators'] = [{'id': c.id, 'name': c.name, 'found_at': d.found_at, 'score': d.score, 'reason': d.reason, 'query': d.query}
                           for d, c in s.exec(select(CuratorDiscovery, Curator).join(Curator, Curator.id == CuratorDiscovery.curator_id)
                                              .where(CuratorDiscovery.item_id == iid, CuratorDiscovery.profile_id == pid)).all()]
        out['collections'] = list(s.exec(select(CollectionItem.collection_id).join(Collection, Collection.id == CollectionItem.collection_id)
                                         .where(CollectionItem.item_id == iid, Collection.profile_id == pid)).all())
        out['topics'] = list(s.exec(select(TopicItem.topic_id).join(Topic, Topic.id == TopicItem.topic_id)
                                    .where(TopicItem.item_id == iid, Topic.profile_id == pid)).all())
        out['related'] = serialize(s, pid, related(s, pid, it))
        return out


class StateIn(BaseModel):
    saved: bool | None = None
    favorite: bool | None = None
    read_later: bool | None = None
    archived: bool | None = None
    dismissed: bool | None = None
    read: bool | None = None
    notes: str | None = Input(default=None, max_length=20000)
    tags: list[str] | None = None
    rating: int | None = Input(default=None, ge=-1, le=1)


@router.post('/items/{iid}/state')
def set_state(iid: int, body: StateIn, pid: int = Depends(profile_id)):
    with session() as s:
        if not s.get(ContentItem, iid) or not can_see(s, pid, iid):
            raise HTTPException(404, 'Item not found')
        st = s.get(ItemState, (pid, iid)) or ItemState(profile_id=pid, item_id=iid)
        data = body.model_dump(exclude_none=True)
        if 'read' in data:
            st.read_at = (st.read_at or time.time()) if data.pop('read') else None
        if data.get('saved') and not st.saved:
            st.saved_at = time.time()
        if 'tags' in data:
            data['tags'] = list(dict.fromkeys(t.strip().lower()[:40] for t in data['tags'] if t.strip()))[:30]
        for k, v in data.items():
            setattr(st, k, v)
        s.add(st); s.commit(); s.refresh(st)
        return state_dict(st)


class UrlIn(BaseModel):
    url: str = Input(max_length=2000)


@router.post('/items/import')
def import_url(body: UrlIn, pid: int = Depends(profile_id)):
    """Manual URL submission: extract the page and save it to the Library."""
    url = body.url.strip()
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    try:
        data = PROVIDERS['page'].fetch(url, '')[0]
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        raise HTTPException(502, 'Could not fetch that page. Check the URL and that it is publicly reachable.')
    with session() as s:
        it = engine.upsert(s, data, 'page')
        if not it:
            raise HTTPException(422, 'Use a public HTTPS URL.')
        st = s.get(ItemState, (pid, it.id)) or ItemState(profile_id=pid, item_id=it.id)
        if not st.saved:
            st.saved, st.saved_at = True, time.time()
        s.add(st); s.commit()
        return serialize(s, pid, [it])[0]


def _title_key(title: str) -> set[str]:
    return {w for w in re.findall(r'[a-z0-9]{3,}', title.lower()) if w not in ai.STOP}


@router.get('/news')
def news(days: int = 1, topic: int | None = None, pid: int = Depends(profile_id)):
    """Recent articles grouped into stories by title overlap. Uses publication time, not discovery time."""
    since = time.time() - max(1, min(days, 90)) * 86400
    with session() as s:
        if topic is not None:
            owned(s, Topic, topic, pid)
        topics = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.kind == 'news')).all()
        stmt = item_query(pid, section='news', topic=None)
        when = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
        stmt = stmt.where(when >= since).order_by(when.desc()).limit(400)
        rows = list(s.exec(stmt).all())
        muted = [k.lower() for t in topics if t.muted for k in (t.keywords or [t.name])]
        follow = next((t for t in topics if t.id == topic), None)
        def keep(i):
            hay = f'{i.title} {i.summary}'.lower()
            if any(m in hay for m in muted):
                return False
            return not follow or any(k.lower() in hay for k in (follow.keywords or [follow.name]))
        rows = [i for i in rows if keep(i)]
        groups: list[dict] = []
        for i in rows:
            key = _title_key(i.title)
            for g in groups:
                if key and len(key & g['_k']) / max(1, min(len(key), len(g['_k']))) >= 0.5:
                    g['_items'].append(i); g['_k'] |= key; break
            else:
                groups.append({'_k': key, '_items': [i]})
        out = []
        for g in groups[:120]:
            its = sorted(g['_items'], key=lambda x: -(x.published_at or x.first_seen))
            out.append({'key': str(its[-1].id), 'title': its[-1].title, 'items': serialize(s, pid, its),
                        'latest': its[0].published_at or its[0].first_seen})
        out.sort(key=lambda g: (-len(g['items']) if days > 1 else 0, -g['latest']))
        return {'groups': out, 'topics': [topic_dict(s, t) for t in topics]}


# ---------- collections

class CollectionIn(BaseModel):
    name: str | None = Input(default=None, min_length=1, max_length=100)
    description: str | None = Input(default=None, max_length=1000)
    icon: str | None = Input(default=None, max_length=8)
    color: str | None = Input(default=None, max_length=20)
    view: str | None = Input(default=None, pattern='^(grid|list|compact|reader|gallery)$')
    pinned: bool | None = None


def collection_dict(s, c: Collection) -> dict:
    n = s.exec(select(func.count()).select_from(CollectionItem).where(CollectionItem.collection_id == c.id)).one()
    return {**c.model_dump(), 'count': n}


@router.get('/collections')
def collections(pid: int = Depends(profile_id)):
    with session() as s:
        return [collection_dict(s, c) for c in s.exec(select(Collection).where(Collection.profile_id == pid).order_by(Collection.name)).all()]


@router.post('/collections')
def create_collection(body: CollectionIn, pid: int = Depends(profile_id)):
    if not body.name:
        raise HTTPException(422, 'Name required')
    with session() as s:
        c = Collection(profile_id=pid, **body.model_dump(exclude_none=True))
        s.add(c); s.commit(); s.refresh(c)
        return collection_dict(s, c)


@router.patch('/collections/{cid}')
def update_collection(cid: int, body: CollectionIn, pid: int = Depends(profile_id)):
    with session() as s:
        c = owned(s, Collection, cid, pid)
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(c, k, v)
        s.add(c); s.commit(); s.refresh(c)
        return collection_dict(s, c)


@router.delete('/collections/{cid}')
def delete_collection(cid: int, pid: int = Depends(profile_id)):
    with session() as s:
        c = owned(s, Collection, cid, pid)
        s.exec(delete(CollectionItem).where(CollectionItem.collection_id == cid))
        for cur in s.exec(select(Curator).where(Curator.collection_id == cid)).all():
            cur.collection_id = None; s.add(cur)
        s.delete(c); s.commit()
        return {'ok': True}


@router.post('/collections/{cid}/items/{iid}')
def add_to_collection(cid: int, iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        owned(s, Collection, cid, pid)
        if not can_see(s, pid, iid):
            raise HTTPException(404, 'Item not found')
        if not s.get(CollectionItem, (cid, iid)):
            s.add(CollectionItem(collection_id=cid, item_id=iid))
        st = s.get(ItemState, (pid, iid)) or ItemState(profile_id=pid, item_id=iid)
        if not st.saved:
            st.saved, st.saved_at = True, time.time()
        s.add(st); s.commit()
        return {'ok': True}


@router.delete('/collections/{cid}/items/{iid}')
def remove_from_collection(cid: int, iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        owned(s, Collection, cid, pid)
        s.exec(delete(CollectionItem).where(CollectionItem.collection_id == cid, CollectionItem.item_id == iid)); s.commit()
        return {'ok': True}


# ---------- sources

class SourceIn(BaseModel):
    provider: str | None = None
    name: str | None = Input(default=None, max_length=100)
    url: str | None = Input(default=None, max_length=2000)
    enabled: bool | None = None


def source_dict(s, src: Source) -> dict:
    return {**src.model_dump(), 'curator_ids': list(s.exec(select(CuratorSource.curator_id).where(CuratorSource.source_id == src.id)).all())}


def _norm_source(provider: str, url: str) -> str:
    p = PROVIDERS.get(provider)
    if not p:
        raise HTTPException(422, 'Unknown provider')
    if not p.needs_url:
        return ''
    if not url:
        raise HTTPException(422, f'{p.label} needs a URL')
    try:
        return curation.canonical(url.strip())
    except ValueError as e:
        raise HTTPException(422, str(e))


def get_or_create_source(s, pid: int, provider: str, url: str = '', name: str = '') -> Source:
    url = _norm_source(provider, url)
    src = s.exec(select(Source).where(Source.profile_id == pid, Source.provider == provider, Source.url == url)).first()
    if not src:
        src = Source(profile_id=pid, provider=provider, url=url, name=name or (url or PROVIDERS[provider].label))
        s.add(src); s.flush()
    return src


@router.get('/sources')
def sources(pid: int = Depends(profile_id)):
    with session() as s:
        return [source_dict(s, x) for x in s.exec(select(Source).where(Source.profile_id == pid).order_by(Source.provider, Source.name)).all()]


@router.post('/sources')
def create_source(body: SourceIn, pid: int = Depends(profile_id)):
    with session() as s:
        src = get_or_create_source(s, pid, body.provider or '', body.url or '', body.name or '')
        s.commit(); s.refresh(src)
        return source_dict(s, src)


@router.patch('/sources/{sid}')
def update_source(sid: int, body: SourceIn, pid: int = Depends(profile_id)):
    with session() as s:
        src = owned(s, Source, sid, pid)
        if body.name is not None: src.name = body.name
        if body.enabled is not None: src.enabled = body.enabled
        if body.url is not None and body.url != src.url:
            url = _norm_source(src.provider, body.url)
            if s.exec(select(Source).where(Source.profile_id == pid, Source.provider == src.provider, Source.url == url, Source.id != sid)).first():
                raise HTTPException(409, 'That source already exists.')
            src.url = url
        s.add(src); s.commit(); s.refresh(src)
        return source_dict(s, src)


@router.delete('/sources/{sid}')
def delete_source(sid: int, pid: int = Depends(profile_id)):
    with session() as s:
        src = owned(s, Source, sid, pid)
        s.exec(delete(CuratorSource).where(CuratorSource.source_id == sid))
        s.delete(src); s.commit()
        return {'ok': True}


@router.post('/sources/{sid}/test')
def test_source(sid: int, pid: int = Depends(profile_id)):
    with session() as s:
        src = owned(s, Source, sid, pid)
    p = PROVIDERS[src.provider]
    ok, reason = p.availability()
    if not ok:
        return {'ok': False, 'count': 0, 'sample': [], 'error': reason}
    try:
        got = p.fetch(src.url, 'open source' if p.mode == 'query' else '')
    except Exception as e:  # noqa: BLE001
        err = engine._safe_error(e)
        with session() as s:
            row = s.get(Source, sid); row.last_checked, row.last_error = time.time(), err; s.add(row); s.commit()
        return {'ok': False, 'count': 0, 'sample': [], 'error': err}
    with session() as s:
        row = s.get(Source, sid); row.last_checked = row.last_success = time.time(); row.last_error = ''; s.add(row); s.commit()
    return {'ok': True, 'count': len(got), 'sample': [{'title': d.get('title', ''), 'url': d.get('url', '')} for d in got[:5]], 'error': ''}


# ---------- Source Scout

class ScoutInput(BaseModel):
    query: str = Input(min_length=1, max_length=2000)


class ScoutAccept(BaseModel):
    index: int = Input(ge=0, le=14)
    curator_id: int | None = None


def _scout_events(s: Session, pid: int, rid: int):
    events = s.exec(select(AgentEvent).where(AgentEvent.kind == 'source_scout', AgentEvent.profile_id == pid,
                                             AgentEvent.payload['scout_run_id'].as_integer() == rid)
                    .order_by(AgentEvent.id)).all()
    from datetime import timezone
    return [{'kind': e.payload.get('step', ''), 'message': e.message,
             'at': e.ts.replace(tzinfo=timezone.utc).timestamp()} for e in events]


def _scout_dict(s: Session, row: ScoutRun):
    return {**row.model_dump(), 'events': _scout_events(s, row.profile_id, row.id)}


def _delete_scout_events(s: Session, pid: int, rid: int):
    s.exec(delete(AgentEvent).where(AgentEvent.kind == 'source_scout', AgentEvent.profile_id == pid,
                                    AgentEvent.payload['scout_run_id'].as_integer() == rid))


def _expire_scout_runs(s: Session):
    # A crashed process cannot leave research permanently running. Normal requests have a bounded fetch budget.
    stale = s.exec(select(ScoutRun).where(ScoutRun.status == 'running', ScoutRun.created_at < time.time() - 600)).all()
    for row in stale:
        row.status = 'failed'; row.error = 'Research was interrupted. Please try again.'; row.finished_at = time.time()
        s.add(row)


class ScoutPlanInput(BaseModel):
    query: str = Input(min_length=1, max_length=2000)
    interval_hours: int = Input(ge=24, le=168)


class ScoutPlanUpdate(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Input(default=None, ge=24, le=168)


@router.get('/scout/plans')
def scout_plans(pid: int = Depends(profile_id)):
    with session() as s:
        return [r.model_dump() for r in s.exec(select(ScoutPlan).where(ScoutPlan.profile_id == pid)
                                               .order_by(ScoutPlan.created_at.desc())).all()]


@router.post('/scout/plans')
def scout_plan_create(body: ScoutPlanInput, pid: int = Depends(profile_id)):
    query = body.query.strip()
    if not query: raise HTTPException(422, 'Enter a topic or HTTPS website URL.')
    if '://' in query and not query.lower().startswith('https://'):
        raise HTTPException(422, 'Use a public HTTPS website URL.')
    if query.lower().startswith('https://'):
        try: curation.canonical(query)
        except ValueError: raise HTTPException(422, 'Use a public HTTPS website URL.')
    elif not PROVIDERS['web_search'].availability()[0]:
        raise HTTPException(422, PROVIDERS['web_search'].availability()[1])
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.exec(select(ScoutPlan).where(ScoutPlan.profile_id == pid, ScoutPlan.query == query)).first()
        if not row:
            if len(s.exec(select(ScoutPlan.id).where(ScoutPlan.profile_id == pid)).all()) >= 20:
                raise HTTPException(422, 'Maximum 20 scheduled Scout searches per profile.')
            row = ScoutPlan(profile_id=pid, query=query)
        row.enabled = True; row.interval_hours = body.interval_hours
        row.next_run = time.time() + body.interval_hours * 3600
        s.add(row); s.commit(); s.refresh(row)
        return row.model_dump()


@router.patch('/scout/plans/{plan_id}')
def scout_plan_update(plan_id: int, body: ScoutPlanUpdate, pid: int = Depends(profile_id)):
    with session() as s:
        row = owned(s, ScoutPlan, plan_id, pid)
        if body.interval_hours is not None: row.interval_hours = body.interval_hours
        if body.enabled is not None: row.enabled = body.enabled
        if row.enabled: row.next_run = time.time() + row.interval_hours * 3600
        s.add(row); s.commit(); s.refresh(row)
        return row.model_dump()


@router.delete('/scout/plans/{plan_id}')
def scout_plan_delete(plan_id: int, pid: int = Depends(profile_id)):
    with session() as s:
        row = owned(s, ScoutPlan, plan_id, pid)
        s.delete(row); s.commit()
        return {'ok': True}


@router.get('/scout/runs')
def scout_runs(pid: int = Depends(profile_id)):
    with session() as s:
        _expire_scout_runs(s); s.commit()
        return [_scout_dict(s, r) for r in s.exec(select(ScoutRun).where(ScoutRun.profile_id == pid)
                                               .order_by(ScoutRun.created_at.desc()).limit(50)).all()]


@router.post('/scout/runs')
def scout_run(body: ScoutInput, pid: int = Depends(profile_id)):
    query = body.query.strip()
    if not query: raise HTTPException(422, 'Enter a topic or HTTPS website URL.')
    if '://' in query and not query.lower().startswith('https://'):
        raise HTTPException(422, 'Use a public HTTPS website URL.')
    if not query.lower().startswith('https://'):
        ok, reason = PROVIDERS['web_search'].availability()
        if not ok: raise HTTPException(422, reason)
    with session() as s:
        # Serialize check-and-create across API workers and bound the number of network jobs.
        s.exec(text('BEGIN IMMEDIATE'))
        _expire_scout_runs(s)
        active = s.exec(select(ScoutRun).where(ScoutRun.status == 'running')).all()
        if any(r.profile_id == pid for r in active):
            raise HTTPException(409, 'A research run is already in progress for this profile.')
        if len(active) >= 4:
            raise HTTPException(429, 'Source Scout is busy. Try again shortly.')
        old = s.exec(select(ScoutRun).where(ScoutRun.profile_id == pid, ScoutRun.status != 'running')
                     .order_by(ScoutRun.created_at.desc()).offset(49)).all()
        for row in old:
            _delete_scout_events(s, pid, row.id); s.delete(row)
        run = ScoutRun(profile_id=pid, query=query)
        s.add(run); s.commit(); s.refresh(run)
        rid = run.id
    def emit(kind, message):
        # Short-lived transactions let other requests see real progress during network calls.
        with session() as s:
            row = s.get(ScoutRun, rid)
            if not row or row.status != 'running': raise ValueError('Research is no longer active.')
            if not hub_allowed(s, pid): raise ValueError('Profile access was restricted.')
            s.add(AgentEvent(kind='source_scout', profile_id=pid, message=message,
                             payload={'scout_run_id': rid, 'step': kind}))
            s.commit()
    try:
        suggestions, sitemaps = scout.research(query, emit)
        if sitemaps: emit('sitemaps', f'Found {len(sitemaps)} sitemap links; sitemap monitoring is not available yet.')
        emit('complete', f'Found {len(suggestions)} validated sources.')
        with session() as s:
            row = s.get(ScoutRun, rid)
            if not row: raise HTTPException(404, 'Research run not found')
            if row.status != 'running': raise HTTPException(409, 'Research was interrupted.')
            row.suggestions = suggestions; row.sitemaps = sitemaps; row.status = 'complete'; row.finished_at = time.time()
            s.add(row); s.commit(); s.refresh(row)
            return _scout_dict(s, row)
    except HTTPException:
        raise
    except Exception:
        with session() as s:
            row = s.get(ScoutRun, rid)
            if row:
                row.status = 'failed'; row.error = 'Research could not be completed.'; row.finished_at = time.time()
                s.add(row); s.commit(); s.refresh(row)
                return _scout_dict(s, row)
        raise HTTPException(404, 'Research run not found')


@router.post('/scout/runs/{rid}/accept')
def scout_accept(rid: int, body: ScoutAccept, pid: int = Depends(profile_id)):
    with session() as s:
        run = owned(s, ScoutRun, rid, pid)
        if run.status != 'complete' or body.index >= len(run.suggestions):
            raise HTTPException(422, 'No validated suggestion at that position.')
        suggestion = run.suggestions[body.index]
        if suggestion.get('provider') not in ('rss', 'html_listing') or suggestion.get('validated') is not True:
            raise HTTPException(422, 'This suggestion is not a validated feed.')
        if body.curator_id is not None: owned(s, Curator, body.curator_id, pid)
        url = suggestion['url']
        provider = suggestion['provider']
    try:
        items = PROVIDERS[provider].fetch(url, '')
    except Exception:
        raise HTTPException(422, 'This source could not be validated again. Research the site again.')
    with session() as s:
        if not hub_allowed(s, pid):
            raise HTTPException(403, 'Hub discovery and research are unavailable for this profile.')
        owned(s, ScoutRun, rid, pid)
        curator = owned(s, Curator, body.curator_id, pid) if body.curator_id is not None else None
        src = get_or_create_source(s, pid, provider, url, suggestion.get('name', ''))
        src.last_checked = src.last_success = time.time(); src.last_error = ''; src.item_count = len(items)
        if curator and not s.get(CuratorSource, (curator.id, src.id)):
            s.add(CuratorSource(curator_id=curator.id, source_id=src.id))
        s.add(src); s.commit(); s.refresh(src)
        return source_dict(s, src)


@router.delete('/scout/runs/{rid}')
def scout_delete(rid: int, pid: int = Depends(profile_id)):
    with session() as s:
        row = owned(s, ScoutRun, rid, pid)
        if row.status == 'running': raise HTTPException(409, 'Wait until research finishes.')
        _delete_scout_events(s, pid, rid)
        s.delete(row); s.commit()
        return {'ok': True}


# ---------- curators

class CuratorIn(BaseModel):
    name: str | None = Input(default=None, min_length=1, max_length=100)
    description: str | None = Input(default=None, max_length=4000)
    icon: str | None = Input(default=None, max_length=8)
    color: str | None = Input(default=None, max_length=20)
    kind: str | None = Input(default=None, pattern='^(watch|research)$')
    instructions: str | None = Input(default=None, max_length=4000)
    queries: list[str] | None = None
    keywords: list[str] | None = None
    exclusions: list[str] | None = None
    content_types: list[str] | None = None
    source_ids: list[int] | None = None
    interval_hours: int | None = Input(default=None, ge=0, le=720)
    enabled: bool | None = None
    ai_enabled: bool | None = None
    min_score: int | None = Input(default=None, ge=0, le=100)
    max_per_run: int | None = Input(default=None, ge=1, le=200)
    collection_id: int | None = None
    topic_id: int | None = None
    alert_enabled: bool | None = None
    alert_keywords: list[str] | None = None
    alert_min_severity: str | None = Input(default=None, pattern='^(info|low|medium|high|critical)$')
    subject: str | None = Input(default=None, max_length=300)
    subject_type: str | None = Input(default=None, pattern='^(|topic|organization|person|domain|product)$')
    aliases: list[str] | None = None


def curator_dict(s, c: Curator) -> dict:
    n = s.exec(select(func.count()).select_from(CuratorDiscovery).where(CuratorDiscovery.curator_id == c.id)).one()
    sids = list(s.exec(select(CuratorSource.source_id).where(CuratorSource.curator_id == c.id)).all())
    return {**c.model_dump(exclude={'provider', 'source'}), 'source_ids': sids, 'discovery_count': n,
            'next_run': c.next_run if c.next_run < 1e11 else None}


def apply(s, c: Curator, body: CuratorIn, pid: int) -> None:
    data = body.model_dump(exclude_unset=True)
    sids = data.pop('source_ids', None)
    for k in ('queries', 'keywords', 'exclusions', 'alert_keywords', 'aliases'):
        if k in data:
            data[k] = _clip_list(data[k])
    if 'content_types' in data:
        data['content_types'] = [t for t in data['content_types'] or [] if t in TYPE_KEYS]
    if data.get('collection_id'):
        owned(s, Collection, data['collection_id'], pid)
    if data.get('topic_id'):
        owned(s, Topic, data['topic_id'], pid)
    for k, v in data.items():
        setattr(c, k, v)
    if 'interval_hours' in data and c.enabled and c.next_run < time.time() + 900 and c.last_status == 'running':
        pass  # running: the run sets next_run when it finishes
    elif 'interval_hours' in data:
        c.next_run = engine.schedule_after(c.interval_hours) if c.last_run else (0 if c.interval_hours else 1e12)
    s.add(c); s.flush()
    if sids is not None:
        for sid in sids:
            owned(s, Source, sid, pid)
        s.exec(delete(CuratorSource).where(CuratorSource.curator_id == c.id))
        for sid in dict.fromkeys(sids):
            s.add(CuratorSource(curator_id=c.id, source_id=sid))


@router.get('/curators')
def curators(kind: str | None = None, pid: int = Depends(profile_id)):
    with session() as s:
        q = select(Curator).where(Curator.profile_id == pid)
        if kind:
            q = q.where(Curator.kind == kind)
        return [curator_dict(s, c) for c in s.exec(q.order_by(Curator.created_at.desc())).all()]


@router.get('/curators/templates')
def templates():
    return TEMPLATES


class DraftIn(BaseModel):
    goal: str = Input(min_length=2, max_length=2000)
    kind: str = Input(default='watch', pattern='^(watch|research)$')


@router.post('/curators/draft')
def draft(body: DraftIn, pid: int = Depends(profile_id)):
    """Propose a curator from natural language. Nothing is stored until the user approves."""
    d = ai.draft(body.goal, body.kind)
    with session() as s:
        existing = {x.provider: x.id for x in s.exec(select(Source).where(Source.profile_id == pid, Source.url == '')).all()}
    return {'name': d['name'], 'description': d['description'], 'icon': d['icon'], 'kind': body.kind, 'instructions': body.goal,
            'queries': d['queries'], 'keywords': d['keywords'], 'exclusions': d['exclusions'], 'content_types': d['content_types'],
            'interval_hours': d['interval_hours'], 'ai_enabled': ai.enabled() and body.kind == 'watch', 'ai': d['ai'],
            'subject': body.goal if body.kind == 'research' else '', 'provider_keys': [p for p in d['providers'] if p in PROVIDERS],
            'source_ids': [existing[p] for p in d['providers'] if p in existing],
            'source_suggestions': [{'provider': p, 'url': '', 'name': PROVIDERS[p].label, 'available': PROVIDERS[p].availability()[0]}
                                   for p in d['providers'] if p in PROVIDERS]
                                  + [{'provider': 'rss', 'url': f['url'], 'name': str(f.get('name') or f['url'])[:100], 'available': True} for f in d['feeds']]}


@router.post('/curators')
def create_curator(body: CuratorIn, pid: int = Depends(profile_id)):
    if not body.name:
        raise HTTPException(422, 'Name required')
    with session() as s:
        if s.exec(select(func.count()).select_from(Curator).where(Curator.profile_id == pid)).one() >= MAX_CURATORS:
            raise HTTPException(422, f'Maximum {MAX_CURATORS} curators per profile.')
        c = Curator(profile_id=pid, name=body.name, next_run=0)
        apply(s, c, body, pid)
        if c.interval_hours == 0 and c.kind == 'watch' and body.interval_hours is not None:
            c.next_run = 1e12
        s.commit(); s.refresh(c)
        return curator_dict(s, c)


@router.get('/curators/{cid}')
def curator(cid: int, pid: int = Depends(profile_id)):
    with session() as s:
        c = owned(s, Curator, cid, pid)
        out = curator_dict(s, c)
        out['runs'] = [r.model_dump() for r in s.exec(select(CuratorRun).where(CuratorRun.curator_id == cid).order_by(CuratorRun.started_at.desc()).limit(30)).all()]
        recent = s.exec(select(ContentItem).join(CuratorDiscovery, CuratorDiscovery.item_id == ContentItem.id)
                        .where(CuratorDiscovery.curator_id == cid).order_by(CuratorDiscovery.found_at.desc()).limit(60)).all()
        out['recent'] = serialize(s, pid, list(recent))
        return out


@router.patch('/curators/{cid}')
def update_curator(cid: int, body: CuratorIn, pid: int = Depends(profile_id)):
    with session() as s:
        c = owned(s, Curator, cid, pid)
        apply(s, c, body, pid)
        s.commit(); s.refresh(c)
        return curator_dict(s, c)


@router.delete('/curators/{cid}')
def delete_curator(cid: int, pid: int = Depends(profile_id)):
    """Removes the curator, its runs and provenance links. Saved items stay in the Library."""
    with session() as s:
        c = owned(s, Curator, cid, pid)
        s.exec(delete(CuratorSource).where(CuratorSource.curator_id == cid))
        s.exec(delete(CuratorRun).where(CuratorRun.curator_id == cid))
        s.exec(delete(CuratorDiscovery).where(CuratorDiscovery.curator_id == cid))
        for a in s.exec(select(Alert).where(Alert.curator_id == cid)).all():
            a.curator_id = None; s.add(a)
        s.delete(c); s.commit()
        return {'ok': True}


@router.post('/curators/{cid}/{action}')
def curator_action(cid: int, action: str, pid: int = Depends(profile_id)):
    with session() as s:
        c = owned(s, Curator, cid, pid)
        if action == 'run':
            if c.last_status in ('running', 'queued') and c.next_run > time.time():
                raise HTTPException(409, 'Already running')
            c.next_run, c.last_status = 0, 'queued'
            s.add(c); s.commit(); s.refresh(c)
            engine.start(cid)
            return curator_dict(s, c)
        elif action == 'pause':
            c.enabled = False
        elif action == 'resume':
            c.enabled = True
            if c.next_run >= 1e11 and c.interval_hours:
                c.next_run = 0
        elif action == 'duplicate':
            if s.exec(select(func.count()).select_from(Curator).where(Curator.profile_id == pid)).one() >= MAX_CURATORS:
                raise HTTPException(422, f'Maximum {MAX_CURATORS} curators per profile.')
            data = c.model_dump(exclude={'id', 'report', 'report_at', 'report_refs', 'last_run', 'last_success', 'last_status', 'error', 'created_at'})
            d = Curator(**{**data, 'name': f'{c.name} (copy)'[:100], 'enabled': False, 'next_run': 1e12})
            s.add(d); s.flush()
            for sid in s.exec(select(CuratorSource.source_id).where(CuratorSource.curator_id == cid)).all():
                s.add(CuratorSource(curator_id=d.id, source_id=sid))
            s.commit(); s.refresh(d)
            return curator_dict(s, d)
        elif action == 'report':
            pass
        else:
            raise HTTPException(404, 'Unknown action')
        s.add(c); s.commit(); s.refresh(c)
        if action != 'report':
            return curator_dict(s, c)
    ai.research_report(cid)
    with session() as s:
        return curator_dict(s, s.get(Curator, cid))


@router.get('/curators/{cid}/export')
def export_curator(cid: int, pid: int = Depends(profile_id)):
    with session() as s:
        c = owned(s, Curator, cid, pid)
        srcs = s.exec(select(Source).join(CuratorSource, CuratorSource.source_id == Source.id).where(CuratorSource.curator_id == cid)).all()
        data = {'watchcraft_curator': 1, **c.model_dump(include={'name', 'description', 'icon', 'color', 'kind', 'instructions', 'queries',
                'keywords', 'exclusions', 'content_types', 'interval_hours', 'ai_enabled', 'min_score', 'max_per_run', 'alert_enabled',
                'alert_keywords', 'alert_min_severity', 'subject', 'subject_type', 'aliases'}),
                'sources': [{'provider': x.provider, 'url': x.url, 'name': x.name} for x in srcs]}
    name = re.sub(r'[^a-z0-9]+', '-', c.name.lower()).strip('-') or 'curator'
    return Response(json.dumps(data, indent=2), media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="{name}.watchcraft.json"'})


# ---------- topics (knowledge + followed news topics)

class TopicIn(BaseModel):
    name: str | None = Input(default=None, min_length=1, max_length=100)
    description: str | None = Input(default=None, max_length=10000)
    keywords: list[str] | None = None
    notes: str | None = Input(default=None, max_length=50000)
    kind: str | None = Input(default=None, pattern='^(knowledge|news)$')
    muted: bool | None = None
    pinned: bool | None = None


def topic_dict(s, t: Topic) -> dict:
    n = s.exec(select(func.count()).select_from(TopicItem).where(TopicItem.topic_id == t.id)).one()
    return {**t.model_dump(), 'count': n}


@router.get('/topics')
def topics(kind: str | None = None, pid: int = Depends(profile_id)):
    with session() as s:
        q = select(Topic).where(Topic.profile_id == pid)
        if kind:
            q = q.where(Topic.kind == kind)
        return [topic_dict(s, t) for t in s.exec(q.order_by(Topic.name)).all()]


@router.post('/topics')
def create_topic(body: TopicIn, pid: int = Depends(profile_id)):
    if not body.name:
        raise HTTPException(422, 'Name required')
    with session() as s:
        data = body.model_dump(exclude_none=True)
        data['keywords'] = _clip_list(data.get('keywords')) or [body.name.lower()]
        t = Topic(profile_id=pid, **data)
        s.add(t); s.commit(); s.refresh(t)
        return topic_dict(s, t)


@router.get('/topics/{tid}')
def topic(tid: int, pid: int = Depends(profile_id)):
    with session() as s:
        t = owned(s, Topic, tid, pid)
        out = topic_dict(s, t)
        linked = s.exec(select(ContentItem).join(TopicItem, TopicItem.item_id == ContentItem.id).where(TopicItem.topic_id == tid)
                        .order_by(TopicItem.added_at.desc()).limit(100)).all()
        out['items'] = serialize(s, pid, list(linked))
        expr = ' OR '.join('"' + k.replace('"', '') + '"' for k in (t.keywords or [t.name]) if k.strip())
        recent = []
        if expr:
            recent = s.exec(item_query(pid, section='discover').where(
                ContentItem.id.in_(text('SELECT rowid FROM content_fts WHERE content_fts MATCH :q').bindparams(q=expr)),
                ContentItem.id.not_in([i.id for i in linked] or [0])).order_by(ContentItem.first_seen.desc()).limit(20)).all()
        out['recent'] = serialize(s, pid, list(recent))
        out['curators'] = [{'id': c.id, 'name': c.name} for c in s.exec(select(Curator).where(Curator.profile_id == pid)).all()
                           if c.topic_id == tid or any(k.lower() in f'{c.name} {c.instructions} {" ".join(c.keywords)}'.lower() for k in (t.keywords or [t.name]))]
        others = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.id != tid, Topic.kind == t.kind)).all()
        mine = {i.id for i in linked}
        out['related'] = [topic_dict(s, o) for o in others
                          if mine & set(s.exec(select(TopicItem.item_id).where(TopicItem.topic_id == o.id)).all())
                          or set(map(str.lower, o.keywords)) & set(map(str.lower, t.keywords))][:8]
        return out


@router.patch('/topics/{tid}')
def update_topic(tid: int, body: TopicIn, pid: int = Depends(profile_id)):
    with session() as s:
        t = owned(s, Topic, tid, pid)
        data = body.model_dump(exclude_none=True)
        if 'keywords' in data:
            data['keywords'] = _clip_list(data['keywords'])
        for k, v in data.items():
            setattr(t, k, v)
        s.add(t); s.commit(); s.refresh(t)
        return topic_dict(s, t)


@router.delete('/topics/{tid}')
def delete_topic(tid: int, pid: int = Depends(profile_id)):
    with session() as s:
        t = owned(s, Topic, tid, pid)
        s.exec(delete(TopicItem).where(TopicItem.topic_id == tid))
        for c in s.exec(select(Curator).where(Curator.topic_id == tid)).all():
            c.topic_id = None; s.add(c)
        s.delete(t); s.commit()
        return {'ok': True}


@router.post('/topics/{tid}/items/{iid}')
def link_topic(tid: int, iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        owned(s, Topic, tid, pid)
        if not can_see(s, pid, iid):
            raise HTTPException(404, 'Item not found')
        if not s.get(TopicItem, (tid, iid)):
            s.add(TopicItem(topic_id=tid, item_id=iid)); s.commit()
        return {'ok': True}


@router.delete('/topics/{tid}/items/{iid}')
def unlink_topic(tid: int, iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        owned(s, Topic, tid, pid)
        s.exec(delete(TopicItem).where(TopicItem.topic_id == tid, TopicItem.item_id == iid)); s.commit()
        return {'ok': True}


# ---------- briefings

class BriefingIn(BaseModel):
    name: str | None = Input(default=None, min_length=1, max_length=100)
    curator_ids: list[int] | None = None
    topic_ids: list[int] | None = None
    frequency: str | None = Input(default=None, pattern='^(manual|daily|weekly)$')
    length: str | None = Input(default=None, pattern='^(concise|detailed)$')


def _apply_briefing(s, b: Briefing, body: BriefingIn, pid: int) -> None:
    data = body.model_dump(exclude_none=True)
    for cid in data.get('curator_ids', []):
        owned(s, Curator, cid, pid)
    for tid in data.get('topic_ids', []):
        owned(s, Topic, tid, pid)
    for k, v in data.items():
        setattr(b, k, v)
    if 'frequency' in data:
        b.next_run = None if b.frequency == 'manual' else time.time() + (86400 if b.frequency == 'daily' else 7 * 86400)


@router.get('/briefings')
def briefings(pid: int = Depends(profile_id)):
    with session() as s:
        return s.exec(select(Briefing).where(Briefing.profile_id == pid).order_by(Briefing.created_at.desc())).all()


@router.post('/briefings')
def create_briefing(body: BriefingIn, pid: int = Depends(profile_id)):
    if not body.name:
        raise HTTPException(422, 'Name required')
    with session() as s:
        b = Briefing(profile_id=pid, name=body.name)
        _apply_briefing(s, b, body, pid)
        s.add(b); s.commit(); s.refresh(b)
        return b


@router.patch('/briefings/{bid}')
def update_briefing(bid: int, body: BriefingIn, pid: int = Depends(profile_id)):
    with session() as s:
        b = owned(s, Briefing, bid, pid)
        _apply_briefing(s, b, body, pid)
        s.add(b); s.commit(); s.refresh(b)
        return b


@router.delete('/briefings/{bid}')
def delete_briefing(bid: int, pid: int = Depends(profile_id)):
    with session() as s:
        b = owned(s, Briefing, bid, pid)
        s.exec(delete(BriefingIssue).where(BriefingIssue.briefing_id == bid))
        s.delete(b); s.commit()
        return {'ok': True}


@router.post('/briefings/{bid}/generate')
def generate_briefing(bid: int, pid: int = Depends(profile_id)):
    with session() as s:
        owned(s, Briefing, bid, pid)
    return ai.generate_briefing(bid)


@router.get('/briefings/{bid}/issues')
def issues(bid: int, pid: int = Depends(profile_id)):
    with session() as s:
        owned(s, Briefing, bid, pid)
        return s.exec(select(BriefingIssue).where(BriefingIssue.briefing_id == bid).order_by(BriefingIssue.created_at.desc()).limit(50)).all()


# ---------- alerts

@router.get('/alerts')
def alerts(state: str = 'new', pid: int = Depends(profile_id)):
    with session() as s:
        q = select(Alert).where(Alert.profile_id == pid)
        if state == 'new':
            q = q.where(Alert.state == 'new', or_(Alert.snooze_until == None, Alert.snooze_until < time.time()))  # noqa: E711
        elif state == 'snoozed':
            q = q.where(Alert.state == 'new', Alert.snooze_until >= time.time())
        elif state != 'all':
            q = q.where(Alert.state == state)
        rows = s.exec(q.order_by(Alert.created_at.desc()).limit(200)).all()
        its = {i['id']: i for i in serialize(s, pid, list(s.exec(select(ContentItem).where(ContentItem.id.in_([a.item_id for a in rows]))).all()))}
        return {'items': [{**a.model_dump(), 'item': its.get(a.item_id)} for a in rows if a.item_id in its], 'total': len(rows)}


class AlertStateIn(BaseModel):
    state: str = Input(pattern='^(new|read|resolved)$')
    snooze_hours: int | None = Input(default=None, ge=1, le=24 * 30)


@router.post('/alerts/{aid}/state')
def alert_state(aid: int, body: AlertStateIn, pid: int = Depends(profile_id)):
    with session() as s:
        a = owned(s, Alert, aid, pid)
        a.state = body.state
        a.snooze_until = time.time() + body.snooze_hours * 3600 if body.snooze_hours else None
        s.add(a); s.commit()
        return {'ok': True}


# ---------- search

@router.get('/search')
def search(q: str = '', type: str = '', provider: str = '', scope: str = 'all', offset: int = 0, limit: int = 30,
           pid: int = Depends(profile_id)):
    """Local search across indexed content, videos, curators, collections and topics. No AI per keystroke."""
    q = q.strip()[:300]
    if not q:
        return {'items': [], 'total': 0, 'videos': [], 'curators': [], 'collections': [], 'topics': []}
    like = f'%{q}%'
    with session() as s:
        if scope == 'library':
            stmt = item_query(pid, section='library', q=q, type=type, provider=provider)
        elif scope == 'knowledge':
            stmt = item_query(pid, section='library', q=q, type=type, provider=provider).where(
                ContentItem.id.in_(select(TopicItem.item_id).join(Topic, Topic.id == TopicItem.topic_id).where(Topic.profile_id == pid)))
        else:
            stmt = item_query(pid, section='all', q=q, type=type, provider=provider)
        expr = fts_query(q)
        if expr:
            rank = text('(SELECT rank FROM content_fts WHERE content_fts MATCH :rq AND rowid = contentitem.id)').bindparams(rq=expr)
            ordered = stmt.order_by(rank)
        else:
            ordered = stmt.order_by(ContentItem.first_seen.desc())
        total = s.exec(select(func.count()).select_from(stmt.subquery())).one()
        rows = list(s.exec(ordered.offset(max(0, offset)).limit(max(1, min(limit, 100)))).all())
        # Notes are private per profile and not in the shared FTS index.
        noted = s.exec(select(ContentItem).join(ItemState, ItemState.item_id == ContentItem.id).where(
            ItemState.profile_id == pid, ItemState.notes.like(like)).limit(10)).all() if offset == 0 else []
        rows += [n for n in noted if n.id not in {r.id for r in rows}]
        videos = []
        if scope == 'all' and type in ('', 'video'):
            from ..models import Candidate, Category, ProfileChannel, Video, WatchHistory
            mine = select(Candidate.video_id).join(Category, Category.id == Candidate.category_id).where(Category.profile_id == pid).union(
                select(WatchHistory.video_id).where(WatchHistory.profile_id == pid))
            blocked = select(ProfileChannel.channel_id).where(ProfileChannel.profile_id == pid, ProfileChannel.blocked == True)  # noqa: E712
            videos = [{'id': v.id, 'title': v.title, 'channel_name': v.channel_name, 'thumbnail': f'/api/thumb/{v.id}'}
                      for v in s.exec(select(Video).where(Video.id.in_(mine), Video.channel_id.not_in(blocked),
                                                          or_(Video.title.like(like), Video.channel_name.like(like))).limit(12)).all()]
        return {'items': serialize(s, pid, rows), 'total': total, 'videos': videos,
                'curators': [{'id': c.id, 'name': c.name, 'kind': c.kind} for c in s.exec(select(Curator).where(
                    Curator.profile_id == pid, or_(Curator.name.like(like), Curator.instructions.like(like), Curator.subject.like(like)))).all()][:10],
                'collections': [{'id': c.id, 'name': c.name} for c in s.exec(select(Collection).where(Collection.profile_id == pid, Collection.name.like(like))).all()][:10],
                'topics': [{'id': t.id, 'name': t.name, 'kind': t.kind} for t in s.exec(select(Topic).where(
                    Topic.profile_id == pid, or_(Topic.name.like(like), Topic.notes.like(like), Topic.description.like(like)))).all()][:10]}


class WebIn(BaseModel):
    q: str = Input(min_length=1, max_length=300)
    providers: list[str] = Input(default_factory=lambda: ['web_search'])


@router.post('/search/web')
def web_search(body: WebIn, pid: int = Depends(profile_id)):  # noqa: ARG001 — profile check gates access
    """Live external search. Results are NOT stored until the user saves one."""
    results, errors = [], []
    for key in body.providers[:6]:
        p = PROVIDERS.get(key)
        if not p or p.mode != 'query':
            errors.append({'provider': key, 'message': 'Not a searchable provider.'}); continue
        ok, reason = p.availability()
        if not ok:
            errors.append({'provider': key, 'message': reason}); continue
        try:
            for d in p.fetch('', body.q)[:15]:
                results.append({'title': d.get('title', ''), 'url': d.get('url', ''), 'snippet': (d.get('summary') or '')[:400],
                                'provider': key, 'type': d.get('type') or p.default_type})
        except Exception as e:  # noqa: BLE001
            errors.append({'provider': key, 'message': engine._safe_error(e)})
    seen, unique = set(), []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url']); unique.append(r)
    return {'results': unique, 'errors': errors}


class SavedSearchIn(BaseModel):
    name: str = Input(min_length=1, max_length=100)
    query: str = Input(default='', max_length=300)
    filters: dict = Input(default_factory=dict)
    pinned: bool = False


@router.get('/saved-searches')
def saved_searches(pid: int = Depends(profile_id)):
    with session() as s:
        return s.exec(select(SavedSearch).where(SavedSearch.profile_id == pid).order_by(SavedSearch.created_at.desc())).all()


@router.post('/saved-searches')
def create_saved_search(body: SavedSearchIn, pid: int = Depends(profile_id)):
    with session() as s:
        filters = {str(k)[:40]: str(v)[:200] for k, v in list(body.filters.items())[:20]}
        x = SavedSearch(profile_id=pid, name=body.name, query=body.query, filters=filters, pinned=body.pinned)
        s.add(x); s.commit(); s.refresh(x)
        return x


@router.delete('/saved-searches/{sid}')
def delete_saved_search(sid: int, pid: int = Depends(profile_id)):
    with session() as s:
        s.delete(owned(s, SavedSearch, sid, pid)); s.commit()
        return {'ok': True}


def delete_profile_data(s: Session, pid: int) -> None:
    """Called when a profile is deleted. Shared ContentItems stay (other profiles may reference them)."""
    cids = list(s.exec(select(Curator.id).where(Curator.profile_id == pid)).all())
    bids = list(s.exec(select(Briefing.id).where(Briefing.profile_id == pid)).all())
    colls = list(s.exec(select(Collection.id).where(Collection.profile_id == pid)).all())
    tids = list(s.exec(select(Topic.id).where(Topic.profile_id == pid)).all())
    s.exec(delete(CuratorSource).where(CuratorSource.curator_id.in_(cids or [0])))
    s.exec(delete(CuratorRun).where(CuratorRun.curator_id.in_(cids or [0])))
    s.exec(delete(BriefingIssue).where(BriefingIssue.briefing_id.in_(bids or [0])))
    s.exec(delete(CollectionItem).where(CollectionItem.collection_id.in_(colls or [0])))
    s.exec(delete(TopicItem).where(TopicItem.topic_id.in_(tids or [0])))
    s.exec(delete(AgentEvent).where(AgentEvent.kind == 'source_scout', AgentEvent.profile_id == pid))
    for model in (CuratorDiscovery, ItemState, Alert, SavedSearch, Briefing, Collection, Topic, Source, Curator, ScoutRun, ScoutPlan):
        s.exec(delete(model).where(model.profile_id == pid))
