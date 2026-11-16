"""Common Room HTTP API under /api/hub/commonroom. Profile-scoped; child profiles see kid-checked posts only."""
import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, case, delete, func, update
from sqlmodel import select

from .. import curation
from ..access import hub_worker_allowed, is_child, profile_id
from ..db import session
from . import harvest, store
from .adapters import ADAPTERS
from .models import (CRAsset, CRBlock, CRCandidate, CRComments, CRFeedback, CRPick, CRPost, CRSource, CRThread,
                     CRThreadSource, CRVisit)

router = APIRouter(prefix='/hub/commonroom', tags=['Common Room'])
PAGE = 30
MAX_THREADS = 50
MAX_SOURCES = 60
COMMENTS_TTL = 15 * 60
FIRST_VISIT_S = 24 * 3600
REASONS = {'off-topic', 'low effort', 'seen it', 'too basic', 'more like this', 'too advanced', 'clickbait', 'repost'}


def require_room(pid: int = Depends(profile_id)) -> int:
    with session() as s:
        if not hub_worker_allowed(s, pid):
            raise HTTPException(403, 'Common Room is unavailable for this profile.')
    return pid


def _owned(s, pid: int, tid: int) -> CRThread:
    t = s.get(CRThread, tid)
    if not t or t.profile_id != pid:
        raise HTTPException(404, 'Thread not found')
    return t


def _child(s, pid: int) -> bool:
    return is_child(s, pid)


def _visible_picks(s, pid: int):
    """Base query of this profile's picks, kid-gated for child profiles."""
    q = select(CRPick, CRPost, CRThread).join(CRPost, CRPost.id == CRPick.post_id) \
        .join(CRThread, CRThread.id == CRPick.thread_id).where(CRPick.profile_id == pid)
    if _child(s, pid):
        q = q.where(CRPost.kid_ok == 1, CRPost.nsfw == False)  # noqa: E712
    return q


def _thread_stats(s, pid: int) -> dict[int, tuple[int, int, int]]:
    """{thread_id: (picks, new picks, sources)} in three grouped queries."""
    base = _visible_picks(s, pid).where(CRPick.hidden == False).subquery()  # noqa: E712
    picks = dict(s.exec(select(base.c.thread_id, func.count()).group_by(base.c.thread_id)).all())
    visit = select(CRVisit.visit_at).where(CRVisit.profile_id == pid,
                                           CRVisit.scope == func.cast(base.c.thread_id, String)).scalar_subquery()
    new = dict(s.exec(select(base.c.thread_id, func.count()).where(base.c.picked_at > func.coalesce(visit, 0))
                      .group_by(base.c.thread_id)).all())
    sources = dict(s.exec(select(CRThreadSource.thread_id, func.count()).join(CRThread, CRThread.id == CRThreadSource.thread_id)
                          .where(CRThread.profile_id == pid).group_by(CRThreadSource.thread_id)).all())
    return {tid: (picks.get(tid, 0), new.get(tid, 0), sources.get(tid, 0)) for tid in set(picks) | set(sources)}


def _thread_json(s, t: CRThread, pid: int, stats: dict | None = None) -> dict:
    picks, new, sources = (stats if stats is not None else _thread_stats(s, pid)).get(t.id, (0, 0, 0))
    return {'id': t.id, 'name': t.name, 'interests': t.interests, 'instructions': t.instructions, 'avoid': t.avoid,
            'strictness': t.strictness, 'child_friendly': t.child_friendly, 'enabled': t.enabled,
            'last_run': t.last_run, 'last_status': 'collecting' if t.lease else t.last_status, 'error': t.error,
            'new_count': new, 'pick_count': picks, 'source_count': sources}


def _media(post: CRPost, child: bool) -> list[dict]:
    if child:
        return [{'kind': 'image', 'url': f'/api/hub/commonroom/posts/{post.id}/media/0', 'poster': ''}] \
            if post.kid_image_ok == 1 else []
    out = []
    for n, m in enumerate(post.media or []):
        if m.get('kind') == 'video':
            out.append({'kind': 'video', 'url': m.get('url') or '',
                        'poster': f'/api/hub/commonroom/posts/{post.id}/media/{n}' if m.get('poster') else ''})
        else:
            out.append({'kind': 'image', 'url': f'/api/hub/commonroom/posts/{post.id}/media/{n}', 'poster': ''})
    return out


def _post_json(pick: CRPick, post: CRPost, thread: CRThread, child: bool, verdict: str = '') -> dict:
    body = post.body or ''
    return {'pick_id': pick.id, 'post_id': post.id, 'thread_id': thread.id, 'thread_name': thread.name,
            'platform': post.platform, 'community': post.community, 'author': post.author, 'title': post.title,
            'excerpt': body[:600] + ('…' if len(body) > 600 else ''), 'url': post.url, 'link_url': post.link_url,
            'domain': post.domain, 'media': _media(post, child), 'score': post.score, 'comments': post.comments,
            'published_at': post.published_at, 'why': pick.why, 'relevance': pick.score, 'saved': pick.saved,
            'verdict': verdict, 'nsfw': post.nsfw, 'spoiler': post.spoiler}


# ---------- threads

class ThreadIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    interests: str = Field(default='', max_length=2000)
    instructions: str = Field(default='', max_length=2000)
    avoid: str = Field(default='', max_length=1000)
    strictness: str = Field(default='balanced', pattern='^(strict|balanced|loose)$')


class ThreadEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    interests: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=2000)
    avoid: str | None = Field(default=None, max_length=1000)
    strictness: str | None = Field(default=None, pattern='^(strict|balanced|loose)$')
    enabled: bool | None = None
    child_friendly: bool | None = None


@router.get('/threads')
def list_threads(pid: int = Depends(require_room)):
    with session() as s:
        rows = s.exec(select(CRThread).where(CRThread.profile_id == pid).order_by(CRThread.position, CRThread.id)).all()
        stats = _thread_stats(s, pid)
        return {'threads': [_thread_json(s, t, pid, stats) for t in rows], 'child': _child(s, pid),
                'platforms': [{'key': a.key, 'label': a.label, 'example': a.example, 'sorts': list(a.sorts)}
                              for a in ADAPTERS.values()]}


@router.post('/threads')
def create_thread(data: ThreadIn, pid: int = Depends(require_room)):
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        if s.exec(select(func.count()).select_from(CRThread).where(CRThread.profile_id == pid)).one() >= MAX_THREADS:
            raise HTTPException(409, f'Up to {MAX_THREADS} threads per profile.')
        child = _child(s, pid)
        t = CRThread(profile_id=pid, name=data.name.strip(), interests=data.interests.strip() or data.name.strip(),
                     instructions=data.instructions.strip(), avoid=data.avoid.strip(),
                     strictness='strict' if child else data.strictness, child_friendly=child, want_more=True)
        s.add(t)
        s.commit()
        s.refresh(t)
        return _thread_json(s, t, pid)


@router.patch('/threads/{tid}')
def edit_thread(tid: int, data: ThreadEdit, pid: int = Depends(require_room)):
    with session() as s:
        t = _owned(s, pid, tid)
        child = _child(s, pid)
        changes = data.model_dump(exclude_none=True)
        if child:
            changes.pop('child_friendly', None)
            changes.pop('strictness', None)
        retune = any(k in changes and changes[k] != getattr(t, k) for k in ('interests', 'instructions', 'avoid', 'strictness'))
        for k, v in changes.items():
            setattr(t, k, v.strip() if isinstance(v, str) else v)
        if 'interests' in changes and changes['interests'] != '':
            t.discovered_at = 0  # rediscover sources for the new interests
        if retune:
            t.next_run = 0
        s.add(t)
        s.commit()
        s.refresh(t)
        return _thread_json(s, t, pid)


@router.delete('/threads/{tid}')
def delete_thread(tid: int, pid: int = Depends(require_room)):
    with session() as s:
        _owned(s, pid, tid)
        _delete_threads(s, [tid])
        s.commit()
    return {'ok': True}


@router.post('/threads/{tid}/run')
def run_thread(tid: int, pid: int = Depends(require_room)):
    with session() as s:
        t = _owned(s, pid, tid)
        t.next_run = 0
        t.enabled = True
        s.add(t)
        s.commit()
    return {'queued': True}


@router.get('/threads/{tid}/learned')
def learned(tid: int, pid: int = Depends(require_room)):
    from . import teach
    with session() as s:
        _owned(s, pid, tid)
        counts = dict(s.exec(select(CRFeedback.verdict, func.count()).where(CRFeedback.thread_id == tid)
                             .group_by(CRFeedback.verdict)).all())
        return {'lessons': teach.lessons(s, tid), 'up': counts.get('up', 0), 'down': counts.get('down', 0)}


# ---------- sources

class SourceIn(BaseModel):
    platform: str = Field(max_length=40)
    locator: str = Field(min_length=1, max_length=300)


class SourceEdit(BaseModel):
    enabled: bool


def _source_json(link: CRThreadSource, src: CRSource) -> dict:
    return {'id': link.id, 'source_id': src.id, 'platform': src.platform, 'locator': src.locator,
            'label': src.label or src.locator, 'origin': link.origin, 'enabled': link.enabled, 'error': src.error,
            'last_ok': src.last_ok or None}


@router.get('/threads/{tid}/sources')
def list_sources(tid: int, pid: int = Depends(require_room)):
    with session() as s:
        _owned(s, pid, tid)
        rows = s.exec(select(CRThreadSource, CRSource).join(CRSource, CRSource.id == CRThreadSource.source_id)
                      .where(CRThreadSource.thread_id == tid).order_by(CRSource.platform, CRSource.locator)).all()
        return {'sources': [_source_json(link, src) for link, src in rows]}


@router.post('/threads/{tid}/sources')
def add_source(tid: int, data: SourceIn, pid: int = Depends(require_room)):
    adapter = ADAPTERS.get(data.platform)
    if not adapter:
        raise HTTPException(400, 'Unknown platform.')
    with session() as s:
        _owned(s, pid, tid)
        if s.exec(select(func.count()).select_from(CRThreadSource).where(CRThreadSource.thread_id == tid)).one() >= MAX_SOURCES:
            raise HTTPException(409, f'Up to {MAX_SOURCES} sources per thread.')
    try:
        loc = adapter.parse(data.locator)
    except ValueError as e:
        raise HTTPException(400, str(e) or 'That does not look like a valid community.')
    sid = store.ensure_source(adapter.key, loc)
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        t = _owned(s, pid, tid)
        link = s.exec(select(CRThreadSource).where(CRThreadSource.thread_id == tid, CRThreadSource.source_id == sid)).first()
        if not link:
            link = CRThreadSource(thread_id=tid, source_id=sid, origin='user')
        link.enabled, link.origin = True, 'user'
        t.next_run = 0
        s.add(link)
        s.add(t)
        s.commit()
        s.refresh(link)
        return _source_json(link, s.get(CRSource, sid))


def _link(s, pid: int, tid: int, lid: int) -> CRThreadSource:
    _owned(s, pid, tid)
    link = s.get(CRThreadSource, lid)
    if not link or link.thread_id != tid:
        raise HTTPException(404, 'Source not found')
    return link


@router.patch('/threads/{tid}/sources/{lid}')
def edit_source(tid: int, lid: int, data: SourceEdit, pid: int = Depends(require_room)):
    with session() as s:
        link = _link(s, pid, tid, lid)
        link.enabled = data.enabled
        s.add(link)
        s.commit()
        s.refresh(link)
        return _source_json(link, s.get(CRSource, link.source_id))


@router.delete('/threads/{tid}/sources/{lid}')
def remove_source(tid: int, lid: int, pid: int = Depends(require_room)):
    with session() as s:
        s.delete(_link(s, pid, tid, lid))
        s.commit()
    return {'ok': True}


# ---------- feed

class VisitIn(BaseModel):
    thread: str = 'all'


def _scope(s, pid: int, thread: str) -> str:
    if thread == 'all':
        return 'all'
    try:
        tid = int(thread)
    except ValueError:
        raise HTTPException(400, 'thread must be "all" or a thread id')
    _owned(s, pid, tid)
    return str(tid)


@router.post('/visit')
def visit(data: VisitIn, pid: int = Depends(require_room)):
    """Start a visit: picks since the previous visit are New; the rest (and anything found later) is Deep."""
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        scope = _scope(s, pid, data.thread)
        v = s.exec(select(CRVisit).where(CRVisit.profile_id == pid, CRVisit.scope == scope)).first()
        if not v:
            v = CRVisit(profile_id=pid, scope=scope, boundary=now - FIRST_VISIT_S, visit_at=now)
        elif now - v.visit_at > 600:  # a quick reload keeps the same split
            v.boundary, v.visit_at = v.visit_at, now
        s.add(v)
        if scope == 'all':  # opening everything also catches up each thread's badge
            for t in s.exec(select(CRThread).where(CRThread.profile_id == pid)).all():
                tv = s.exec(select(CRVisit).where(CRVisit.profile_id == pid, CRVisit.scope == str(t.id))).first() \
                    or CRVisit(profile_id=pid, scope=str(t.id), boundary=v.boundary)
                tv.visit_at = now
                s.add(tv)
        s.commit()
        return {'boundary': v.boundary, 'visit_at': v.visit_at}


@router.get('/feed')
def feed(thread: str = 'all', section: str = 'new', boundary: float | None = None, cursor: str = '',
         pid: int = Depends(require_room)):
    if section not in ('new', 'deep'):
        raise HTTPException(400, 'section must be new or deep')
    try:
        offset = max(0, int(cursor or 0))
    except ValueError:
        raise HTTPException(400, 'bad cursor')
    with session() as s:
        scope = _scope(s, pid, thread)
        v = s.exec(select(CRVisit).where(CRVisit.profile_id == pid, CRVisit.scope == scope)).first()
        visit_at = v.visit_at if v else time.time()
        edge = boundary if boundary is not None else (v.boundary if v else visit_at - FIRST_VISIT_S)
        child = _child(s, pid)
        q = _visible_picks(s, pid).where((CRPick.hidden == False) | (CRPick.hidden_at > visit_at))  # noqa: E712
        if scope != 'all':
            q = q.where(CRPick.thread_id == int(scope))
        else:
            q = q.where(CRThread.enabled == True)  # noqa: E712
        if section == 'new':
            q = q.where(CRPick.picked_at > edge, CRPick.picked_at <= visit_at) \
                .order_by(CRPick.published_at.desc(), CRPick.id.desc())
        else:
            # Stable for the whole visit: the pool known at the visit start ranked first (unseen before seen),
            # then everything found while scrolling, appended in discovery order.
            age_days = (visit_at - CRPick.published_at) / 86400.0
            rank = CRPick.score - age_days * 2  # only fields fixed for the visit keep offsets stable
            later = CRPick.picked_at > visit_at
            seen = (CRPick.seen_at > 0) & (CRPick.seen_at < visit_at)
            q = q.where((CRPick.picked_at <= edge) | later) \
                .order_by(later, case((later, CRPick.picked_at), else_=0.0), seen, rank.desc(), CRPick.id.desc())
        rows = s.exec(q.offset(offset).limit(PAGE + 1)).all()
        more = len(rows) > PAGE
        rows = rows[:PAGE]
        verdicts = dict(s.exec(select(CRFeedback.post_id, CRFeedback.verdict).where(
            CRFeedback.profile_id == pid, CRFeedback.post_id.in_([p.id for _, p, _ in rows] or [0]))).all())
        posts = [_post_json(pick, post, t, child, verdicts.get(post.id, '')) for pick, post, t in rows
                 if not (pick.hidden and verdicts.get(post.id) != 'down')]
        building = False
        if section == 'deep' and not more:
            # Running dry: ask the curators for older posts; the page polls while they work. A thread that
            # has walked every source to the end rests for its normal interval instead of re-running.
            tids = [int(scope)] if scope != 'all' else None
            q2 = select(CRThread).where(CRThread.profile_id == pid, CRThread.enabled == True)  # noqa: E712
            if tids:
                q2 = q2.where(CRThread.id.in_(tids))
            now = time.time()
            arm = []
            for t in s.exec(q2).all():
                if t.want_more or t.lease:
                    building = True
                elif not (harvest.exhausted(t.id) and t.last_run and now - t.last_run < t.interval_minutes * 60):
                    arm.append(t.id)
            if arm:
                s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                s.exec(update(CRThread).where(CRThread.id.in_(arm)).values(want_more=True))
                s.commit()
                building = True
        return {'posts': posts, 'next_cursor': str(offset + len(rows)) if more else '', 'has_more': more,
                'building': building}


class SeenIn(BaseModel):
    pick_ids: list[int] = Field(max_length=200)


@router.post('/seen')
def seen(data: SeenIn, pid: int = Depends(require_room)):
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        s.exec(update(CRPick).where(CRPick.profile_id == pid, CRPick.id.in_(data.pick_ids or [0]), CRPick.seen_at == 0)
               .values(seen_at=time.time()))
        s.commit()
    return {'ok': True}


def _pick(s, pid: int, pick_id: int) -> CRPick:
    pick = s.get(CRPick, pick_id)
    if not pick or pick.profile_id != pid:
        raise HTTPException(404, 'Post not found')
    return pick


class FeedbackIn(BaseModel):
    verdict: str = Field(pattern='^(up|down|)$')
    reasons: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default='', max_length=300)


@router.put('/picks/{pick_id}/feedback')
def feedback(pick_id: int, data: FeedbackIn, pid: int = Depends(require_room)):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        pick = _pick(s, pid, pick_id)
        post = s.get(CRPost, pick.post_id)
        f = s.exec(select(CRFeedback).where(CRFeedback.profile_id == pid, CRFeedback.thread_id == pick.thread_id,
                                            CRFeedback.post_id == pick.post_id)).first()
        if not data.verdict and not data.note.strip() and not data.reasons:
            if f:
                s.delete(f)
        else:
            f = f or CRFeedback(profile_id=pid, thread_id=pick.thread_id, post_id=pick.post_id)
            f.verdict, f.note = data.verdict, data.note.strip()
            f.reasons = [r for r in data.reasons if r in REASONS]
            f.title, f.community, f.created_at = (post.title if post else '')[:300], (post.community if post else ''), now
            s.add(f)
        pick.hidden = data.verdict == 'down'
        pick.hidden_at = now if pick.hidden else 0
        s.add(pick)
        s.commit()
    return {'ok': True}


class PickEdit(BaseModel):
    saved: bool | None = None
    hidden: bool | None = None


@router.put('/picks/{pick_id}')
def edit_pick(pick_id: int, data: PickEdit, pid: int = Depends(require_room)):
    with session() as s:
        pick = _pick(s, pid, pick_id)
        if data.saved is not None:
            pick.saved = data.saved
        if data.hidden is not None:
            pick.hidden, pick.hidden_at = data.hidden, time.time() if data.hidden else 0
        s.add(pick)
        s.commit()
    return {'ok': True}


def _post_for(s, pid: int, post_id: int) -> tuple[CRPost, CRPick, CRThread]:
    row = s.exec(_visible_picks(s, pid).where(CRPick.post_id == post_id).limit(1)).first()
    if not row:
        raise HTTPException(404, 'Post not found')
    pick, post, thread = row
    return post, pick, thread


@router.get('/posts/{post_id}')
def get_post(post_id: int, pid: int = Depends(require_room)):
    with session() as s:
        post, pick, thread = _post_for(s, pid, post_id)
        verdict = s.exec(select(CRFeedback.verdict).where(CRFeedback.profile_id == pid, CRFeedback.post_id == post_id)).first() or ''
        from .kids import BODY_SEEN
        child = _child(s, pid)
        return {**_post_json(pick, post, thread, child, verdict),
                'body': post.body[:BODY_SEEN] if child else post.body}


@router.get('/posts/{post_id}/comments')
def comments(post_id: int, pid: int = Depends(require_room)):
    from . import kids
    with session() as s:
        post, _, _ = _post_for(s, pid, post_id)
        child = _child(s, pid)
        cached = s.get(CRComments, post_id)
        data = {'url': post.url, 'external_id': post.external_id, 'community': post.community, 'meta': post.meta or {},
                'platform': post.platform}
        fresh = cached and time.time() - cached.fetched_at < COMMENTS_TTL
        if fresh and (not child or cached.kid is not None):
            return {'comments': cached.kid if child else cached.tree, 'url': post.url}
    adapter = ADAPTERS.get(data['platform'])
    tree = cached.tree if fresh else []
    if not fresh and adapter and adapter.comments:
        try:
            tree = adapter.comments(data) or []
        except Exception:  # noqa: BLE001 — comments are optional
            tree = cached.tree if cached else []
    kid = kids.filter_comments(tree) if child else None
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = s.get(CRComments, post_id) or CRComments(post_id=post_id)
        if not fresh:
            row.tree, row.fetched_at, row.kid = tree, time.time(), None
        if kid is not None:
            row.kid = kid
        s.add(row)
        s.commit()
    return {'comments': kid if child else tree, 'url': data['url']}


@router.get('/posts/{post_id}/media/{n}')
def media(post_id: int, n: int, pid: int = Depends(require_room)):
    """The post's own pictures through our server: never a general URL fetcher."""
    from ..hub.api import IMG_CACHE, IMG_MAX_BYTES, IMG_TYPES, _image_mime, _prune_img_cache
    with session() as s:
        post, _, _ = _post_for(s, pid, post_id)
        if _child(s, pid):
            asset = s.get(CRAsset, post_id) if post.kid_image_ok == 1 and n == 0 else None
            if not asset:
                raise HTTPException(404, 'No picture for this post.')
            return Response(asset.data, media_type=asset.mime, headers={'Cache-Control': 'private, no-store',
                                                                         'X-Content-Type-Options': 'nosniff'})
        items = post.media or []
        if not 0 <= n < len(items):
            raise HTTPException(404, 'No picture for this post.')
        m = items[n]
        url = m.get('poster') if m.get('kind') == 'video' else m.get('url')
    if not url:
        raise HTTPException(404, 'No picture for this post.')
    try:
        url = curation.canonical(url)
    except ValueError:
        raise HTTPException(404, 'No picture for this post.')
    key = hashlib.sha256(url.encode()).hexdigest()
    path = IMG_CACHE / key[:2] / key
    if path.is_file():
        data = path.read_bytes()
        mime = _image_mime(data)
        if mime:
            return Response(data, media_type=mime, headers={'Cache-Control': 'private, max-age=86400'})
    try:
        data = curation.fetch(url, kinds=IMG_TYPES, limit=IMG_MAX_BYTES)
    except Exception:  # noqa: BLE001
        raise HTTPException(404, 'That picture could not be fetched.')
    mime = _image_mime(data)
    if not mime:
        raise HTTPException(404, 'That picture is not a supported image.')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        _prune_img_cache(time.time())
    except OSError:
        pass
    return Response(data, media_type=mime, headers={'Cache-Control': 'private, max-age=86400'})


# ---------- blocks

class BlockIn(BaseModel):
    kind: str = Field(pattern='^(community|author|domain)$')
    value: str = Field(min_length=1, max_length=200)


@router.get('/blocks')
def list_blocks(pid: int = Depends(require_room)):
    with session() as s:
        rows = s.exec(select(CRBlock).where(CRBlock.profile_id == pid).order_by(CRBlock.created_at.desc())).all()
        return {'blocks': [{'id': b.id, 'kind': b.kind, 'value': b.value} for b in rows]}


@router.post('/blocks')
def add_block(data: BlockIn, pid: int = Depends(require_room)):
    value = data.value.strip().lower()
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        b = s.exec(select(CRBlock).where(CRBlock.profile_id == pid, CRBlock.kind == data.kind, CRBlock.value == value)).first()
        if not b:
            b = CRBlock(profile_id=pid, kind=data.kind, value=value)
            s.add(b)
        # Hide what is already on the wall from that community/author/domain.
        col = {'community': CRPost.community, 'author': CRPost.author, 'domain': CRPost.domain}[data.kind]
        posts = select(CRPost.id).where(func.lower(col) == value)
        s.exec(update(CRPick).where(CRPick.profile_id == pid, CRPick.post_id.in_(posts), CRPick.hidden == False)  # noqa: E712
               .values(hidden=True, hidden_at=now))
        s.commit()
        s.refresh(b)
        return {'id': b.id, 'kind': b.kind, 'value': b.value}


@router.delete('/blocks/{bid}')
def remove_block(bid: int, pid: int = Depends(require_room)):
    with session() as s:
        b = s.get(CRBlock, bid)
        if not b or b.profile_id != pid:
            raise HTTPException(404, 'Not found')
        s.delete(b)
        s.commit()
    return {'ok': True}


# ---------- housekeeping

def new_count(s, pid: int) -> int:
    v = s.exec(select(CRVisit).where(CRVisit.profile_id == pid, CRVisit.scope == 'all')).first()
    since = v.visit_at if v else 0
    base = _visible_picks(s, pid).where(CRPick.hidden == False, CRPick.picked_at > since)  # noqa: E712
    return s.exec(select(func.count()).select_from(base.subquery())).one()


def _delete_threads(s, tids: list[int]):
    if not tids:
        return
    for model, col in ((CRPick, CRPick.thread_id), (CRFeedback, CRFeedback.thread_id),
                       (CRCandidate, CRCandidate.thread_id), (CRThreadSource, CRThreadSource.thread_id),
                       (CRThread, CRThread.id)):
        s.exec(delete(model).where(col.in_(tids)))
    s.exec(delete(CRVisit).where(CRVisit.scope.in_([str(t) for t in tids])))


def delete_profile(s, pid: int):
    _delete_threads(s, list(s.exec(select(CRThread.id).where(CRThread.profile_id == pid)).all()))
    s.exec(delete(CRBlock).where(CRBlock.profile_id == pid))
    s.exec(delete(CRVisit).where(CRVisit.profile_id == pid))
