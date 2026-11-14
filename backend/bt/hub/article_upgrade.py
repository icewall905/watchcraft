"""Conservative article identity upgrade for historical web captures, plus a coverage audit.

Older captures dropped the page's structured article identity (fixed in capture for new pages), so
real articles stayed type ``web`` and never reached News. Local archives cannot prove identity: only
cleaned plain text (ArticleText.raw_body), links and assets are kept, never the page markup that
carries JSON-LD/OpenGraph types. So the upgrade re-reads one page at a time on the idle capture lane
and applies exactly the capture rule (capture.merge_page_identity). Nothing else changes: body,
summary and reviewed text stay, existing identity wins, user decisions are never overridden.

The audit records, per profile, why a news-interested watch curator yields (almost) no articles. It
never edits a curator's sources: that stays the user's configuration.
"""
import logging
import time
from collections import Counter
from urllib.parse import urlsplit

import http.client
from sqlalchemy import String, cast, func, or_, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import select

from .. import curation
from ..access import hub_allowed
from ..db import get_kv, save_setting, session
from ..models import Profile, Setting
from . import slots
from .models import (CaptureJob, CapturedAsset, CapturedPage, ContentItem, Curator, CuratorDiscovery,
                     CuratorSource, ItemVerdict, Source)
from .providers import PROVIDERS

log = logging.getLogger(__name__)
WINDOW = 7 * 86400          # older items cannot reach the News window anyway
PROBE_GAP = 120             # at most one identity re-read per two minutes (and only when capture is idle)
PROBE_LEASE = 3600          # a crashed probe may be claimed again after this
RETRY_AFTER = 6 * 3600      # transient fetch failures (429/5xx/timeouts) retry this much later
MAX_ATTEMPTS = 3
SCAN_LIMIT = 200
MAX_SCAN_PAGES = 50
AUDIT_KEY = 'watchcraft_article_coverage'
PROBE_KEY = 'watchcraft_article_upgrade'
AUDIT_INTERVAL = 30 * 60
LOW_YIELD = 2               # fewer article findings than this in 7 days counts as no yield
ARTICLE_TYPES = ('news', 'article')
SOCIAL = ('mastodon', 'mastodon_trends', 'lemmy')


# ---------- candidates

def _open(probe, now) -> bool:
    """No check yet, a crashed check, or a transient failure whose backoff has passed."""
    if not isinstance(probe, dict):
        return True
    if probe.get('result') == 'running':
        return (probe.get('at') or 0) < now - PROBE_LEASE
    return probe.get('result') == 'retry' and (probe.get('next_at') or 0) <= now


def _open_sql(now):
    field = lambda key: func.json_extract(ContentItem.meta, f'$.article_probe.{key}')  # noqa: E731
    return or_(func.json_extract(ContentItem.meta, '$.article_probe').is_(None),
               (field('result') == 'running') & (field('at') < now - PROBE_LEASE),
               (field('result') == 'retry') & (field('next_at') <= now))


def _eligible_sql(s, q):
    """Every exclusion SQL can decide, so ineligible rows never crowd out older candidates."""
    allowed = [pid for pid in s.exec(select(Profile.id).where(Profile.role.in_(('adult', 'admin')))).all()
               if hub_allowed(s, pid)]
    types = cast(Curator.content_types, String)
    return q.where(
        CuratorDiscovery.profile_id.in_(allowed or [-1]),
        ContentItem.provider.not_in(SOCIAL), ContentItem.url.like('https://%'),
        func.coalesce(func.json_extract(ContentItem.meta, '$.social_post'), 0).in_((0, False)),
        Curator.enabled.is_(True), Curator.kind == 'watch',
        or_(func.coalesce(func.json_array_length(Curator.content_types), 0) == 0,
            types.like('%"news"%'), types.like('%"article"%')),
        CaptureJob.status.in_(('complete', 'partial')),
        ~select(CapturedAsset.item_id).where(CapturedAsset.item_id == ContentItem.id,
                                             CapturedAsset.kind == 'document').exists(),
        ~select(ItemVerdict.item_id).where(ItemVerdict.item_id == ContentItem.id, or_(
            ItemVerdict.overruled != '', ItemVerdict.forced.is_(True))).exists())


def _rows(s, now, pid=None, unchecked=True, limit=None, offset=0, eligible=False):
    published = func.coalesce(ContentItem.published_at, ContentItem.first_seen)
    q = (select(ContentItem.id, ContentItem.url, ContentItem.provider, ContentItem.meta,
                CuratorDiscovery.profile_id, Curator.content_types, Curator.enabled, Curator.kind,
                CaptureJob.status)
         .join(CuratorDiscovery, CuratorDiscovery.item_id == ContentItem.id)
         .join(Curator, (Curator.id == CuratorDiscovery.curator_id) & (Curator.profile_id == CuratorDiscovery.profile_id))
         .join(Profile, Profile.id == CuratorDiscovery.profile_id)
         .join(CapturedPage, CapturedPage.item_id == ContentItem.id)
         .outerjoin(CaptureJob, (CaptureJob.item_id == ContentItem.id) & (CaptureJob.profile_id == CuratorDiscovery.profile_id))
         .where(ContentItem.type == 'web', ContentItem.body != '', published >= now - WINDOW,
                Profile.role.in_(('adult', 'admin'))))
    if pid is not None:
        q = q.where(CuratorDiscovery.profile_id == pid)
    if unchecked:
        q = q.where(_open_sql(now))
    if eligible:
        q = _eligible_sql(s, q)
    q = q.order_by(ContentItem.first_seen.desc(), ContentItem.id.desc(), CuratorDiscovery.id)
    return s.exec(q.offset(offset).limit(limit) if limit else q).all()


def _reason(s, row, now, allowed):
    """Why this (item, discovering profile) pair is not a probe candidate; '' when it is."""
    iid, url, provider, meta, pid, types, enabled, kind, job_status = row
    meta = meta if isinstance(meta, dict) else {}
    probe = meta.get('article_probe')
    if not _open(probe, now):
        return 'checked:' + str(probe.get('result') or 'unknown')
    if provider in SOCIAL or meta.get('social_post'):
        return 'social_post'
    if not str(url).startswith('https://'):
        return 'not_https'
    if pid not in allowed:
        allowed[pid] = hub_allowed(s, pid)
    if not allowed[pid]:
        return 'profile_unavailable'
    if not enabled or kind != 'watch' or (types and not set(types) & set(ARTICLE_TYPES)):
        return 'curator_not_news'
    if job_status not in ('complete', 'partial'):
        return 'capture_not_finished'
    if s.get(CapturedAsset, (iid, 'document')):
        return 'document'
    decided = s.exec(select(ItemVerdict.item_id).where(ItemVerdict.item_id == iid, or_(
        ItemVerdict.overruled != '', ItemVerdict.forced.is_(True))).limit(1)).first()
    if decided is not None:
        return 'user_or_rule_decision'
    return ''


def stats(s, now=None, pid=None) -> dict:
    """Aggregate counts only (no titles, URLs or text) for the CLI dry run."""
    now = time.time() if now is None else now
    allowed, best = {}, {}
    for row in _rows(s, now, pid, unchecked=False):
        reason = _reason(s, row, now, allowed)
        # An item is eligible if any discovering profile/curator makes it so.
        if row[0] not in best or reason == '':
            best[row[0]] = reason
    reasons = Counter(r or 'eligible' for r in best.values())
    return {'window_days': WINDOW // 86400, 'web_captured_items': len(best),
            'eligible': reasons.pop('eligible', 0), 'local_proof_available': 0,
            'not_eligible': dict(sorted(reasons.items()))}


def next_candidate(s, now=None, pid=None):
    now = time.time() if now is None else now
    allowed = {}
    for page in range(MAX_SCAN_PAGES):  # SQL already excludes almost everything; Python re-checks
        rows = _rows(s, now, pid, limit=SCAN_LIMIT, offset=page * SCAN_LIMIT, eligible=True)
        for row in rows:
            if _reason(s, row, now, allowed) == '':
                return row[0], row[4]
        if len(rows) < SCAN_LIMIT:
            break
    return None


# ---------- probe

def claim(iid, now=None) -> bool:
    """Mark one item as being checked; False if someone else already decided it."""
    now = time.time() if now is None else now
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        item = s.get(ContentItem, iid)
        if not item or item.type != 'web':
            return False
        probe = (item.meta or {}).get('article_probe')
        if not _open(probe, now):
            return False
        attempts = int(probe.get('attempts') or 0) if isinstance(probe, dict) else 0
        item.meta = {**(item.meta or {}), 'article_probe': {'at': now, 'by': 'upgrade', 'result': 'running',
                                                            'attempts': attempts}}
        s.add(item); s.commit()
    return True


def _finish(iid, result, transient=False):
    """Record a final result, or a bounded retry for a transient fetch failure."""
    now = time.time()
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        item = s.get(ContentItem, iid)
        probe = (item.meta or {}).get('article_probe') if item else None
        if item and isinstance(probe, dict) and probe.get('result') == 'running':
            marker = {'at': now, 'by': 'upgrade', 'result': result}
            if transient:
                attempts = int(probe.get('attempts') or 0) + 1
                marker['attempts'] = attempts
                if attempts < MAX_ATTEMPTS:
                    result = marker['result'] = 'retry'
                    marker['next_at'] = now + RETRY_AFTER
            item.meta = {**item.meta, 'article_probe': marker}
            s.add(item)
        s.commit()
    return result


def _transient(exc) -> bool:
    status = getattr(exc, 'status', None)
    if isinstance(exc, curation.FetchError):
        return status in (408, 425, 429) or (isinstance(status, int) and status >= 500)
    return isinstance(exc, (OSError, http.client.HTTPException))  # timeouts, resets, DNS


def _same_page(a, b):
    pa, pb = urlsplit(a or ''), urlsplit(b or '')
    host = lambda p: (p.hostname or '').lower().removeprefix('www.')  # noqa: E731
    return host(pa) == host(pb) and (pa.path.rstrip('/') or '/') == (pb.path.rstrip('/') or '/')


def probe(iid) -> str:
    """Re-read one claimed page and apply capture's identity rule. Returns the recorded result."""
    from . import capture
    from .engine import index
    with session() as s:
        item = s.get(ContentItem, iid)
        if not item or item.type != 'web':
            return _finish(iid, 'changed')
        url = item.url
    try:
        data, final, _ = curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000, with_metadata=True)
        if not _same_page(url, final):
            return _finish(iid, 'redirected')  # a moved/expired page may land on an unrelated article
        page = capture.extract_page(data, final)
    except Exception as exc:  # noqa: BLE001 — leave the item as it was
        return _finish(iid, 'unreachable', transient=_transient(exc))
    if not isinstance(page, dict) or not (page.get('body') or '').strip():
        return _finish(iid, 'no_text')
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        item = s.get(ContentItem, iid)
        probe_meta = (item.meta or {}).get('article_probe') if item else None
        if not item or item.type != 'web' or not isinstance(probe_meta, dict) or probe_meta.get('result') != 'running':
            s.commit()
            return 'changed'
        identity = capture._captured_page_identity('web', '', '', None, page, final)
        promoted = capture.merge_page_identity(s, item, identity, source='upgrade')
        s.flush(); index(s, item)
        pids = capture.promotion_profiles(s, iid) if promoted else []
        result = item.meta['article_probe']['result']
        s.commit()
    if promoted:
        capture.queue_master(pids, iid)
    return result


def _claim_due(key, field, now, interval) -> bool:
    """Cross-process CAS lease. Reads first: a tick that is not due writes nothing."""
    with session() as s:
        row = s.get(Setting, key)
        if row is None:
            s.exec(insert(Setting).values(key=key, value={}).on_conflict_do_nothing(index_elements=['key']))
            s.commit()
            row = s.get(Setting, key)
        old = row.value or {}
        if old.get(field, 0) > now:
            return False
        done = s.exec(update(Setting).where(Setting.key == key, Setting.value == old)
                      .values(value={**old, field: now + interval}))
        s.commit()
        return done.rowcount == 1


def _claim_gap(now) -> bool:
    """At most one probe starts per PROBE_GAP across processes."""
    return _claim_due(PROBE_KEY, 'next_at', now, PROBE_GAP)


def probe_tick(now=None) -> bool:
    """Start one probe on the shared capture lane when capture is idle. True if one started."""
    now = time.time() if now is None else now
    try:
        if not _claim_gap(now):
            return False
        with session() as s:
            found = next_candidate(s, now)
        if not found or not claim(found[0], now):
            return False
        iid, pid = found
        if slots.spawn_lane('local-capture', 'capture', iid, pid, 'Article identity check', probe, iid):
            return True
        with session() as s:  # lane busy: release the claim for a later pass
            item = s.get(ContentItem, iid)
            marker = (item.meta or {}).get('article_probe') if item else None
            if isinstance(marker, dict) and marker.get('result') == 'running':
                rest = {k: v for k, v in item.meta.items() if k != 'article_probe'}
                if marker.get('attempts'):  # keep the retry count of an earlier transient failure
                    rest['article_probe'] = {**marker, 'result': 'retry', 'next_at': now}
                item.meta = rest
                s.add(item); s.commit()
    except Exception:  # noqa: BLE001 — never stops the capture scheduler
        log.exception('article identity probe failed')
    return False


def run_batch(limit, pid=None, gap=2.0) -> Counter:
    """Synchronous bounded pass for the CLI: no lane, but still a pause between page reads."""
    results = Counter()
    for n in range(max(0, limit)):
        with session() as s:
            found = next_candidate(s, pid=pid)
        if not found or not claim(found[0]):
            break
        if n and gap > 0:
            time.sleep(gap)
        results[probe(found[0])] += 1
    return results


# ---------- coverage audit

def _article_source(src: Source) -> bool:
    prov = PROVIDERS.get(src.provider)
    item_type = (src.config or {}).get('item_type')
    return bool(prov) and (item_type in ARTICLE_TYPES or (not item_type and prov.default_type in ARTICLE_TYPES))


def audit(s, pid, now=None) -> list[dict]:
    """News-interested watch curators of one profile with (almost) no articles in 7 days, with why."""
    now = time.time() if now is None else now
    out = []
    curators = s.exec(select(Curator).where(Curator.profile_id == pid, Curator.kind == 'watch',
                                             Curator.enabled.is_(True)).order_by(Curator.id)).all()
    for c in curators:
        if c.content_types and not set(c.content_types) & set(ARTICLE_TYPES):
            continue
        counts = dict(s.exec(select(ContentItem.type, func.count(func.distinct(ContentItem.id)))
                             .join(CuratorDiscovery, CuratorDiscovery.item_id == ContentItem.id)
                             .where(CuratorDiscovery.curator_id == c.id, CuratorDiscovery.profile_id == pid,
                                    CuratorDiscovery.found_at >= now - WINDOW)
                             .group_by(ContentItem.type)).all())
        articles = sum(counts.get(t, 0) for t in ARTICLE_TYPES)
        if articles >= LOW_YIELD:
            continue
        web = counts.get('web', 0)
        sources = s.exec(select(Source).join(CuratorSource, CuratorSource.source_id == Source.id)
                         .where(CuratorSource.curator_id == c.id, Source.profile_id == pid,
                                Source.enabled.is_(True))).all()
        article_sources = sum(_article_source(src) for src in sources)
        if not article_sources:
            reason = f'No article-type sources; {web} web findings in 7 days.'
        else:
            reason = f'{article_sources} article-type source(s) found no articles in 7 days; {web} web findings.'
        out.append({'id': c.id, 'name': c.name, 'articles_7d': articles, 'web_7d': web,
                    'article_sources': article_sources, 'sources': len(sources), 'reason': reason})
    return out


def _claim_audit(now) -> bool:
    return _claim_due(AUDIT_KEY, 'next_check_at', now, AUDIT_INTERVAL)


def audit_tick(now=None) -> bool:
    """Refresh every hub profile's coverage reasons at most every AUDIT_INTERVAL. Local reads only."""
    now = time.time() if now is None else now
    try:
        if not _claim_audit(now):
            return False
        profiles = {}
        with session() as s:
            pids = s.exec(select(Curator.profile_id).where(Curator.enabled.is_(True)).distinct()).all()
            for pid in pids:
                if hub_allowed(s, pid):
                    profiles[str(pid)] = {'checked_at': now, 'curators': audit(s, pid, now)}
            state = get_kv(AUDIT_KEY, {}, s=s)
            save_setting(AUDIT_KEY, {**state, 'profiles': profiles}, s=s)
        return True
    except Exception:  # noqa: BLE001 — never stops the capture scheduler
        log.exception('article coverage audit failed')
        return False


def coverage(s, pid) -> dict:
    """This profile's last audit only, limited to curators it still owns and runs. For shelf/API use."""
    own = get_kv(AUDIT_KEY, {}, s=s).get('profiles', {}).get(str(pid), {})
    if not hub_allowed(s, pid):
        return {'checked_at': None, 'curators': []}
    rows = []
    for row in own.get('curators', []):
        c = s.get(Curator, row.get('id'))
        if c and c.profile_id == pid and c.enabled and c.kind == 'watch':
            rows.append({**row, 'name': c.name})
    return {'checked_at': own.get('checked_at'), 'curators': rows}
