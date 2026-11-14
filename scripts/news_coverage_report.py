#!/usr/bin/env python3
"""Read-only news coverage and preparation diagnostic (no application imports).

Run inside the service: python news_coverage_report.py --database /data/bt.sqlite3
Optional --profile limits inspection; --include-config includes curator interests
and sanitized attached source URLs (never free-form instructions). Default output
contains aggregates, numeric IDs and collector health/settings, never article text,
profile names, free-form configuration or credentials. No network/model calls.
Counts are current state within publication windows, not historical stage events.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

CLEANUP_VERSION = 1  # bt.hub.article_cleanup.VERSION; checked by regression test
READY_TARGET = 100
CANDIDATE_TARGET = 300
ARTICLE_TYPES = ('news', 'article')
WHITESPACE = ' \t\r\n\v\f\u00a0'


def unpack(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def metrics(rows, now, days):
    """Distinct item counts, with disjoint approved/unread preparation blockers."""
    cutoff = now - days * 86400
    recent = [r for r in rows if cutoff <= r['effective_at'] <= now]
    counts = Counter({k: 0 for k in ('discovered','visible','hidden','approved','approved_unread','consumed_or_archived','ready','pending_review','keep','feature','set_aside','reject','known_publication_date','unknown_publication_date')})
    counts['discovered'] = len(recent)
    publishers, stories = set(), set()
    pending_since = []
    capture_states, summary_states, languages = Counter(), Counter(), Counter()
    blockers, samples = Counter(), defaultdict(list)
    for r in recent:
        iid = r['id']
        counts['known_publication_date' if r['published_at'] is not None else 'unknown_publication_date'] += 1
        if r['muted'] or r['dismissed']:
            counts['hidden'] += 1
            continue
        counts['visible'] += 1
        tier = r['tier'] or 'pending_review'
        counts[tier] += 1
        if tier not in ('keep', 'feature'):
            continue
        counts['approved'] += 1
        if r['read_at'] is not None or r['archived']:
            counts['consumed_or_archived'] += 1
            continue
        counts['approved_unread'] += 1
        capture_states[r['capture_status'] or 'missing'] += 1
        summary_states[r['summary_status'] or 'missing'] += 1
        if not r['has_body'] or not r['has_raw']:
            blocker = 'missing_local_text'
        elif r['cleanup_status'] != 'reviewed' or r['cleanup_version'] != CLEANUP_VERSION:
            blocker = 'cleanup_not_current'
        elif r['capture_status'] not in ('complete', 'partial'):
            blocker = 'capture_not_complete'
        elif r['summary_status'] != 'ready' or not r['has_summary']:
            blocker = 'summary_not_ready'
        else:
            blocker = 'ready'
        blockers[blocker] += 1
        if len(samples[blocker]) < 3:
            samples[blocker].append(iid)
        if blocker == 'ready':
            counts['ready'] += 1
            if r['publisher'].strip():
                publishers.add(r['publisher'].strip().casefold())
            stories.add(r['story_id'] if r['story_id'] is not None else f'item:{iid}')
            language = r['language'] or 'unknown'
            languages[language] += 1
        else:
            pending_since.append(r['found_at'])
    newly_found = [r for r in rows if cutoff <= r['found_at'] <= now]
    # found_at is profile/curator discovery, not global ContentItem.first_seen.
    return {'counts': dict(counts), 'preparation': dict(blockers),
            'sample_item_ids': dict(samples), 'capture_states': dict(capture_states),
            'summary_states': dict(summary_states), 'ready_publishers': len(publishers),
            'ready_stories': len(stories), 'ready_languages': dict(languages),
            'newly_discovered': len(newly_found),
            'newly_discovered_older_articles': sum(r['effective_at'] < cutoff for r in newly_found),
            'future_dated': sum(r['effective_at'] > now for r in rows),
            'oldest_unprepared_discovery_age_hours': round(max(0, now - min(pending_since)) / 3600, 1) if pending_since else None}


def combine(rows):
    """A profile has one item even when several curators discovered it."""
    out = {}
    for row in rows:
        prior = out.get(row['id'])
        if prior is None or row['found_at'] < prior['found_at']:
            out[row['id']] = row
    return list(out.values())


def report(database, profile=None, now=None, limit=50000, include_config=False):
    if not 1 <= limit <= 200000:
        raise ValueError('limit must be between 1 and 200000 discovery links per profile')
    now = time.time() if now is None else now
    uri = Path(database).resolve(strict=True).as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        # Pin all tables/counts to one snapshot while workers continue running.
        db.execute('BEGIN')
        curators = [dict(r) for r in db.execute('SELECT * FROM curator' + (' WHERE profile_id=?' if profile is not None else '') + ' ORDER BY profile_id,id', (() if profile is None else (profile,)))]
        profiles = sorted({r['profile_id'] for r in curators})
        result = {'as_of': now, 'cleanup_version': CLEANUP_VERSION, 'profiles': [],
                  'notes': ['Windows use publication time, falling back to global first_seen only when publication is unknown.',
                            'Current-state funnel of persisted news/article discoveries; screened-out candidates and historical stage timings are not stored here.',
                            'Per-curator and source/provider/publisher breakdowns overlap; profile totals deduplicate item IDs.',
                            'Language metadata may be unknown; this report does not infer language from headlines.',
                            'Ready matches text/cleanup/capture/summary gates, not human acceptance of extraction or relevance.',
                            'Run totals include all curator content types and repeated source results; duration is wall time, not model time.',
                            'No model token or cost totals: existing discovery records do not store article-level usage.',
                            'Provider breakdown uses canonical item provenance; source breakdown uses this curator discovery.',
                            'Publisher diversity normalizes case/whitespace only; it does not establish independent ownership.']}
        for pid in profiles:
            own = [c for c in curators if c['profile_id'] == pid]
            muted = [str(k).lower() for t in db.execute("SELECT name,keywords FROM topic WHERE profile_id=? AND kind='news' AND muted=1", (pid,)) for k in (unpack(t['keywords'], []) or [t['name']])]
            rows = [dict(r) for r in db.execute('''SELECT d.curator_id,d.source_id,d.found_at,i.id,i.title,i.summary,i.publisher,i.provider,
                i.published_at,coalesce(i.published_at,i.first_seen) effective_at,
                coalesce(length(trim(i.body,?)),0)>0 has_body,
                coalesce(length(trim(a.raw_body,?)),0)>0 has_raw,
                CASE WHEN json_valid(i.enrichment) THEN coalesce(length(trim(CAST(json_extract(i.enrichment,'$.summary') AS TEXT),?)),0)>0 ELSE 0 END has_summary,
                CASE WHEN json_valid(i.meta) THEN json_extract(i.meta,'$.language') ELSE NULL END language,
                a.status cleanup_status,a.version cleanup_version,v.tier,st.read_at,st.archived,st.dismissed,
                j.status capture_status,j.summary_status,si.story_id
                FROM curatordiscovery d JOIN curator c ON c.id=d.curator_id AND c.profile_id=d.profile_id
                JOIN contentitem i ON i.id=d.item_id
                LEFT JOIN articletext a ON a.item_id=i.id
                LEFT JOIN itemverdict v ON v.item_id=i.id AND v.profile_id=d.profile_id
                LEFT JOIN itemstate st ON st.item_id=i.id AND st.profile_id=d.profile_id
                LEFT JOIN capturejob j ON j.item_id=i.id AND j.profile_id=d.profile_id
                LEFT JOIN storyitem si ON si.item_id=i.id
                WHERE d.profile_id=? AND i.type IN ('news','article')
                AND (coalesce(i.published_at,i.first_seen)>=? OR d.found_at>=?)
                ORDER BY d.found_at DESC,d.id DESC LIMIT ?''',
                (WHITESPACE, WHITESPACE, WHITESPACE, pid, now-7*86400, now-7*86400, limit+1))]
            truncated = len(rows) > limit
            rows = rows[:limit]
            for r in rows:
                # Only metadata used to apply the existing News mute policy; never emitted.
                hay = f"{r.pop('title') or ''} {r.pop('summary') or ''}".lower()
                r['muted'] = any(term in hay for term in muted)
                r['publisher'] = r['publisher'] or ''
            all_items = combine(rows)
            entry = {'profile_id': pid, 'discovery_links_scanned': len(rows), 'truncated': truncated,
                     'counts_are_lower_bounds': truncated, 'windows': {str(d): metrics(all_items, now, d) for d in (1,7)},
                     'curators': []}
            approved = entry['windows']['7']['counts']['approved']
            ready = entry['windows']['7']['counts']['ready']
            def reached(count, target):
                return True if count >= target else None if truncated else False
            ready_met = reached(ready, READY_TARGET)
            candidate_met = reached(approved, CANDIDATE_TARGET)
            entry['reference_supply'] = {
                'ready_target': READY_TARGET, 'candidate_target': CANDIDATE_TARGET,
                'ready_shortfall_upper_bound': max(0, READY_TARGET - ready),
                'candidate_shortfall_upper_bound': max(0, CANDIDATE_TARGET - approved),
                'ready_met': ready_met, 'candidate_buffer_met': candidate_met,
                'met': ready_met and candidate_met if not truncated or (ready_met and candidate_met) else None,
                'basis': 'Diagnostic scan, not the application news_supply 1200-row capped universe.'}
            sources = {r['id']: dict(r) for r in db.execute('SELECT * FROM source WHERE profile_id=?', (pid,))}
            for c in own:
                subset = combine([r for r in rows if r['curator_id'] == c['id']])
                types = unpack(c['content_types'], [])
                attached = [sources[r[0]] for r in db.execute('SELECT source_id FROM curatorsource WHERE curator_id=?', (c['id'],)) if r[0] in sources]
                if not subset and types and not set(types).intersection(ARTICLE_TYPES):
                    continue
                type_counts = {r['type']: r['n'] for r in db.execute('''SELECT i.type,count(DISTINCT i.id) n
                    FROM curatordiscovery d JOIN contentitem i ON i.id=d.item_id
                    WHERE d.profile_id=? AND d.curator_id=? AND (coalesce(i.published_at,i.first_seen)>=? OR d.found_at>=?)
                    GROUP BY i.type''', (pid,c['id'],now-7*86400,now-7*86400))}
                out = {'id': c['id'], 'recent_discovery_types': type_counts, 'enabled': bool(c['enabled']), 'kind': c['kind'],
                       'interval_hours': c['interval_hours'], 'last_success': c['last_success'],
                       'last_status': c['last_status'], 'ai_enabled': bool(c['ai_enabled']),
                       'min_score': c['min_score'], 'max_per_run': c['max_per_run'],
                       'sources': [{'id': s['id'], 'provider': s['provider'], 'enabled': bool(s['enabled']),
                                    'last_success': s['last_success'], 'has_error': bool(s['last_error']),
                                    'last_checked': s['last_checked'], 'item_count': s['item_count']} for s in attached],
                       'windows': {str(d): metrics(subset, now, d) for d in (1,7)}}
                out['runs'] = {}
                for days in (1, 7):
                    run = db.execute('''SELECT count(*) n,coalesce(sum(new_items),0) new_items,
                        coalesce(sum(seen_items),0) source_results,
                        sum(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
                        sum(CASE WHEN status='partial' THEN 1 ELSE 0 END) partial,
                        avg(CASE WHEN finished_at>=started_at THEN finished_at-started_at END) avg_wall_seconds
                        FROM curatorrun WHERE profile_id=? AND curator_id=? AND started_at BETWEEN ? AND ?''',
                        (pid,c['id'],now-days*86400,now)).fetchone()
                    out['runs'][str(days)] = dict(run)
                if include_config:
                    out['configuration'] = {k: unpack(c[k], []) if k in ('queries','keywords','exclusions','content_types','aliases') else c[k]
                                            for k in ('name','subject','queries','keywords','exclusions','content_types','aliases')}
                    for source_out, source in zip(out['sources'], attached):
                        # Credentials embedded in URLs should never appear in reports.
                        from urllib.parse import urlsplit, urlunsplit
                        parsed = urlsplit(source['url'])
                        source_out['url'] = urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', ''))
                entry['curators'].append(out)
            for dimension in ('source_id', 'provider', 'publisher'):
                buckets = defaultdict(list)
                for r in rows:
                    buckets[r[dimension] if r[dimension] is not None else 'unknown'].append(r)
                entry[dimension + '_breakdown'] = {str(k): {str(d): metrics(combine(rs),now,d) for d in (1,7)} for k,rs in buckets.items()}
            result['profiles'].append(entry)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--profile', type=int)
    parser.add_argument('--now', type=float, help='Unix timestamp for reproducible reports')
    parser.add_argument('--limit', type=int, default=50000)
    parser.add_argument('--include-config', action='store_true')
    args = parser.parse_args()
    print(json.dumps(report(args.database, args.profile, args.now, args.limit, args.include_config), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
