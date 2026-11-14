#!/usr/bin/env python3
"""Re-check historical `web` captures for verified article identity (bounded, idempotent).

Default is a dry run: aggregate counts only (no titles, URLs or article text), no network.
--apply re-reads at most --limit candidate pages with the same rule capture uses and prints
result counts. Items with user/rule decisions, social posts, documents or a more specific type
are never changed. Run inside the service, e.g.:

    python upgrade_captured_articles.py --data-dir /data            # dry run
    python upgrade_captured_articles.py --data-dir /data --apply --limit 20
"""
import argparse
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--data-dir', help='BT_DATA_DIR holding bt.sqlite3 (default: environment)')
    ap.add_argument('--profile', type=int, help='only items discovered by this profile id')
    ap.add_argument('--apply', action='store_true', help='re-read pages (network) and upgrade verified articles')
    ap.add_argument('--limit', type=int, default=20, help='max pages re-read with --apply (default 20)')
    args = ap.parse_args(argv)
    if args.data_dir:
        os.environ['BT_DATA_DIR'] = args.data_dir
    backend = Path(__file__).resolve().parent.parent / 'backend'
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from bt.db import session
    from bt.hub import article_upgrade

    with session() as s:
        out = {'mode': 'apply' if args.apply else 'dry-run', 'before': article_upgrade.stats(s, pid=args.profile)}
    if args.apply:
        out['results'] = dict(article_upgrade.run_batch(min(max(args.limit, 0), 500), pid=args.profile))
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
