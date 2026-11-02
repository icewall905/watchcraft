#!/usr/bin/env python3
"""Read-only aggregate report of curation outcomes and local-download readiness.

Example: python scripts/curation_lifecycle_report.py --database /data/bt.sqlite3 \
    --since 2026-11-01T00:00:00Z --media /media

An accepted item is not proof of human usefulness. A recorded download is not
proof that it plays; --media also verifies the recorded path exists on disk.
The report emits no profile names, playlist names, video IDs or titles.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3


def parse_time(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp must include a timezone')
    return dt.astimezone(timezone.utc)


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0, 'p50_seconds': None, 'p95_seconds': None}
    return {'n': len(values), 'p50_seconds': round(values[math.ceil(len(values) * .50) - 1], 3),
            'p95_seconds': round(values[math.ceil(len(values) * .95) - 1], 3)}


def report(database, since, media=None):
    database = Path(database).resolve(strict=True)
    media = Path(media).resolve(strict=True) if media else None
    uri = database.as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        rows = db.execute('''SELECT cat.kind, c.status, c.score, c.red_flags,
             (julianday(c.judged_at) - julianday(c.created_at)) * 86400,
             d.status, d.path, (julianday(d.finished_at) - julianday(c.judged_at)) * 86400
          FROM candidate c JOIN category cat ON cat.id = c.category_id
          LEFT JOIN download d ON d.video_id = c.video_id
          WHERE c.judged_at IS NOT NULL AND julianday(c.judged_at) >= julianday(?)
                AND c.status IN ('accepted', 'rejected')''', (since.isoformat(),))
        buckets = defaultdict(lambda: {'counts': defaultdict(int), 'decision_seconds': defaultdict(list), 'ready_seconds': []})
        for kind, status, score, flags, decision, download_status, path, ready in rows:
            bucket = buckets[kind or 'unknown']
            counts = bucket['counts']
            try:
                flag = (json.loads(flags or '[]') or [''])[0]
            except (ValueError, TypeError, IndexError, KeyError):
                flag = ''
            stage = ('full_judgment' if score is not None else
                     'title_screen' if str(flag).startswith('off-topic:') else
                     'prefilter' if str(flag).startswith('prefilter:') else 'other')
            counts[f'{stage}_{status}'] += 1
            if decision is not None and decision >= 0:
                bucket['decision_seconds'][stage].append(decision)
            if status != 'accepted' or download_status != 'done':
                continue
            counts['accepted_recorded_local'] += 1
            if media is not None:
                file = (media / (path or '')).resolve()
                if media not in file.parents or not file.is_file():
                    counts['accepted_missing_local_file'] += 1
                    continue
                counts['accepted_existing_local_file'] += 1
            if ready is not None and ready >= 0:
                bucket['ready_seconds'].append(ready)
            else:
                counts['local_before_acceptance_or_missing_time'] += 1
        return {'since_utc': since.isoformat(), 'local_files_checked': media is not None,
                'notes': ['Acceptance is a proxy, not human usefulness.',
                          'Durations can include queueing, pause and unrelated workload.',
                          'One downloaded video may satisfy multiple playlist candidates.',
                          'Existing file checks do not establish decodability.'],
                'playlist_kinds': {kind: {'counts': dict(value['counts']),
                    'found_to_decision_by_stage': {stage: distribution(durations)
                        for stage, durations in sorted(value['decision_seconds'].items())},
                    'accepted_to_recorded_local': distribution(value['ready_seconds'])}
                    for kind, value in sorted(buckets.items())}}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--since', required=True, help='ISO timestamp with timezone')
    parser.add_argument('--media', help='optional media directory for local file-existence checks')
    args = parser.parse_args()
    try:
        since = parse_time(args.since)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report(args.database, since, args.media), indent=2))


if __name__ == '__main__':
    main()
