"""Choose posts for a thread: rule filter → embedding ranking → LLM judge → picks.

Every post a thread looked at gets a CRCandidate row, so nothing is judged twice. Posts the judge
could not reach this run stay pending for the next one; a post an answered batch skipped is retried
once before it counts as rejected.

Besides its own sources, a thread also sees recent posts fetched for the owner's other threads when
embeddings say they are close to its interests, so every fetch can feed several threads."""
import logging
import math
import time
from typing import NamedTuple
from urllib.parse import urlsplit

from sqlalchemy import or_
from sqlmodel import select

from .. import access
from ..db import get_settings, session
from ..hub import ai, semantic
from . import joy, kids, social, store, teach
from .models import (CRBlock, CRCandidate, CRPick, CRPost, CRPostVector, CRSource, CRSourcePost, CRThread,
                     CRThreadSource)

log = logging.getLogger(__name__)
THRESHOLD = {'strict': 75, 'balanced': 60, 'loose': 45}
POOL = 400
JUDGE_MAX = 60
JUDGE_MAX_MORE = 120   # the feed ran low: judge deeper
MISS_LIMIT = 2         # answered batches that skipped a post before it is rejected
CROSS_POOL = 150       # recent posts from the owner's other threads considered per run
CROSS_WINDOW_S = 3 * 86400
CROSS_MIN = 0.45       # embedding relevance a borrowed post needs before it is judged
BATCH = 15
EMBED_BATCH = 32
KINDS = ('humor', 'showcase', 'question', 'discussion', 'news', 'deepdive', 'oddity')
WILD_FUN = 80          # a delightful post may be this fun ...
WILD_SLACK = 20        # ... and this far under the threshold and still be kept as a wildcard
TALK_BONUS_MAX = 8     # a post people are already discussing gets up to this much help over the keep line
REL_WEIGHT, FUN_WEIGHT = 0.6, 0.4   # the keep line weighs relevance and fun: on-topic alone is not enough
REL_FLOOR = 10         # ... but a regular keep is at most this far under the threshold on relevance
DULL_FUN = 35          # a post this unfun is dull ...
DULL_MARGIN = 15       # ... and is kept only this far over the threshold on relevance ...
DULL_TALK = 10         # ... or with a real conversation going
POP_WINDOW_S = 14 * 86400
NEUTRAL_FUN = 50       # fun assumed when no judge is configured
# Aggregator-like sources that bring listings, not conversations: kept only once someone replies to them.
AGGREGATOR_DOMAINS = ('awesome.ecosyste.ms', 'ossalt.com', 'rss.feedspot.com')
AGGREGATOR_URL_BITS = ('infiniterss.php',)   # feed-title scrapers that wrap someone else's headline
PROMO_AUTHORS = ('radwebhosting',)
SCHEMA = {'type': 'object', 'properties': {'scores': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'n': {'type': 'integer'}, 'score': {'type': 'integer', 'minimum': 0, 'maximum': 100},
    'fun': {'type': 'integer', 'minimum': 0, 'maximum': 100}, 'kind': {'type': 'string', 'enum': list(KINDS)},
    'why': {'type': 'string'}},
    'required': ['n', 'score', 'fun', 'kind', 'why']}}}, 'required': ['scores']}


class Verdict(NamedTuple):
    score: int
    why: str
    fun: int = 0
    kind: str = ''


def talk_bonus(comments: int) -> int:
    """Modest help over the keep line for posts with a conversation: 1 reply +2, 3 +4, 15+ +8."""
    return min(TALK_BONUS_MAX, round(2 * math.log2(1 + max(0, comments or 0))))


def _on(host: str, domains) -> bool:
    host = (host or '').lower()
    return any(host == d or host.endswith('.' + d) for d in domains)


def aggregator(p) -> bool:
    """A directory, feed-title scraper or promo account: listings and ads rather than posts people talk about."""
    urls = f'{p.url} {p.link_url}'.lower()
    hosts = [p.domain] + [urlsplit(u).hostname or '' for u in (p.url, p.link_url) if u]
    author = (p.author or '').lower()
    return any(_on(h, AGGREGATOR_DOMAINS) for h in hosts) or any(b in urls for b in AGGREGATOR_URL_BITS) \
        or any(a in author for a in PROMO_AUTHORS)


def capped(p) -> bool:
    """Aggregator-like posts nobody engaged with never reach the judge."""
    return aggregator(p) and not (p.comments or 0) and (p.score or 0) <= 1


def joy_score(v: Verdict, comments: int = 0, bonus: float = 0.0) -> float:
    """Relevance and fun together, plus help for a live conversation and what the owner has enjoyed before."""
    return REL_WEIGHT * v.score + FUN_WEIGHT * v.fun + talk_bonus(comments) + bonus


def dull(v: Verdict, threshold: int, comments: int = 0) -> bool:
    """On-topic but nobody would stop scrolling for it: kept only when very relevant or much discussed."""
    return v.fun < DULL_FUN and v.score < threshold + DULL_MARGIN and (comments or 0) < DULL_TALK


def keeps(v: Verdict, threshold: int, wild: bool, comments: int = 0, bonus: float = 0.0,
          penalty: int = 0) -> tuple[bool, bool]:
    """(kept, wildcard). A post must clear the line on relevance and fun together (joy_score), be close to
    on-topic and not be dull. `bonus` comes from learned priors (joy.bonus); `penalty` from throttled sources.
    Wildcards are loosely on-topic posts kept because they are a delight."""
    line = threshold + penalty
    good = joy_score(v, comments, bonus) >= line or v.score >= line + DULL_MARGIN \
        or (v.score >= line and (comments or 0) >= DULL_TALK)   # fun enough, very relevant, or a real conversation
    if v.score >= line - REL_FLOOR and good and not dull(v, line, comments):
        return True, False
    if wild and v.fun >= WILD_FUN and v.score >= line - WILD_SLACK:
        return True, True
    return False, False


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


def _cross_pool(s, thread: CRThread, have: set[int]) -> list[CRPost]:
    """Recent posts fetched for the owner's other threads that this thread has not judged yet."""
    others = select(CRThread.id).where(CRThread.profile_id == thread.profile_id, CRThread.id != thread.id,
                                       CRThread.enabled == True)  # noqa: E712
    srcs = select(CRThreadSource.source_id).where(CRThreadSource.thread_id.in_(others),
                                                  CRThreadSource.enabled == True)  # noqa: E712
    judged = select(CRCandidate.post_id).where(CRCandidate.thread_id == thread.id)
    rows = s.exec(select(CRPost).join(CRSourcePost, CRSourcePost.post_id == CRPost.id)
                  .where(CRSourcePost.source_id.in_(srcs), CRPost.id.not_in(judged),
                         CRPost.fetched_at >= time.time() - CROSS_WINDOW_S)
                  .order_by(CRPost.published_at.desc()).limit(CROSS_POOL * 2)).all()
    out, seen = [], set(have)
    for r in rows:
        if r.id not in seen:
            seen.add(r.id)
            s.expunge(r)
            out.append(r)
    return out[:CROSS_POOL]


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


def _popularity(s, posts: list[CRPost], now: float) -> dict[int, float]:
    """post_id → percentile of its crowd engagement among recent posts of the same community."""
    keys = {(p.platform, p.community) for p in posts if p.community}
    if not keys:
        return {}
    peers: dict[tuple[str, str], list[float]] = {}
    for platform, community, score, comments in s.exec(
            select(CRPost.platform, CRPost.community, CRPost.score, CRPost.comments)
            .where(CRPost.community.in_({c for _, c in keys}), CRPost.published_at >= now - POP_WINDOW_S)
            .order_by(CRPost.id.desc()).limit(20000)).all():
        if (platform, community) in keys:
            peers.setdefault((platform, community), []).append(social.crowd(score, comments))
    out = {}
    for p in posts:
        pct = social.percentile(social.crowd(p.score, p.comments), peers.get((p.platform, p.community), []))
        if pct is not None:
            out[p.id] = pct
    return out


def _rank(thread: CRThread, posts: list[CRPost], pop: dict[int, float] | None = None,
          lift: dict[int, float] | None = None) -> list[tuple[CRPost, float]]:
    """Likely-to-be-enjoyed 0..1 before the judge: embeddings (sub goal + owner taste) when configured, else
    freshness and engagement; the crowd's verdict within its own community (`pop`) and learned lifts for the
    post's community and sources (`lift`) spend the judge's budget on posts likely to be fun."""
    vecs = _vectors(posts)
    goal = taste = None
    if vecs:
        try:
            goal = semantic.embed([f'{thread.name}\n{thread.interests}\n{thread.instructions}'])[0]
        except ValueError:
            goal = None
        with session() as s:
            taste = teach.taste(s, thread.id, semantic.config()['model'], thread.profile_id)
    now = time.time()
    out = []
    for p in posts:
        fresh = math.exp(-max(0.0, now - p.published_at) / (7 * 86400))
        # Feeds and scraped pages carry no votes: give them a neutral middle instead of zero.
        buzz = 0.35 if not p.score and not p.comments else min(1.0, math.log1p(social.crowd(p.score, p.comments)) / 8)
        if pop and p.id in pop:  # the community's own verdict beats an absolute count
            buzz = 0.5 * buzz + 0.5 * pop[p.id]
        base = 0.6 * fresh + 0.4 * buzz
        v = vecs.get(p.id)
        if v and goal:
            sim = teach.cosine(v, goal) + (0.5 * teach.cosine(v, taste) if taste else 0.0)
            base = 0.75 * sim + 0.25 * base
        if lift:
            base += max(-0.1, min(0.1, lift.get(p.id, 0.0)))
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
    rules.append('This feed replaces browsing Reddit: it should be fun to scroll, not only informative. '
                 'Score each numbered post 0-100 for how much the owner would want to see it in this thread. '
                 'High: clearly on-topic and worth opening: substantive, novel, useful, funny or delightful. '
                 'Low: off-topic, reposts, rage bait, self-promotion or ads, help requests nobody else would '
                 'learn from, or only loosely related. A short post is fine when it is genuinely funny, clever '
                 'or charming. Score 25 or less: SEO filler, promo and hosting/affiliate posts, "top N tools" or '
                 '"alternatives to X" listings, directory or awesome-list entries, and stubs that only repeat a '
                 'headline or link to somewhere else with nothing of their own. '
                 '"fun" is 0-100 for whether a bored person scrolling would stop, click and enjoy it: funny, '
                 'surprising, playful, weird, a lively or opinionated discussion, a cool thing someone built with '
                 'pictures, a great story. Dry announcements, changelogs, press releases, routine version bumps '
                 'and plain help questions score low (under 35) however on-topic they are. Being relevant is not '
                 'enough: a relevant but dull post is not worth keeping unless it is big news for the owner. '
                 '"comments", when given, is how many replies the post already has: a real conversation adds fun. '
                 '"popularity", when given, is how the post did with its own community compared with its other '
                 'recent posts: the crowd is a strong hint for fun, but not for relevance. '
                 '"kind" is the one that fits best: humor (jokes, memes, banter), showcase (something someone '
                 'built or made), question (asking for help or opinions), discussion (debate, hot take, '
                 'open conversation), news (announcement or report), deepdive (long article, analysis, '
                 'tutorial), oddity (strange, surprising or obscure find). '
                 '"why" is one short line (max 15 words) telling the owner why it fits, written to them.')
    if thread.child_friendly:
        rules.append('This thread is read by a child: score 0 anything not suitable for a 10-12 year old.')
    return ' '.join(rules)


def _judge(thread: CRThread, lessons: str, batch: list[CRPost], pop: dict[int, float] | None = None) -> dict[int, Verdict] | None:
    pop = pop or {}
    items = [{'type': 'post', 'title': p.title, 'publisher': f'{p.platform} {p.community}'.strip(),
              'summary': p.body[:600], 'url': p.link_url or p.url,
              **({'comments': p.comments} if getattr(p, 'comments', 0) else {}),
              **({'popularity': social.popularity_text(pop.get(p.id), p.community)}
                 if social.popularity_text(pop.get(p.id), p.community) else {}),
              **({'body': p.body[:kids.BODY_SEEN]} if len(p.body) > 600 else {})} for p in batch]
    user = ai._data(items)
    if lessons:
        user = '<data>\n' + ai._esc(lessons) + '\n</data>\n\n' + user
    try:
        res = ai._call(_system(thread), user, SCHEMA, max_tokens=1800, role='commonroom_curate')
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
            try:
                fun = max(0, min(100, int(row.get('fun') or 0)))
            except (TypeError, ValueError):
                fun = 0
            kind = str(row.get('kind') or '').strip().lower()
            out[batch[n - 1].id] = Verdict(score, ' '.join(str(row.get('why') or '').split())[:200], fun,
                                           kind if kind in KINDS else '')
    return out


def _settle(thread: CRThread, lease: str, decided: dict[int, tuple[str, Verdict, bool]], posts: dict[int, CRPost],
            missed: set[int] = frozenset()):
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        live = s.get(CRThread, thread.id)
        if not live or live.lease != lease:  # deleted or taken over while we worked
            s.rollback()
            return
        ids = list(decided) + [m for m in missed if m not in decided]
        have = {c.post_id: c for c in s.exec(select(CRCandidate).where(CRCandidate.thread_id == thread.id,
                                                                         CRCandidate.post_id.in_(ids))).all()}
        for pid in missed:
            if pid in decided:
                continue
            c = have.get(pid) or CRCandidate(thread_id=thread.id, post_id=pid)
            c.misses += 1
            c.status = 'rejected' if c.misses >= MISS_LIMIT else 'pending'
            s.add(c)
        picked = set(s.exec(select(CRPick.post_id).where(CRPick.thread_id == thread.id,
                                                         CRPick.post_id.in_(list(decided)))).all())
        new_keeps = []
        for pid, (status, v, wild) in decided.items():
            c = have.get(pid) or CRCandidate(thread_id=thread.id, post_id=pid)
            c.status = status
            s.add(c)
            if status == 'kept' and pid not in picked:
                new_keeps.append(pid)
                s.add(CRPick(profile_id=thread.profile_id, thread_id=thread.id, post_id=pid, score=v.score,
                             why=v.why, fun=v.fun, kind=v.kind, wildcard=wild,
                             published_at=posts[pid].published_at, picked_at=now))
        _count_kept(s, thread.id, new_keeps)
        s.commit()


def _count_kept(s, thread_id: int, post_ids: list[int]):
    """Credit the sources a kept post came from: library health and per-thread yield."""
    if not post_ids:
        return
    counts: dict[int, int] = {}
    for sid in s.exec(select(CRSourcePost.source_id).where(CRSourcePost.post_id.in_(post_ids))).all():
        counts[sid] = counts.get(sid, 0) + 1
    if not counts:
        return
    for src in s.exec(select(CRSource).where(CRSource.id.in_(list(counts)))).all():
        src.kept += counts[src.id]
        s.add(src)
    for link in s.exec(select(CRThreadSource).where(CRThreadSource.thread_id == thread_id,
                                                    CRThreadSource.source_id.in_(list(counts)))).all():
        link.kept += counts[link.source_id]
        s.add(link)


def run(thread: CRThread, lease: str) -> dict:
    with session() as s:
        posts = _pool(s, thread)
        cross = _cross_pool(s, thread, {p.id for p in posts}) if semantic.available()[0] else []
        blocks = list(s.exec(select(CRBlock).where(CRBlock.profile_id == thread.profile_id)).all())
        lessons = teach.prompt(s, thread)
        child = access.is_child(s, thread.profile_id)
        languages = set(store.allowed_languages(s, thread.profile_id))
        pri = joy.priors(thread.profile_id, s=s)
        throttled = joy.throttles(s, thread.id)
        everyone = posts + cross
        pop = _popularity(s, everyone, time.time())
        own = set(s.exec(select(CRThreadSource.source_id).where(CRThreadSource.thread_id == thread.id)).all())
        brought: dict[int, list[int]] = {}
        for sid, post_id in s.exec(select(CRSourcePost.source_id, CRSourcePost.post_id).where(
                CRSourcePost.post_id.in_([p.id for p in everyone] or [0]))).all():
            brought.setdefault(post_id, []).append(sid)
    show_nsfw = bool(get_settings()['commonroom'].get('show_nsfw')) and not child and not thread.child_friendly
    words = [w.strip().lower() for w in (thread.avoid or '').replace('\n', ',').split(',') if len(w.strip()) > 2]
    decided: dict[int, tuple[str, Verdict, bool]] = {}
    no = Verdict(0, '')
    keep = []
    for p in posts:
        if (p.nsfw and not show_nsfw) or ((child or thread.child_friendly) and p.spoiler) \
                or _blocked(p, blocks) or (words and _avoided(p, words)) \
                or (languages and p.language and p.language not in languages) or capped(p):
            decided[p.id] = ('rejected', no, False)
        else:
            keep.append(p)
    borrowed = {p.id for p in cross}
    for p in cross:
        if not ((p.nsfw and not show_nsfw) or ((child or thread.child_friendly) and p.spoiler)
                or _blocked(p, blocks) or (words and _avoided(p, words))
                or (languages and p.language and p.language not in languages) or capped(p)):
            keep.append(p)
    # Borrowed posts only reach the judge when embeddings rank them close; the rest wait unjudged.
    lift = {p.id: joy.predict(pri, community=p.community, platform=p.platform, sources=brought.get(p.id, []))
            for p in keep}
    ranked = [(p, rel) for p, rel in _rank(thread, keep, pop, lift) if p.id not in borrowed or rel >= CROSS_MIN]
    ranked = ranked[:JUDGE_MAX_MORE if thread.want_more else JUDGE_MAX]
    if ai.enabled():  # teasers from scraped pages and feeds: judge the whole article, under its real headline
        from . import articles
        articles.complete([p for p, _ in ranked])
    threshold = THRESHOLD.get(thread.strictness, 60)
    wild = not (child or thread.child_friendly)
    scores: dict[int, Verdict] = {}
    missed: set[int] = set()
    if ai.enabled():
        for start in range(0, len(ranked), BATCH):
            got = _judge(thread, lessons, [p for p, _ in ranked[start:start + BATCH]], pop)
            if got is not None:
                scores.update(got)
                missed.update(p.id for p, _ in ranked[start:start + BATCH] if p.id not in got)
    else:
        scores = {p.id: Verdict(int(round(rel * 100)), '', NEUTRAL_FUN) for p, rel in ranked}  # no judge: fun unknown
    talk = {p.id: p.comments for p in everyone}
    by_id = {p.id: p for p in everyone}

    def verdict(pid: int, v: Verdict) -> tuple[bool, bool]:
        p = by_id.get(pid)
        extra = joy.bonus(pri, kind=v.kind, community=p.community if p else '', platform=p.platform if p else '',
                          sources=brought.get(pid, []))
        mine = [sid for sid in brought.get(pid, []) if sid in own]
        return keeps(v, threshold, wild, talk.get(pid, 0), extra, joy.penalty(mine, throttled))
    kept = [pid for pid, v in scores.items() if verdict(pid, v)[0]]
    if child or thread.child_friendly:
        allowed, failed = kids.check_posts(kept)
        for pid in kept:
            if pid in failed:
                scores[pid] = no
            elif pid not in allowed:
                scores.pop(pid)  # the safety model gave no answer: judge again next run
    for pid, v in scores.items():
        ok, wildcard = verdict(pid, v)
        decided[pid] = ('kept' if ok else 'rejected', v, wildcard)
    _settle(thread, lease, decided, by_id, missed)
    kept_v = [d[1] for d in decided.values() if d[0] == 'kept']
    return {'pool': len(posts), 'judged': len(scores), 'kept': len(kept_v),
            'fun': sum(1 for v in kept_v if v.fun >= 60),
            'dull': sum(1 for pid, v in scores.items() if dull(v, threshold, talk.get(pid, 0)))}


# ---------- one-off: fun and kind for picks kept before the judge gave them

REJUDGE_WINDOW_S = 7 * 86400   # Today, Hot and Top only look this far back
REJUDGE_EVERY_S = 60           # at most one judge batch this often
_rejudge = {'at': 0.0, 'tried': set()}


def rejudge(now: float | None = None) -> int:
    """Ask the judge for fun and kind of one batch of recent picks that have none. Score, status and the
    wildcard flag stay as they were. A pick the judge skips is not asked again until restart."""
    now = now or time.time()
    if now - _rejudge['at'] < REJUDGE_EVERY_S or not ai.enabled():
        return 0
    tried = _rejudge['tried']
    with session() as s:
        rows = s.exec(select(CRPick.id, CRPick.thread_id).where(
            CRPick.kind == '', CRPick.published_at >= now - REJUDGE_WINDOW_S)
            .order_by(CRPick.published_at.desc()).limit(500)).all()
        todo = [(pick_id, tid) for pick_id, tid in rows if pick_id not in tried]
        if not todo:
            return 0
        tid = todo[0][1]
        ids = [pick_id for pick_id, t in todo if t == tid][:BATCH]
        thread = s.get(CRThread, tid)
        picks = list(s.exec(select(CRPick).where(CRPick.id.in_(ids))).all())
        posts = {p.id: p for p in s.exec(select(CRPost).where(CRPost.id.in_([k.post_id for k in picks]))).all()}
        lessons = teach.prompt(s, thread) if thread else ''
    _rejudge['at'] = now
    tried.update(ids)
    picks = [k for k in picks if k.post_id in posts]
    if not thread or not picks:
        return 0
    got = _judge(thread, lessons, [posts[k.post_id] for k in picks])
    if not got:
        return 0
    done = 0
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        for k in picks:
            v = got.get(k.post_id)
            live = s.get(CRPick, k.id)
            if v and live and not live.kind:
                live.fun, live.kind = v.fun, v.kind
                s.add(live)
                done += 1
        s.commit()
    log.info('Common Room re-judged %d older picks of thread %s for fun and kind', done, tid)
    return done
