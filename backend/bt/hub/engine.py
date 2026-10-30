"""Curator execution: fetch sources, dedupe into canonical items, score, route to outputs."""
import asyncio
import logging
import re
import threading
import time
from urllib.parse import urlsplit

from sqlalchemy import text, update
from sqlmodel import Session, select

from .. import curation
from ..db import engine as db_engine, get_settings, session
from . import ai
from .models import (Alert, Briefing, CollectionItem, ContentItem, Curator, CuratorDiscovery, CuratorRun,
                     CuratorSource, Source, TopicItem)
from .providers import PROVIDERS

log = logging.getLogger(__name__)
LEASE_S = 900
SEVERITY = ['info', 'low', 'medium', 'high', 'critical']
TRACKING = re.compile(r'^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src)$', re.I)


# ---------- full-text index (FTS5, rowid = content item id)

def ensure_fts(conn) -> None:
    conn.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(title, summary, body, people, tokenize='porter unicode61')")


def index(s: Session, item: ContentItem) -> None:
    s.exec(text('DELETE FROM content_fts WHERE rowid = :id').bindparams(id=item.id))
    s.exec(text('INSERT INTO content_fts(rowid, title, summary, body, people) VALUES (:id, :t, :s, :b, :p)').bindparams(
        id=item.id, t=item.title, s=item.summary, b=item.body, p=f'{item.author} {item.publisher} {item.meta.get("cve_id", "")}'))


# ---------- canonical content

def canonical_url(url: str) -> str:
    """HTTPS-normalise and drop tracking params so the same story from two curators is one item."""
    u = url.strip()
    if u.startswith('http://'):
        u = 'https://' + u[7:]
    c = curation.canonical(u)
    p = urlsplit(c)
    query = '&'.join(x for x in p.query.split('&') if x and not TRACKING.match(x.split('=')[0]))
    path = p.path.rstrip('/') or '/'
    return p._replace(query=query, path=path).geturl()


def upsert(s: Session, data: dict, provider: str) -> ContentItem | None:
    try:
        url = canonical_url(data['url'])
    except (ValueError, KeyError):
        return None
    item = s.exec(select(ContentItem).where(ContentItem.url == url)).first()
    if item:
        # Fill gaps from richer sources; never overwrite established facts.
        changed = False
        for k in ('summary', 'author', 'publisher', 'image', 'published_at', 'body'):
            if data.get(k) and not getattr(item, k):
                setattr(item, k, data[k]); changed = True
        if data.get('meta'):
            merged = {**data['meta'], **item.meta}
            if merged != item.meta:
                item.meta = merged; changed = True
        if changed:
            s.add(item); s.flush(); index(s, item)
        return item
    item = ContentItem(url=url, title=(data.get('title') or url)[:500], type=data.get('type') or PROVIDERS[provider].default_type,
                       summary=data.get('summary', ''), body=data.get('body', ''), author=data.get('author', ''),
                       publisher=data.get('publisher', ''), image=data.get('image', ''), published_at=data.get('published_at'),
                       provider=data.get('provider') or provider, external_id=data.get('external_id', ''), meta=data.get('meta') or {})
    s.add(item); s.flush(); index(s, item)
    return item


# ---------- relevance

def _terms(values) -> list[str]:
    return [v.strip().lower() for v in values or [] if isinstance(v, str) and v.strip()]


def keyword_score(c: Curator, data: dict) -> tuple[float | None, str]:
    """Deterministic relevance without AI. None = excluded."""
    hay = f"{data.get('title', '')} {data.get('summary', '')} {data.get('meta', {}).get('product', '')} {data.get('meta', {}).get('vendor', '')}".lower()
    for x in _terms(c.exclusions):
        if x in hay:
            return None, f'Excluded term "{x}"'
    kws = _terms(c.keywords)
    if c.content_types and data.get('type') not in c.content_types:
        return None, 'Content type not selected'
    if not kws:
        return 50.0, ''
    hits = [k for k in kws if k in hay]
    if not hits:
        return 0.0, ''
    return min(100.0, 50 + 50 * len(hits) / len(kws)), 'Matches ' + ', '.join(f'"{h}"' for h in hits[:4])


def _feed_needs_keywords(provider: str) -> bool:
    return provider == 'cisa_kev'  # a global catalogue: only keep what the curator asked about


# ---------- running

def claim(cid: int) -> bool:
    with session() as s:
        res = s.exec(update(Curator).where(Curator.id == cid, Curator.next_run <= time.time())
                     .values(next_run=time.time() + LEASE_S, last_status='running'))
        s.commit()
        return res.rowcount == 1


def run(cid: int, trigger: str = 'schedule') -> CuratorRun | None:
    """Execute one curator. Idempotent across processes via the next_run lease."""
    if not claim(cid):
        return None
    with session() as s:
        c = s.get(Curator, cid)
        r = CuratorRun(curator_id=cid, profile_id=c.profile_id, trigger=trigger)
        s.add(r); s.commit(); s.refresh(r)
    try:
        return _execute(c, r)
    except Exception:  # noqa: BLE001 — never leave a curator stuck in "running"
        log.exception('curator %s run failed', cid)
        with session() as s:
            row, cur = s.get(CuratorRun, r.id), s.get(Curator, cid)
            row.finished_at, row.status = time.time(), 'failed'
            row.errors = [*row.errors, {'source': 'WatchCraft', 'message': 'Internal error during the run; it will retry on schedule.'}]
            if cur:
                cur.last_run, cur.last_status = time.time(), 'failed'
                cur.error = 'Internal error during the last run.'
                cur.next_run = schedule_after(cur.interval_hours)
                s.add(cur)
            s.add(row); s.commit(); s.refresh(row)
            return row


def start(cid: int) -> None:
    """Run now, in the background (the lease still prevents overlap with the scheduler)."""
    threading.Thread(target=run, args=(cid, 'manual'), daemon=True, name=f'curator-{cid}').start()


def recover_stale() -> None:
    """After a crash/restart: runs still marked running past the lease are failed."""
    with session() as s:
        for row in s.exec(select(CuratorRun).where(CuratorRun.status == 'running', CuratorRun.started_at < time.time() - LEASE_S)).all():
            row.status, row.finished_at = 'failed', time.time()
            row.errors = [*row.errors, {'source': 'WatchCraft', 'message': 'Interrupted (server restart or crash).'}]
            s.add(row)
        for c in s.exec(select(Curator).where(Curator.last_status.in_(['running', 'queued']), Curator.next_run < time.time())).all():
            c.last_status = 'failed'
            s.add(c)
        s.commit()


def _execute(c: Curator, r: CuratorRun) -> CuratorRun:
    cid = c.id
    with session() as s:
        sources = s.exec(select(Source).join(CuratorSource, CuratorSource.source_id == Source.id)
                         .where(CuratorSource.curator_id == cid, Source.enabled == True)).all()
    errors, candidates = [], []
    queries = [q for q in (c.queries or []) if q.strip()][:8] or ([c.subject] if c.subject else [])
    for src in sources:
        prov = PROVIDERS.get(src.provider)
        ok, reason = prov.availability() if prov else (False, 'Unknown provider')
        if not ok:
            errors.append({'source': src.name or src.provider, 'message': reason}); continue
        got, failed = 0, None
        for q in (queries if prov.mode == 'query' else ['']):
            try:
                for d in prov.fetch(src.url, q):
                    candidates.append((src, q, d)); got += 1
            except Exception as e:  # noqa: BLE001 — one source never fails the run
                log.info('source %s failed: %s', src.id, e)
                failed = _safe_error(e)
        if prov.mode == 'query' and not queries:
            failed = 'No search queries configured.'
        with session() as s:
            row = s.get(Source, src.id)
            row.last_checked = time.time()
            if failed and not got:
                row.last_error = failed
                errors.append({'source': src.name or src.provider, 'message': failed})
            else:
                row.last_success, row.last_error = time.time(), ''
                row.item_count = got
            s.add(row); s.commit()

    # Score: keywords first (cheap), then optional AI over the survivors.
    scored = []
    seen_urls = set()
    for src, q, d in candidates:
        try:
            key = canonical_url(d['url'])
        except (ValueError, KeyError):
            continue
        if key in seen_urls:
            continue
        seen_urls.add(key)
        score, reason = keyword_score(c, d)
        # Feeds are unfiltered firehoses: with keywords set, keep matches only. Query results already match a query.
        if score is None or (score == 0 and (PROVIDERS[src.provider].mode != 'query' or _feed_needs_keywords(src.provider))):
            continue
        scored.append([src, q, d, score, reason])
    with session() as s:
        known = set(s.exec(select(ContentItem.url).join(CuratorDiscovery, CuratorDiscovery.item_id == ContentItem.id)
                                      .where(CuratorDiscovery.curator_id == cid)).all())
    fresh = [x for x in scored if canonical_url(x[2]['url']) not in known][: max(1, c.max_per_run)]
    if c.ai_enabled and fresh and ai.enabled():
        try:
            for x, (score, reason) in zip(fresh, ai.rank(c, [x[2] for x in fresh])):
                x[3], x[4] = score, reason or x[4]
        except Exception as e:  # noqa: BLE001 — AI is optional; keep keyword scores
            errors.append({'source': 'AI relevance', 'message': f'AI scoring unavailable ({_safe_error(e)}); used keyword scores.'})
    new = 0
    with session() as s:
        c = s.get(Curator, cid)
        for src, q, d, score, reason in fresh:
            if score < c.min_score:
                continue
            item = upsert(s, d, src.provider)
            if not item or s.exec(select(CuratorDiscovery).where(CuratorDiscovery.curator_id == cid, CuratorDiscovery.item_id == item.id)).first():
                continue
            s.add(CuratorDiscovery(curator_id=cid, item_id=item.id, profile_id=c.profile_id, source_id=src.id,
                                   query=q, score=score, reason=reason))
            new += 1
            route(s, c, item, reason)
        status = 'ok' if not errors else ('failed' if sources and len(errors) >= len(sources) and not candidates else 'partial')
        if not sources:
            status, errors = 'failed', [{'source': 'Curator', 'message': 'No sources selected.'}]
        r = s.get(CuratorRun, r.id)
        r.finished_at, r.status, r.new_items, r.seen_items, r.errors = time.time(), status, new, len(candidates), errors
        c.last_run = time.time()
        c.last_status = status
        if status != 'failed':
            c.last_success = time.time()
        c.error = '' if status == 'ok' else '; '.join(f"{e['source']}: {e['message']}" for e in errors)[:500]
        c.next_run = time.time() + c.interval_hours * 3600 if c.interval_hours else 1e12
        s.add(r); s.add(c); s.commit(); s.refresh(r)
    if c.kind == 'research' and new and ai.enabled():
        try:
            ai.research_report(cid)
        except Exception as e:  # noqa: BLE001
            log.info('report failed: %s', e)
    return r


def route(s: Session, c: Curator, item: ContentItem, reason: str) -> None:
    """Explicit, user-configured destinations only."""
    if c.collection_id and not s.get(CollectionItem, (c.collection_id, item.id)):
        s.add(CollectionItem(collection_id=c.collection_id, item_id=item.id))
    if c.topic_id and not s.get(TopicItem, (c.topic_id, item.id)):
        s.add(TopicItem(topic_id=c.topic_id, item_id=item.id))
    if not c.alert_enabled:
        return
    hay = f'{item.title} {item.summary}'.lower()
    sev = (item.meta.get('severity') or '').lower()
    hits = [k for k in _terms(c.alert_keywords) if k in hay]
    min_i = SEVERITY.index(c.alert_min_severity) if c.alert_min_severity in SEVERITY else 3
    by_sev = sev in SEVERITY and SEVERITY.index(sev) >= min_i
    if not (hits or by_sev or (item.meta.get('known_exploited') and min_i <= 3)):
        return
    if s.exec(select(Alert).where(Alert.profile_id == c.profile_id, Alert.item_id == item.id)).first():
        return
    why = []
    if item.meta.get('known_exploited'):
        why.append('CISA lists this as exploited in the wild')
    if by_sev:
        why.append(f"{item.meta.get('source', 'Source')} severity {sev}" + (f" (CVSS {item.meta['cvss_score']})" if item.meta.get('cvss_score') else ''))
    if hits:
        why.append('matches alert keywords ' + ', '.join(f'"{h}"' for h in hits[:3]))
    applicability = ' Possibly relevant — verify affected versions against your systems.' if item.type == 'advisory' else ''
    s.add(Alert(profile_id=c.profile_id, item_id=item.id, curator_id=c.id, severity=sev if sev in SEVERITY else ('high' if hits else 'info'),
                title=item.title, reason=('; '.join(why) + '.' + applicability).strip()))


def _safe_error(e: Exception) -> str:
    """Short provider error without URLs, headers or credentials."""
    if isinstance(e, ValueError):
        return str(e)[:200]
    name = type(e).__name__
    status = getattr(getattr(e, 'response', None), 'status_code', None)
    return f'{name} (HTTP {status})' if status else f'{name}: source unreachable or returned invalid data'


async def scheduler() -> None:
    """Durable schedule: state lives in SQLite, so restarts resume where they left off."""
    await asyncio.to_thread(recover_stale)
    while True:
        try:
            with session() as s:
                ids = [c.id for c in s.exec(select(Curator).where(Curator.enabled == True, Curator.next_run <= time.time())).all()]
                briefs = [b.id for b in s.exec(select(Briefing).where(Briefing.frequency != 'manual', Briefing.next_run <= time.time())).all()]
            for cid in ids:
                await asyncio.to_thread(run, cid)
            for bid in briefs:
                await asyncio.to_thread(ai.generate_briefing, bid)
        except Exception:  # noqa: BLE001 — never kill the loop
            log.exception('hub scheduler tick failed')
        await asyncio.sleep(30)


def schedule_after(interval_hours: int) -> float:
    return time.time() + interval_hours * 3600 if interval_hours else 1e12


def settings_ai() -> bool:
    return bool(get_settings()['search'].get('hub_ai', True))


__all__ = ['run', 'scheduler', 'upsert', 'canonical_url', 'ensure_fts', 'index', 'db_engine']
