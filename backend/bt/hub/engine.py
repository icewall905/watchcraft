"""Curator execution: fetch sources, dedupe into canonical items, score, route to outputs."""
import asyncio
import hashlib
import logging
import re
import time
from urllib.parse import urlsplit

from sqlalchemy import text, update
from sqlmodel import Session, select

from .. import curation
from ..access import hub_allowed
from ..db import engine as db_engine, get_settings, session
from . import activity, ai, polling, slots, narrate
from .models import (Alert, Briefing, CollectionItem, ContentItem, Curator, CuratorDiscovery, CuratorRun,
                     CuratorSource, PageRevision, Source, SourceCheck, TopicItem)
from .providers import PROVIDERS

log = logging.getLogger(__name__)
LEASE_S = 900
SEVERITY = ['info', 'low', 'medium', 'high', 'critical']
TRACKING = re.compile(r'^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src)$', re.I)


def record_page_revision(src: Source, data: dict, observed_at: float | None = None) -> None:
    """Record a changed observation, serializing concurrent checks of the same source."""
    title, body = data['title'], data['body']
    digest = hashlib.sha256((title + '\0' + body).encode()).hexdigest()
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        current = s.get(Source, src.id)
        if (not current or current.profile_id != src.profile_id or not current.enabled
                or current.url != src.url or not hub_allowed(s, src.profile_id)):
            return
        last = s.exec(select(PageRevision).where(PageRevision.source_id == src.id)
                      .order_by(PageRevision.id.desc()).limit(1)).first()
        if last and observed_at is not None and last.observed_at > observed_at:
            return
        if last and last.digest == digest:
            return
        s.add(PageRevision(source_id=src.id, profile_id=src.profile_id, digest=digest, title=title, body=body,
                           observed_at=observed_at if observed_at is not None else time.time()))
        s.flush()
        old = s.exec(select(PageRevision).where(PageRevision.source_id == src.id)
                     .order_by(PageRevision.id.desc()).offset(20)).all()
        for revision in old:
            s.delete(revision)
        s.commit()


def record_source_check(src: Source, trigger: str, count: int, error: str = '', checked_at: float | None = None) -> bool:
    """Record a completed check only if its source and profile still match the request."""
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(Source, src.id)
        if not row or row.profile_id != src.profile_id or row.url != src.url or not hub_allowed(s, src.profile_id):
            return False
        now = checked_at if checked_at is not None else time.time()
        if row.last_checked is not None and row.last_checked > now:
            return False
        status = 'partial' if error and count else 'failed' if error else 'ok'
        row.last_checked, row.last_error = now, error
        if status != 'failed':
            row.last_success, row.item_count = now, count
        s.add(row)
        s.add(SourceCheck(source_id=src.id, profile_id=src.profile_id, checked_at=now, trigger=trigger,
                          status=status, item_count=count, error=error))
        s.flush()
        for old in s.exec(select(SourceCheck).where(SourceCheck.source_id == src.id)
                          .order_by(SourceCheck.id.desc()).offset(20)).all():
            s.delete(old)
        s.commit()
        return True


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
        # A sitemap supplies no title, so its URL placeholder is not an established fact.
        if item.provider == 'sitemap' and item.title == item.url and data.get('title') and data['title'] != item.url:
            item.title = data['title'][:500]; changed = True
        for k in ('summary', 'author', 'publisher', 'image', 'published_at', 'body'):
            if data.get(k) and not getattr(item, k):
                setattr(item, k, data[k]); changed = True
        if data.get('enrichment') and not item.enrichment.get('summary'):
            item.enrichment = {**item.enrichment, **data['enrichment']}; changed = True
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
                       provider=data.get('provider') or provider, external_id=data.get('external_id', ''), meta=data.get('meta') or {},
                       enrichment=data.get('enrichment') or {})
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

def claim(cid: int, trigger: str = 'schedule') -> bool:
    with session() as s:
        curator = s.get(Curator, cid)
        if not curator or not hub_allowed(s, curator.profile_id):
            return False
        cond = [Curator.id == cid, Curator.next_run <= time.time()]
        if trigger == 'schedule':
            cond.append(Curator.enabled == True)  # paused while it waited for a slot
        res = s.exec(update(Curator).where(*cond)
                     .values(next_run=time.time() + LEASE_S, last_status='running'))
        s.commit()
        return res.rowcount == 1


def run(cid: int, trigger: str = 'schedule') -> CuratorRun | None:
    """Execute one curator. Idempotent across processes via the next_run lease."""
    if not claim(cid, trigger):
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
        if cur:
            activity.emit(cur, r.id, 'failed', narrate.say((cur.tone or 'friendly'), 'curator.failed', seed=str(r.id)))
        return row


def start(cid: int) -> None:
    """Run now, in the background; user-started runs never queue (the lease still prevents overlap)."""
    with session() as s:
        c = s.get(Curator, cid)
        pid, name = (c.profile_id, c.name) if c else (None, '')
    slots.spawn(f'curator-{cid}', 'curator', cid, pid, name, run, cid, 'manual', priority=True)


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
    activity.emit(c, r.id, 'started', narrate.say(c.tone, 'curator.started', seed=str(r.id),
                                                  sources=narrate.phrase(len(sources), 'source')))
    queries = [q for q in (c.queries or []) if q.strip()][:8] or ([c.subject] if c.subject else [])
    for src in sources:
        label = (src.name or src.provider)[:100]
        activity.emit(c, r.id, 'checking', narrate.say(c.tone, 'curator.checking', seed=f'{r.id}-{src.id}', label=label), source_id=src.id)
        prov = PROVIDERS.get(src.provider)
        ok, reason = prov.availability() if prov else (False, 'Unknown provider')
        if not ok:
            record_source_check(src, 'curator', 0, reason)
            activity.emit(c, r.id, 'source_error', narrate.say(c.tone, 'curator.source_unavailable', seed=f'{r.id}-{src.id}',
                            label=label), source_id=src.id, error=reason)
            errors.append({'source': src.name or src.provider, 'message': reason}); continue
        got, failed, checked_at, shared = 0, None, None, False
        for q in (queries if prov.mode == 'query' else ['']):
            with session() as s:
                if not hub_allowed(s, c.profile_id):
                    raise ValueError('Hub access was restricted during the run.')
            try:
                if prov.mode == 'feed':
                    result = polling.fetch(prov, src.url, _safe_error, force=r.trigger == 'manual')
                    checked_at, shared = result.fetched_at, result.shared
                    if result.error:
                        raise ValueError(result.error)
                    fetched = result.items
                else:
                    fetched = prov.fetch(src.url, q)
                if src.provider == 'page_watch':
                    record_page_revision(src, fetched[0], checked_at)
                for d in fetched:
                    candidates.append((src, q, dict(d))); got += 1
            except Exception as e:  # noqa: BLE001 — one source never fails the run
                log.info('source %s failed: %s', src.id, e)
                failed = _safe_error(e)
        if prov.mode == 'query' and not queries:
            failed = 'No search queries configured.'
        record_source_check(src, 'shared' if shared else 'curator', got, failed or '', checked_at)
        if failed:
            errors.append({'source': src.name or src.provider, 'message': failed})
            activity.emit(c, r.id, 'source_error', narrate.say(c.tone, 'curator.source_error', seed=f'{r.id}-{src.id}',
                            label=label, got=narrate.phrase(got, 'item')), source_id=src.id, error=failed)
        else:
            activity.emit(c, r.id, 'reused' if shared else 'checked',
                          narrate.say(c.tone, 'curator.reused' if shared else 'curator.checked', seed=f'{r.id}-{src.id}',
                                      label=label, got=narrate.phrase(got, 'item')), source_id=src.id)

    # Score: keywords first (cheap), then optional AI over the survivors.
    activity.emit(c, r.id, 'reviewing', narrate.say(c.tone, 'curator.reviewing', seed=str(r.id),
                                                   got=narrate.phrase(len(candidates), 'source item')))
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
    activity.emit(c, r.id, 'matched', narrate.say(c.tone, 'curator.matched', seed=str(r.id),
                                                  fresh=narrate.phrase(len(fresh), 'unseen matching item')))
    if c.ai_enabled and fresh and ai.enabled():
        activity.emit(c, r.id, 'ranking', narrate.say(c.tone, 'curator.ranking', seed=str(r.id),
                                                      fresh=narrate.phrase(len(fresh), 'item')))
        try:
            for x, (score, reason) in zip(fresh, ai.rank(c, [x[2] for x in fresh])):
                x[3], x[4] = score, reason or x[4]
        except Exception as e:  # noqa: BLE001 — AI is optional; keep keyword scores
            errors.append({'source': 'AI relevance', 'message': f'AI scoring unavailable ({_safe_error(e)}); used keyword scores.'})
            activity.emit(c, r.id, 'ranking_error', 'AI ranking was unavailable; kept the keyword scores.', error=_safe_error(e))
    new = 0
    with session() as s:
        c = s.get(Curator, cid)
        if not c or not hub_allowed(s, c.profile_id):
            raise ValueError('Hub access was restricted during the run.')
        for src, q, d, score, reason in fresh:
            if score < c.min_score:
                continue
            item = upsert(s, d, src.provider)
            if not item or s.exec(select(CuratorDiscovery).where(CuratorDiscovery.curator_id == cid, CuratorDiscovery.item_id == item.id)).first():
                continue
            s.add(CuratorDiscovery(curator_id=cid, item_id=item.id, profile_id=c.profile_id, source_id=src.id,
                                   query=q, score=score, reason=reason))
            from .capture import enqueue
            enqueue(s, c.profile_id, item.id)
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
    activity.emit(c, r.id, 'completed', narrate.say(c.tone, 'curator.completed', seed=str(r.id),
                        new=narrate.phrase(new, 'new item'), seen=narrate.phrase(len(candidates), 'source item'),
                        issues=narrate.phrase(len(errors), 'issue')), status=status)
    if c.kind == 'research' and new and ai.enabled():
        try:
            activity.emit(c, r.id, 'writing_report', narrate.say(c.tone, 'curator.writing_report', seed=str(r.id)))
            report = ai.research_report(cid)
            if report is not None:
                activity.emit(c, r.id, 'report_ready', narrate.say(c.tone, 'curator.report_ready', seed=str(r.id)))
        except Exception as e:  # noqa: BLE001
            log.info('report failed: %s', e)
            activity.emit(c, r.id, 'report_error', narrate.say(c.tone, 'curator.report_error', seed=str(r.id)), error=_safe_error(e))
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
            await asyncio.to_thread(dispatch_due)
        except Exception:  # noqa: BLE001 — never kill the loop
            log.exception('hub scheduler tick failed')
        await asyncio.sleep(30)


def dispatch_due() -> None:
    """Start due agents in the background, never more than the free agent slots."""
    from . import scout
    free = slots.free()
    if free <= 0:
        return
    free -= scout.run_due(free)
    now = time.time()
    with session() as s:
        curators = s.exec(select(Curator).where(Curator.enabled == True, Curator.next_run <= now)
                          .order_by(Curator.next_run)).all()
        briefs = s.exec(select(Briefing).where(Briefing.frequency != 'manual', Briefing.next_run <= now)
                        .order_by(Briefing.next_run)).all()
    for c in curators:
        if free <= 0:
            return
        if slots.spawn(f'curator-{c.id}', 'curator', c.id, c.profile_id, c.name, run, c.id):
            free -= 1
    for b in briefs:
        if free <= 0:
            return
        with session() as s:
            # Lease the briefing so a failure retries later instead of every tick.
            claimed = s.exec(update(Briefing).where(Briefing.id == b.id, Briefing.next_run == b.next_run)
                             .values(next_run=now + LEASE_S))
            s.commit()
        if claimed.rowcount and slots.spawn(f'briefing-{b.id}', 'briefing', b.id, b.profile_id, b.name,
                                            ai.generate_briefing, b.id):
            free -= 1

    from .models import ScoutReportConfig
    from .scout_report import scheduled
    with session() as s:
        reports = s.exec(select(ScoutReportConfig).where(ScoutReportConfig.frequency != 'manual',
                         ScoutReportConfig.next_run <= now).order_by(ScoutReportConfig.next_run)).all()
    for config in reports:
        if free <= 0: return
        with session() as s:
            claimed = s.exec(update(ScoutReportConfig).where(ScoutReportConfig.profile_id == config.profile_id,
                             ScoutReportConfig.next_run == config.next_run).values(next_run=now + LEASE_S))
            s.commit()
        if claimed.rowcount and slots.spawn(f'scout-report-{config.profile_id}', 'scout_report', None,
                                            config.profile_id, 'Scout Report', scheduled,
                                            config.profile_id, config.frequency, now + LEASE_S):
            free -= 1

    if free > 0:
        from . import capture
        capture.dispatch()


slots.after_release = dispatch_due


def schedule_after(interval_hours: int) -> float:
    return time.time() + interval_hours * 3600 if interval_hours else 1e12


def settings_ai() -> bool:
    return bool(get_settings()['search'].get('hub_ai', True))


__all__ = ['run', 'scheduler', 'upsert', 'canonical_url', 'ensure_fts', 'index', 'db_engine']
