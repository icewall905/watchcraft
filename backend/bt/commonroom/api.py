"""Common Room HTTP API under /api/hub/commonroom. Profile-scoped; child profiles see kid-checked posts only."""
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, case, delete, func, or_, update
from sqlmodel import select

from ..access import hub_worker_allowed, is_child, profile_id
from ..db import session
from . import expedition, harvest, social, store
from .adapters import ADAPTERS
from .models import (CRAsset, CRBlock, CRCandidate, CRComments, CRFeedback, CRPick, CRPost, CRSource, CRThread,
                     CRSourcePost, CRThreadSource, CRVisit)

router = APIRouter(prefix='/hub/commonroom', tags=['Common Room'])
PAGE = 30
MAX_THREADS = 50
MAX_SOURCES = 60
COMMENTS_TTL = 15 * 60      # an empty stored tree is fetched live again after this
COMMENTS_REFRESH_S = 120    # a manual refresh is honoured once the stored copy is this old
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
    allowed = store.allowed_languages(s, pid)
    if allowed:  # only the chosen languages; a post whose language is unknown still shows
        q = q.where(or_(CRPost.language == '', CRPost.language.in_(allowed)))
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
    from . import assets
    failed = set((post.meta or {}).get('media_failed') or [])

    def gone(url: str) -> bool:  # the source stopped serving it and we never got a copy
        return url in failed and not assets.path(url).is_file()
    out = []
    for n, m in enumerate(post.media or []):
        if m.get('poster') and gone(m['poster']):
            m = {**m, 'poster': ''}
        if m.get('kind') != 'video' and gone(m.get('url') or ''):
            continue
        if m.get('kind') == 'video':
            out.append({'kind': 'video', 'url': m.get('url') or '',
                        'poster': f'/api/hub/commonroom/posts/{post.id}/media/{n}?size=thumb' if m.get('poster') else ''})
        else:
            u = f'/api/hub/commonroom/posts/{post.id}/media/{n}'
            out.append({'kind': 'image', 'url': u, 'thumb': u + '?size=thumb', 'poster': ''})
    return out


def _post_json(pick: CRPick, post: CRPost, thread: CRThread, child: bool, verdict: str = '') -> dict:
    body = post.body or ''
    return {'pick_id': pick.id, 'post_id': post.id, 'thread_id': thread.id, 'thread_name': thread.name,
            'platform': post.platform, 'community': post.community, 'author': post.author, 'title': post.title,
            'excerpt': body[:600] + ('…' if len(body) > 600 else ''), 'url': post.url, 'link_url': post.link_url,
            'domain': post.domain, 'media': _media(post, child), 'score': post.score, 'comments': post.comments,
            'published_at': post.published_at, 'why': pick.why, 'relevance': pick.score, 'saved': pick.saved,
            'verdict': verdict, 'nsfw': post.nsfw, 'spoiler': post.spoiler,
            'fun': pick.fun, 'kind': pick.kind, 'wildcard': pick.wildcard, 'top': [], 'elsewhere': []}


ELSEWHERE_MAX = 4
CUTS_TTL_S = 600
CUTS_WINDOW_S = 2 * 86400
_cuts: list = [0.0, {}]


def _velocity_cuts(s) -> dict:
    """Per-platform rising/hot cut-offs from posts published in the last two days (cached for ten minutes).
    Older posts re-fetched lately would drag the cut-offs to near zero."""
    now = time.time()
    if now - _cuts[0] > CUTS_TTL_S:
        rows = s.exec(select(CRPost.platform, CRPost.meta, CRPost.score, CRPost.comments, CRPost.published_at)
                      .where(CRPost.published_at >= now - CUTS_WINDOW_S).order_by(CRPost.id.desc()).limit(5000)).all()
        _cuts[:] = [now, social.thresholds((r[0], social.velocity(r[1], r[2], r[3], r[4], now)) for r in rows)]
    return _cuts[1]


def _socialize(s, rows: list, out: list[dict], child: bool) -> list[dict]:
    """Add top comments and other discussions of the same link; fold same-link duplicates on one page into
    the copy with the most comments. Child profiles get kid-checked comments only and no outside links."""
    by_post = {post.id: post for _, post, _ in rows}
    ids = list(by_post)
    if not ids:
        return out
    now, cuts = time.time(), _velocity_cuts(s)
    for o in out:
        p = by_post[o['post_id']]
        o['velocity'] = round(social.velocity(p.meta, p.score, p.comments, p.published_at, now), 2)
        o['badges'] = social.badges(p.platform, o['velocity'], p.published_at, now, cuts, p.score, p.comments)
    for c in s.exec(select(CRComments).where(CRComments.post_id.in_(ids))).all():
        tree = c.kid if child else c.tree
        for o in out:
            if o['post_id'] == c.post_id and tree:
                o['top'] = social.top_comments(tree, by_post[c.post_id].author)
    if child:
        return out
    keys = {p.link_key for p in by_post.values() if p.link_key and p.link_key != '-'}
    if not keys:
        return out
    groups: dict[str, list] = {}
    for r in s.exec(select(CRPost.id, CRPost.link_key, CRPost.platform, CRPost.community, CRPost.url, CRPost.comments,
                           CRPost.nsfw).where(CRPost.link_key.in_(list(keys))).limit(400)).all():
        groups.setdefault(r.link_key, []).append(r)
    keep, owner = [], {}
    for o in out:
        key = by_post[o['post_id']].link_key
        if key in owner:  # the same link twice on one page: keep the livelier discussion
            first = owner[key]
            if o['comments'] > first['comments']:
                keep[keep.index(first)] = o
                owner[key] = o
            continue
        if key in groups:
            owner[key] = o
        keep.append(o)
    for key, o in owner.items():
        seen = {o['url']}
        others = []
        for r in sorted(groups[key], key=lambda r: -(r.comments or 0)):
            if r.url in seen or r.nsfw:
                continue
            seen.add(r.url)
            others.append({'platform': r.platform, 'community': r.community, 'url': r.url, 'comments': r.comments})
        o['elsewhere'] = others[:ELSEWHERE_MAX]
    return keep


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
        s.flush()
        expedition.enqueue(s, t.id, 'new')
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
            t.discovered_at = 0  # the next run queues an expedition for the new interests
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
            'label': src.label or expedition.describe(src.platform, src.locator), 'origin': link.origin,
            'enabled': link.enabled, 'error': src.error, 'last_ok': src.last_ok or None, 'status': src.status,
            'fit': link.fit, 'tried': link.tried, 'kept': link.kept, 'hits': src.hits,
            'url': _source_url(src)}


def _source_url(src: CRSource) -> str:
    if src.platform == 'web':
        try:
            r = expedition.web_adapter.recipe_of(src.locator)
            return r.get('url') or r.get('url_template', '').split('?')[0]
        except ValueError:
            return ''
    if src.platform == 'reddit':
        return 'https://www.reddit.com/' + src.locator
    return src.locator if src.locator.startswith('https://') else ''


def _sources_json(s, tid: int) -> dict:
    rows = s.exec(select(CRThreadSource, CRSource).join(CRSource, CRSource.id == CRThreadSource.source_id)
                  .where(CRThreadSource.thread_id == tid).order_by(CRSource.platform, CRSource.locator)).all()
    return {'sources': [_source_json(link, src) for link, src in rows],
            'hunt': expedition.hunt_json(expedition.latest(s, tid))}


@router.get('/threads/{tid}/sources')
def list_sources(tid: int, pid: int = Depends(require_room)):
    with session() as s:
        _owned(s, pid, tid)
        return _sources_json(s, tid)


@router.post('/threads/{tid}/find-sources')
def find_sources(tid: int, pid: int = Depends(require_room)):
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        _owned(s, pid, tid)
        if expedition.active(s, tid):
            raise HTTPException(409, 'A source expedition is already running for this thread.')
        expedition.enqueue(s, tid, 'button')
        s.commit()
        return _sources_json(s, tid)


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
    thread: int | str = 'all'  # the page sends a thread id as a number


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
        scope = _scope(s, pid, str(data.thread))
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


MODES = ('today', 'hot', 'new', 'deep', 'top', 'saved')
TODAY_N = 15
TODAY_WINDOW_S = 36 * 3600
HOT_WINDOW_S = 3 * 86400
TOP_WINDOW_S = 7 * 86400
POOL_MAX = 400


def _heat(o: dict, cuts: dict) -> float:
    """How hot a post is for its own platform (1.0 = at the hot cut-off); 0 for feeds without numbers and for
    posts below the badge floors."""
    hot = cuts.get(o['platform'], (0.0, 0.0))[1]
    if not {'hot', 'rising'} & set(o.get('badges') or ()):
        return 0.0
    return min(3.0, o.get('velocity', 0) / hot) if hot else 0.0


def _ranked(s, pid: int, q, mode: str, child: bool, verdicts_for) -> list[dict]:
    """today/hot: rank a bounded recent pool in Python, where liveliness (which moves) can count."""
    now = time.time()
    window = TODAY_WINDOW_S if mode == 'today' else HOT_WINDOW_S
    rows = s.exec(q.where(CRPick.published_at >= now - window).order_by(CRPick.published_at.desc())
                  .limit(POOL_MAX)).all()
    if mode == 'today' and len(rows) < TODAY_N:  # a quiet day: reach back a week
        rows = s.exec(q.where(CRPick.published_at >= now - TOP_WINDOW_S).order_by(CRPick.published_at.desc())
                      .limit(POOL_MAX)).all()
    verdicts = verdicts_for([p.id for _, p, _ in rows])
    rows = [r for r in rows if not r[0].hidden]
    out = _socialize(s, rows, [_post_json(pick, post, t, child, verdicts.get(post.id, '')) for pick, post, t in rows],
                     child)
    cuts = _velocity_cuts(s)
    if mode == 'hot':
        key = lambda o: _heat(o, cuts) * 50 + o['relevance'] * 0.3 + o['fun'] * 0.2  # noqa: E731
    else:  # today: the best of the day, with a nudge for what is buzzing and for fun
        key = lambda o: o['relevance'] + o['fun'] * 0.5 + _heat(o, cuts) * 15  # noqa: E731
    out.sort(key=key, reverse=True)
    return out


@router.get('/feed')
def feed(thread: str = 'all', section: str = '', mode: str = '', kind: str = '', boundary: float | None = None,
         cursor: str = '', pid: int = Depends(require_room)):
    """One page of the wall. `mode` picks the view; `section` (new|deep) is the older name for new/deep."""
    from .curate import KINDS
    mode = mode or section or 'new'
    if mode not in MODES:
        raise HTTPException(400, 'mode must be one of ' + ', '.join(MODES))
    if kind and kind not in KINDS:
        raise HTTPException(400, 'unknown kind')
    section = mode
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
        if kind:
            q = q.where(CRPick.kind == kind)

        def verdicts_for(ids):
            return dict(s.exec(select(CRFeedback.post_id, CRFeedback.verdict).where(
                CRFeedback.profile_id == pid, CRFeedback.post_id.in_(ids or [0]))).all())
        if mode in ('today', 'hot'):
            ranked = _ranked(s, pid, q, mode, child, verdicts_for)
            if mode == 'today':
                page = social.mix(ranked[:TODAY_N])
                return {'posts': page, 'next_cursor': '', 'has_more': False, 'building': False, 'mode': mode,
                        'end': True}
            page = social.mix(ranked[offset:offset + PAGE])
            more = len(ranked) > offset + PAGE
            return {'posts': page, 'next_cursor': str(offset + PAGE) if more else '', 'has_more': more,
                    'building': False, 'mode': mode, 'end': not more}
        if mode == 'saved':
            q = q.where(CRPick.saved == True).order_by(CRPick.id.desc())  # noqa: E712
        elif mode == 'top':
            q = q.where(CRPick.published_at >= visit_at - TOP_WINDOW_S) \
                .order_by((CRPick.score + CRPick.fun * 0.5).desc(), CRPick.id.desc())
        elif section == 'new':
            q = q.where(CRPick.picked_at > edge, CRPick.picked_at <= visit_at) \
                .order_by(CRPick.published_at.desc(), CRPick.id.desc())
        else:
            # Stable for the whole visit: the pool known at the visit start ranked first (unseen before seen),
            # then everything found while scrolling, appended in discovery order.
            age_days = (visit_at - CRPick.published_at) / 86400.0
            rank = CRPick.score + CRPick.fun * 0.3 - age_days * 2  # only fields fixed for the visit keep offsets stable
            later = CRPick.picked_at > visit_at
            seen = (CRPick.seen_at > 0) & (CRPick.seen_at < visit_at)
            q = q.where((CRPick.picked_at <= edge) | later) \
                .order_by(later, case((later, CRPick.picked_at), else_=0.0), seen, rank.desc(), CRPick.id.desc())
        rows = s.exec(q.offset(offset).limit(PAGE + 1)).all()
        more = len(rows) > PAGE
        rows = rows[:PAGE]
        verdicts = verdicts_for([p.id for _, p, _ in rows])
        shown = [r for r in rows if not (r[0].hidden and verdicts.get(r[1].id) != 'down')]
        posts = _socialize(s, shown, [_post_json(pick, post, t, child, verdicts.get(post.id, ''))
                                      for pick, post, t in shown], child)
        if mode in ('deep', 'top'):
            posts = social.mix(posts)
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
            dry = []
            for t in s.exec(q2).all():
                if t.want_more or t.lease or expedition.active(s, t.id):
                    building = True
                elif not (harvest.exhausted(t.id) and t.last_run and now - t.last_run < t.interval_minutes * 60):
                    arm.append(t.id)
                else:
                    dry.append(t.id)
            if arm:
                s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                s.exec(update(CRThread).where(CRThread.id.in_(arm)).values(want_more=True))
                s.commit()
                building = True
            for tid in dry:  # every source walked to its end: send an expedition for new places
                building = expedition.request(tid, 'dry') or building
        return {'posts': posts, 'next_cursor': str(offset + len(rows)) if more else '', 'has_more': more,
                'building': building, 'mode': mode, 'end': not more and not building}


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
        one = _socialize(s, [(pick, post, thread)], [_post_json(pick, post, thread, child, verdict)], child)[0]
        return {**one, 'body': post.body[:BODY_SEEN] if child else post.body}


@router.get('/posts/{post_id}/comments')
def comments(post_id: int, refresh: bool = False, pid: int = Depends(require_room)):
    """The stored comment tree, kept current by the scheduler sweep; fetched live only when nothing is stored
    yet, or on request (refresh) when the stored copy is older than a few minutes. Child profiles get the
    kid-checked copy."""
    from . import kids
    with session() as s:
        post, _, _ = _post_for(s, pid, post_id)
        child = _child(s, pid)
        cached = s.get(CRComments, post_id)
        data = {'url': post.url, 'external_id': post.external_id, 'community': post.community, 'meta': post.meta or {},
                'platform': post.platform}
        post_comments = post.comments
        tree, kid, fetched = (cached.tree or [], cached.kid, cached.fetched_at) if cached else ([], None, 0.0)
    from .assets import comment_source, discussion_url
    read = comment_source(data)
    supported = read is not None
    now = time.time()
    live = supported and ((refresh and now - fetched > COMMENTS_REFRESH_S) or (not tree and now - fetched > COMMENTS_TTL))
    if live:
        try:
            got = read(data) or []
        except Exception:  # noqa: BLE001 — comments are optional; the stored copy stays
            got = []
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = s.get(CRComments, post_id) or CRComments(post_id=post_id)
            row.checked_at = now
            if got or not row.tree:  # an empty answer never wipes a stored tree
                row.tree, row.kid, row.fetched_at, row.count = got, None, now, post_comments
            tree, kid, fetched = row.tree or [], row.kid, row.fetched_at
            s.add(row)
            s.commit()
    if child and kid is None:
        kid = kids.filter_comments(tree) if tree else []
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = s.get(CRComments, post_id)
            if row and (row.tree or []) == tree:
                row.kid = kid
                s.add(row)
            s.commit()
    elsewhere = '' if child else discussion_url(data)
    return {'comments': kid if child else tree, 'url': elsewhere or data['url'], 'fetched_at': fetched,
            'supported': supported, 'discussion': elsewhere}


@router.get('/posts/{post_id}/media/{n}')
def media(post_id: int, n: int, size: str = '', pid: int = Depends(require_room)):
    """The post's own pictures from the local copy (saved when the post was picked); the source is only a
    fallback for a picture not saved yet. Never a general URL fetcher."""
    from . import assets
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
    if not url or not url.startswith('https://'):
        raise HTTPException(404, 'No picture for this post.')
    headers = {'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'}
    data = assets.local(url, thumb=size == 'thumb')
    if data is None:
        try:
            if not assets.save(url):
                raise HTTPException(404, 'That picture is not a supported image.')
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            raise HTTPException(404, 'That picture could not be fetched.')
        data = assets.local(url, thumb=size == 'thumb')
    mime = assets.mime_of(data or b'')
    if not mime:
        raise HTTPException(404, 'That picture is not a supported image.')
    return Response(data, media_type=mime, headers=headers)


# ---------- languages

class LanguagesIn(BaseModel):
    languages: list[str] = Field(default_factory=list, max_length=60)


@router.get('/languages')
def get_languages(pid: int = Depends(require_room)):
    """Languages of this profile's posts (most first) and the ones it chose; none chosen = all."""
    with session() as s:
        threads = select(CRThread.id).where(CRThread.profile_id == pid)
        sources = select(CRThreadSource.source_id).where(CRThreadSource.thread_id.in_(threads))
        mine = or_(CRPost.id.in_(select(CRCandidate.post_id).where(CRCandidate.thread_id.in_(threads))),
                   CRPost.id.in_(select(CRSourcePost.post_id).where(CRSourcePost.source_id.in_(sources))))
        seen = s.exec(select(CRPost.language, func.count()).where(mine, CRPost.language != '')
                      .group_by(CRPost.language).order_by(func.count().desc()).limit(30)).all()
        return {'languages': [{'key': k, 'count': n} for k, n in seen], 'allowed': store.allowed_languages(s, pid)}


@router.put('/languages')
def set_languages(body: LanguagesIn, pid: int = Depends(require_room)):
    import re
    from ..models import Profile
    codes: list[str] = []
    for raw in body.languages:
        code = raw.strip().lower()
        if not re.fullmatch(r'[a-z]{2,3}', code):
            raise HTTPException(422, 'Use language codes such as da or en.')
        if code not in codes:
            codes.append(code)
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        p = s.get(Profile, pid)
        p.prefs = {**(p.prefs or {}), 'commonroom_languages': codes}
        s.add(p)
        s.commit()
    return {'allowed': codes}


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
