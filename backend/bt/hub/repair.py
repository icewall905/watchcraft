"""Source repair: revalidate, look for alternatives, propose and test watcher rules, apply with rollback.

Nothing here reports a watcher as working because it was saved: every applied rule set comes with the
result of an actual extraction, and the previous rules stay available as a revision.
"""
import logging
import time
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlmodel import select

from .. import curation
from ..access import hub_allowed
from ..db import session
from . import watchers
from .models import Source, SourceCheck, WatcherRevision
from .providers import PROVIDERS

AUTO_REPAIR_EVERY = 86400
REVISIONS_KEPT = 20
log = logging.getLogger(__name__)


def recent_checks(s, src: Source, n: int = 20) -> list[SourceCheck]:
    return s.exec(select(SourceCheck).where(SourceCheck.source_id == src.id, SourceCheck.profile_id == src.profile_id)
                  .order_by(SourceCheck.id.desc()).limit(n)).all()


def source_health(s, src: Source) -> dict:
    return watchers.health(recent_checks(s, src), src.enabled, src.provider)


def test(src: Source, config: dict | None) -> dict:
    """Run the provider with these rules against the live source, bypassing the shared cache."""
    from .engine import _safe_error
    prov = PROVIDERS[src.provider]
    ok, reason = prov.availability()
    if not ok:
        return {'ok': False, 'count': 0, 'sample': [], 'error': reason}
    try:
        items = prov.run(src.url, 'open source' if prov.mode == 'query' else '', config)
    except Exception as e:  # noqa: BLE001 — reported to the user as a failed test
        return {'ok': False, 'count': 0, 'sample': [], 'error': _safe_error(e)}
    return {'ok': bool(items), 'count': len(items), 'sample': watchers.preview(items),
            'error': '' if items else 'The rules extracted nothing.'}


def save(src: Source, config: dict, reason: str, result: dict) -> Source | None:
    """Apply tested rules, keeping the previous ones as a revision for rollback."""
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        row = s.get(Source, src.id)
        if not row or row.profile_id != src.profile_id or not hub_allowed(s, src.profile_id):
            return None
        has_history = s.exec(select(WatcherRevision.id).where(WatcherRevision.source_id == row.id).limit(1)).first()
        if not has_history:
            # Record the rules in service before the first change so they can be restored.
            s.add(WatcherRevision(source_id=row.id, profile_id=row.profile_id, config=row.config or {}, reason='original'))
        s.add(WatcherRevision(source_id=row.id, profile_id=row.profile_id, config=config, reason=reason,
                              item_count=result.get('count', 0), sample=result.get('sample', [])[:5]))
        if (row.config or {}) != config:
            row.rules_changed_at = time.time()
        row.config = config
        s.add(row); s.flush()
        for old in s.exec(select(WatcherRevision).where(WatcherRevision.source_id == row.id)
                          .order_by(WatcherRevision.id.desc()).offset(REVISIONS_KEPT)).all():
            s.delete(old)
        s.commit(); s.refresh(row)
    return row


def _alternates(src: Source) -> list[dict]:
    """Other validated ways to follow the same site (feeds first), found by Source Scout's inspection."""
    from . import scout
    p = urlsplit(src.url)
    site = src.url if src.provider != 'sitemap' else f'{p.scheme}://{p.netloc}/'
    found, _ = scout.inspect_site(site, lambda *_: None)
    order = {'rss': 0, 'sitemap': 1, 'html_listing': 2, 'page_watch': 3}
    return sorted([f for f in found if (f['provider'], f['url']) != (src.provider, src.url)],
                  key=lambda f: order.get(f['provider'], 9))[:5]


def propose(src: Source) -> dict:
    """Diagnose a source and propose tested replacement rules. Applies nothing."""
    steps, proposals, alternates = [], [], []
    current_rules = src.config or {}
    current = test(src, current_rules)
    steps.append({'step': 'revalidate', 'ok': current['ok'],
                  'detail': f"Current rules extracted {current['count']} items." if current['ok'] else current['error']})
    with session() as s:
        health = source_health(s, src)
    typical = health['typical_count']
    previous = {x['url'] for x in current['sample']}
    try:
        alternates = _alternates(src)
        steps.append({'step': 'alternatives', 'ok': True,
                      'detail': f'Found {len(alternates)} other validated way(s) to follow this site.'})
    except Exception as e:  # noqa: BLE001
        from .engine import _safe_error
        steps.append({'step': 'alternatives', 'ok': False, 'detail': _safe_error(e)})
    candidates = []
    if src.provider == 'html_listing':
        try:
            data, final, _ = curation.fetch(src.url, kinds=curation.HTML_TYPES, limit=3_000_000, with_metadata=True)
            candidates = [p['config'] for p in watchers.suggest_listing_rules(data, final, current_rules)]
            steps.append({'step': 'reinspect', 'ok': True, 'detail': f'Inspected the page structure; {len(candidates)} rule set(s) to test.'})
        except Exception as e:  # noqa: BLE001
            from .engine import _safe_error
            steps.append({'step': 'reinspect', 'ok': False, 'detail': _safe_error(e)})
    elif src.provider == 'page_watch' and current_rules.get('content_selector') and not current['ok']:
        candidates = [{k: v for k, v in current_rules.items() if k != 'content_selector'}]
        steps.append({'step': 'reinspect', 'ok': True, 'detail': 'The content area is gone; testing the whole page instead.'})
    for config in candidates:
        if config == current_rules:
            continue
        result = test(src, config)  # an actual extraction, not just the inspection's estimate
        if not result['ok']:
            continue
        overlap = (len(previous & {x['url'] for x in result['sample']}) / len(previous)) if previous else None
        proposals.append({'config': config, **result,
                          'comparison': {'typical_count': typical, 'current_count': current['count'],
                                         'overlap': None if overlap is None else round(overlap, 2)}})
    # Keep the inspector's ranking: more links is not better when they are site navigation.
    steps.append({'step': 'test', 'ok': bool(proposals),
                  'detail': f'{len(proposals)} proposal(s) passed a live extraction test.' if candidates
                  else 'No alternative rules apply to this source type.'})
    return {'health': health, 'current': {'config': current_rules, **current}, 'proposals': proposals[:3],
            'alternates': alternates, 'steps': steps}


def safe(proposal: dict, provider: str) -> bool:
    """Policy for unattended repair: a real, comparable number of items (a watched page is one item)."""
    if provider == 'page_watch':
        return proposal['count'] == 1
    typical = proposal['comparison']['typical_count']
    return proposal['count'] >= 3 and (not typical or proposal['count'] >= 0.5 * typical)


def auto(src: Source) -> bool:
    """Repair a broken source whose owner allowed it, at most once a day."""
    from .engine import record_source_check
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))  # two curators sharing the source must not both repair it
        row = s.get(Source, src.id)
        if not row or not (row.config or {}).get('auto_repair') or not hub_allowed(s, row.profile_id):
            return False
        if source_health(s, row)['status'] != 'broken':
            return False
        if row.last_repair and time.time() - row.last_repair < AUTO_REPAIR_EVERY:
            return False
        row.last_repair = time.time()
        s.add(row); s.commit(); s.refresh(row)
        s.expunge(row)
    try:
        plan = propose(row)
    except Exception:  # noqa: BLE001 — repair is best effort; the source stays as it was
        log.exception('automatic repair of source %s failed', row.id)
        return False
    best = next((p for p in plan['proposals'] if safe(p, row.provider)), None)
    if not best:
        return False
    if save(row, best['config'], 'auto-repair', best) is None:
        return False
    record_source_check(row, 'repair', best['count'])
    return True
