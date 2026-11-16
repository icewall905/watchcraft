"""Choose posts for a thread: rule filter → embedding ranking → LLM judge → picks.

Every post a thread looked at gets a CRCandidate row, so nothing is judged twice. Posts the judge
could not reach this run stay pending for the next one."""
import logging
import math
import time

from sqlalchemy import or_
from sqlmodel import select

from .. import access
from ..db import get_settings, session
from ..hub import ai, semantic
from . import kids, store, teach
from .models import (CRBlock, CRCandidate, CRPick, CRPost, CRPostVector, CRSourcePost, CRThread, CRThreadSource)

log = logging.getLogger(__name__)
THRESHOLD = {'strict': 75, 'balanced': 60, 'loose': 45}
POOL = 400
JUDGE_MAX = 60
BATCH = 15
EMBED_BATCH = 32
SCHEMA = {'type': 'object', 'properties': {'scores': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'n': {'type': 'integer'}, 'score': {'type': 'integer', 'minimum': 0, 'maximum': 100}, 'why': {'type': 'string'}},
    'required': ['n', 'score', 'why']}}}, 'required': ['scores']}


def _pool(s, thread: CRThread) -> list[CRPost]:
    judged = select(CRCandidate.post_id).where(CRCandidate.thread_id == thread.id, CRCandidate.status != 'pending')
    from_sources = select(CRSourcePost.post_id).join(CRThreadSource, CRThreadSource.source_id == CRSourcePost.source_id) \
        .where(CRThreadSource.thread_id == thread.id, CRThreadSource.enabled == True)  # noqa: E712
    from_search = select(CRCandidate.post_id).where(CRCandidate.thread_id == thread.id, CRCandidate.status == 'pending')
    rows = s.exec(select(CRPost).where(or_(CRPost.id.in_(from_sources), CRPost.id.in_(from_search)),
                                       CRPost.id.not_in(judged))
                  .order_by(CRPost.published_at.desc()).limit(POOL)).all()
    for r in rows:
        s.expunge(r)
    return list(rows)


def _blocked(post: CRPost, blocks: list[CRBlock]) -> bool:
    for b in blocks:
        v = b.value.lower()
        if (b.kind == 'community' and post.community.lower() == v) or (b.kind == 'author' and post.author.lower() == v) \
                or (b.kind == 'domain' and (post.domain == v or post.domain.endswith('.' + v))):
            return True
    return False


def _avoided(post: CRPost, words: list[str]) -> bool:
    text = f'{post.title}\n{post.body[:3000]}'.lower()
    return any(w in text for w in words)


def _text(p: CRPost) -> str:
    return f'{p.title}\n{p.community}\n{p.body[:800]}'


def _vectors(posts: list[CRPost]) -> dict[int, list[float]]:
    """Embed posts that lack a vector for the current model. Returns every vector available."""
    ok, _ = semantic.available()
    if not ok or not posts:
        return {}
    model = semantic.config()['model']
    ids = [p.id for p in posts]
    out: dict[int, list[float]] = {}
    with session() as s:
        for row in s.exec(select(CRPostVector).where(CRPostVector.post_id.in_(ids), CRPostVector.model == model)).all():
            v = teach._vec(row.vector, row.dim)
            if v is not None:
                out[row.post_id] = list(v)
    missing = [p for p in posts if p.id not in out]
    fresh: dict[int, list[float]] = {}
    for start in range(0, len(missing), EMBED_BATCH):
        batch = missing[start:start + EMBED_BATCH]
        try:
            vecs = semantic.embed([_text(p) for p in batch])
        except ValueError as e:
            log.info('Common Room embeddings failed: %s', e)
            break
        fresh.update({p.id: v for p, v in zip(batch, vecs)})
    if fresh:
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            for pid, v in fresh.items():
                row = s.get(CRPostVector, pid) or CRPostVector(post_id=pid)
                row.model, row.dim, row.vector = model, len(v), semantic._pack(v)
                s.add(row)
            s.commit()
    out.update(fresh)
    return out


def _rank(thread: CRThread, posts: list[CRPost]) -> list[tuple[CRPost, float]]:
    """Relevance 0..1 before the judge: embeddings when configured, else engagement and freshness."""
    vecs = _vectors(posts)
    goal = taste = None
    if vecs:
        try:
            goal = semantic.embed([f'{thread.name}\n{thread.interests}\n{thread.instructions}'])[0]
        except ValueError:
            goal = None
        with session() as s:
            taste = teach.taste(s, thread.id, semantic.config()['model'])
    now = time.time()
    out = []
    for p in posts:
        fresh = math.exp(-max(0.0, now - p.published_at) / (7 * 86400))
        buzz = min(1.0, math.log1p(max(0, p.score) + 2 * max(0, p.comments)) / 8)
        base = 0.6 * fresh + 0.4 * buzz
        v = vecs.get(p.id)
        if v and goal:
            sim = teach.cosine(v, goal) + (0.5 * teach.cosine(v, taste) if taste else 0.0)
            base = 0.75 * sim + 0.25 * base
        out.append((p, base))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _system(thread: CRThread) -> str:
    rules = [f'You curate one thread of a personal social feed, named "{thread.name}".',
             f'The owner is interested in: {thread.interests or thread.name}.']
    if thread.instructions.strip():
        rules.append(f'Owner instructions: {thread.instructions.strip()}')
    if thread.avoid.strip():
        rules.append(f'Never keep posts about: {thread.avoid.strip()}')
    rules.append('Score each numbered post 0-100 for how much the owner would want to see it in this thread. '
                 'High: clearly on-topic, substantive, novel, useful or delightful, worth opening. '
                 'Low: off-topic, low-effort, reposts, rage bait, self-promotion or ads, help requests nobody '
                 'else would learn from, or only loosely related. "why" is one short line (max 15 words) telling '
                 'the owner why it fits, written to them.')
    if thread.child_friendly:
        rules.append('This thread is read by a child: score 0 anything not suitable for a 10-12 year old.')
    return ' '.join(rules)


def _judge(thread: CRThread, lessons: str, batch: list[CRPost]) -> dict[int, tuple[int, str]] | None:
    items = [{'type': 'post', 'title': p.title, 'publisher': f'{p.platform} {p.community}'.strip(),
              'summary': p.body[:600], 'url': p.link_url or p.url,
              **({'body': p.body[:2000]} if len(p.body) > 600 else {})} for p in batch]
    user = ai._data(items)
    if lessons:
        user = '<data>\n' + ai._esc(lessons) + '\n</data>\n\n' + user
    try:
        res = ai._call(_system(thread), user, SCHEMA, max_tokens=1800, role='worker')
    except Exception as e:  # noqa: BLE001 — retried next run
        log.info('Common Room judge failed for thread %s: %s', thread.id, e)
        return None
    out = {}
    for row in res.get('scores', []) if isinstance(res, dict) else []:
        n = row.get('n') if isinstance(row, dict) else None
        if isinstance(n, int) and 1 <= n <= len(batch):
            try:
                score = max(0, min(100, int(row.get('score'))))
            except (TypeError, ValueError):
                continue
            out[batch[n - 1].id] = (score, ' '.join(str(row.get('why') or '').split())[:200])
    return out


def _settle(thread: CRThread, lease: str, decided: dict[int, tuple[str, int, str]], posts: dict[int, CRPost]):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        live = s.get(CRThread, thread.id)
        if not live or live.lease != lease:  # deleted or taken over while we worked
            s.rollback()
            return
        have = {c.post_id: c for c in s.exec(select(CRCandidate).where(CRCandidate.thread_id == thread.id,
                                                                         CRCandidate.post_id.in_(list(decided)))).all()}
        picked = set(s.exec(select(CRPick.post_id).where(CRPick.thread_id == thread.id,
                                                         CRPick.post_id.in_(list(decided)))).all())
        for pid, (status, score, why) in decided.items():
            c = have.get(pid) or CRCandidate(thread_id=thread.id, post_id=pid)
            c.status = status
            s.add(c)
            if status == 'kept' and pid not in picked:
                s.add(CRPick(profile_id=thread.profile_id, thread_id=thread.id, post_id=pid, score=score, why=why,
                             published_at=posts[pid].published_at, picked_at=now))
        s.commit()


def run(thread: CRThread, lease: str) -> dict:
    with session() as s:
        posts = _pool(s, thread)
        blocks = list(s.exec(select(CRBlock).where(CRBlock.profile_id == thread.profile_id)).all())
        lessons = teach.prompt(s, thread)
        child = access.is_child(s, thread.profile_id)
        languages = set(store.allowed_languages(s, thread.profile_id))
    show_nsfw = bool(get_settings()['commonroom'].get('show_nsfw')) and not child and not thread.child_friendly
    words = [w.strip().lower() for w in (thread.avoid or '').replace('\n', ',').split(',') if len(w.strip()) > 2]
    decided: dict[int, tuple[str, int, str]] = {}
    keep = []
    for p in posts:
        if (p.nsfw and not show_nsfw) or ((child or thread.child_friendly) and p.spoiler) \
                or _blocked(p, blocks) or (words and _avoided(p, words)) \
                or (languages and p.language and p.language not in languages):
            decided[p.id] = ('rejected', 0, '')
        else:
            keep.append(p)
    ranked = _rank(thread, keep)[:JUDGE_MAX]
    threshold = THRESHOLD.get(thread.strictness, 60)
    scores: dict[int, tuple[int, str]] = {}
    if ai.enabled():
        for start in range(0, len(ranked), BATCH):
            got = _judge(thread, lessons, [p for p, _ in ranked[start:start + BATCH]])
            if got is not None:
                scores.update(got)
                for p, _ in ranked[start:start + BATCH]:
                    scores.setdefault(p.id, (0, ''))  # answered batch, post missing from answer: reject
    else:
        scores = {p.id: (int(round(rel * 100)), '') for p, rel in ranked}
    kept = [pid for pid, (score, _) in scores.items() if score >= threshold]
    if child or thread.child_friendly:
        allowed, failed = kids.check_posts(kept)
        for pid in kept:
            if pid in failed:
                scores[pid] = (0, '')
            elif pid not in allowed:
                scores.pop(pid)  # the safety model gave no answer: judge again next run
    for pid, (score, why) in scores.items():
        decided[pid] = ('kept' if score >= threshold else 'rejected', score, why)
    _settle(thread, lease, decided, {p.id: p for p in posts})
    return {'pool': len(posts), 'judged': len(scores), 'kept': sum(1 for d in decided.values() if d[0] == 'kept')}
