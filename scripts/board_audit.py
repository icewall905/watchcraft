#!/usr/bin/env python3
"""Mood Board curation audit: how often the owner keeps what the curator adds, by board, day, source host and reason.

Read-only. Point it at a copy of bt.sqlite3 (or the live file; it opens read-only):
    python3 scripts/board_audit.py data/bt.sqlite3 [--board 2] [--days 14]
"""
import argparse
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import urlsplit

sys.path.insert(0, __import__('os').path.join(__import__('os').path.dirname(__file__), '..', 'backend'))
try:
    from bt.hub.board_quality import REASONS, domain, reasons_in  # same grouping as the curator's lessons
except Exception:  # pragma: no cover - runs without the backend's dependencies
    REASONS, domain = [], (lambda h: '.'.join(h.split('.')[-2:]))
    reasons_in = lambda note: [w for w in ('costume', 'boring', 'teen', 'stock', 'ai') if w in (note or '').lower()]  # noqa: E731


def host(url):
    h = (urlsplit(url or '').hostname or '').lower()
    return domain(h[4:] if h.startswith('www.') else h) if h else '?'


def pct(good, total):
    return f'{100 * good / total:5.1f}%' if total else '    -'


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('db')
    ap.add_argument('--board', type=int)
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()
    db = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    since = time.time() - args.days * 86400
    boards = db.execute('SELECT id, name FROM moodboard' + (' WHERE id = ?' if args.board else ''),
                        (args.board,) if args.board else ()).fetchall()
    for bid, name in boards:
        rows = db.execute('SELECT p.source_url, p.created_at, f.verdict, f.note FROM moodpin p '
                          'LEFT JOIN moodfeedback f ON f.pin_id = p.id WHERE p.board_id = ? AND p.created_at > ?',
                          (bid, since)).fetchall()
        if not rows:
            continue
        reviewed = [r for r in rows if r[2]]
        good = sum(r[2] == 'good' for r in reviewed)
        print(f'\n=== Board {bid} {name}: {len(rows)} pins, {len(reviewed)} reviewed, approval {pct(good, len(reviewed))}')
        by_day = defaultdict(lambda: [0, 0])
        by_host = defaultdict(lambda: [0, 0, 0])
        for url, created, verdict, _note in rows:
            day = time.strftime('%Y-%m-%d', time.localtime(created))
            h = by_host[host(url)]
            h[2] += 1
            if verdict:
                by_day[day][0 if verdict == 'good' else 1] += 1
                h[0 if verdict == 'good' else 1] += 1
        print('  Approval by day pins were added:')
        for day, (g, b) in sorted(by_day.items()):
            print(f'    {day}  {g:4d} good {b:4d} bad  {pct(g, g + b)}')
        print('  By source host (pins, good, bad, approval):')
        for h, (g, b, n) in sorted(by_host.items(), key=lambda kv: -kv[1][2])[:15]:
            print(f'    {h:28s} {n:5d} {g:5d} {b:5d}  {pct(g, g + b)}')
        reasons = Counter(k for r in reviewed if r[2] == 'bad' for k in (reasons_in(r[3]) or ['(no note)']))
        print('  Reject reasons:', ', '.join(f'{k} {n}' for k, n in reasons.most_common()))
        try:
            v = db.execute('SELECT COUNT(*), SUM(fits), AVG(confidence) FROM moodverdict WHERE board_id = ? AND created_at > ?',
                           (bid, since)).fetchone()
            if v[0]:
                print(f'  Vision verdicts: {v[0]} judged, {v[1]} kept ({pct(v[1], v[0])}), mean confidence {v[2]:.0f}')
        except sqlite3.OperationalError:
            pass  # database from before verdicts were stored


if __name__ == '__main__':
    main()
