#!/usr/bin/env python3
"""Replay Mood Board picture checks on pictures owners already reviewed (Fits / Not a fit).

Run inside the app image with BT_DATA_DIR pointing at a PRIVATE COPY of a database snapshot (never the live data
dir): it opens the copy, rates each reviewed picture with the preference rater (and optionally the older judge),
and writes raw results as JSON lines. `--analyze` then grid-searches score weights and keep thresholds.

Leave-one-out: a batch's own reviews are removed from the liked/disliked examples the rater sees.
Output identifies boards and pins by id only.

  python board_rating_replay.py --out /replay/out.jsonl [--boards 3,7] [--per-board 40] [--legacy]
  python board_rating_replay.py --analyze /replay/out.jsonl
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys
import time
import urllib.request


def queue_wait(metrics_url: str, wait: float = 900, poll: float = 15) -> None:
    """Give way to live work on a shared vLLM server: wait while other requests queue."""
    if not metrics_url:
        return
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(metrics_url, timeout=5) as r:
                waiting = sum(float(line.rsplit(' ', 1)[1]) for line in r.read().decode().splitlines()
                              if line.startswith('vllm:num_requests_waiting{'))
        except Exception:  # noqa: BLE001 — metrics unavailable: carry on slowly
            time.sleep(poll)
            return
        if waiting <= 0:
            return
        time.sleep(poll)


def replay(args) -> None:
    data_dir = os.environ.get('BT_DATA_DIR', '')
    if not data_dir or data_dir.rstrip('/') in ('/data', './data'):
        sys.exit('Refusing to run: set BT_DATA_DIR to a private snapshot copy, never the live data dir.')
    from sqlmodel import select
    from bt.db import init_db, session
    from bt.hub import board_brief, moodboards as mb
    from bt.hub.models import MoodAsset, MoodBoard, MoodFeedback, MoodPin, MoodPinNote

    init_db()
    rng = random.Random(7)
    wanted = {int(x) for x in args.boards.split(',')} if args.boards else None
    with session() as s:
        boards = [b for b in s.exec(select(MoodBoard).order_by(MoodBoard.id)).all() if wanted is None or b.id in wanted]
    out = open(args.out, 'a')
    for board in boards:
        with session() as s:
            rows = s.exec(select(MoodFeedback.pin_id, MoodFeedback.verdict).join(MoodAsset, MoodAsset.pin_id == MoodFeedback.pin_id)
                          .where(MoodFeedback.board_id == board.id)).all()
            good = [p for p, v in rows if v == 'good']
            bad = [p for p, v in rows if v == 'bad']
            if len(good) + len(bad) < args.min_reviews:
                continue
            half = args.per_board // 2
            pick = rng.sample(good, min(len(good), max(half, args.per_board - len(bad)))) + \
                rng.sample(bad, min(len(bad), max(half, args.per_board - len(good))))
            rng.shuffle(pick)
            verdict = dict(rows)
            profile = board_brief.taste_profile(s, board)
            reviews = board_brief._reviews(s, board.id, 200)
            strict = bool(board.child_friendly) or board.strictness != 'balanced'
            child = bool(board.child_friendly)
            ai_mode = board.ai_images if board.ai_images in mb.ai_detect.MODES else 'reject'
            context = board.name + ' — ' + board.interests
            instructions = (board.instructions + mb.board_rules(board) + mb.feedback_lessons(s, board.id)).strip()
            items = []
            for pid in pick:
                pin, asset = s.get(MoodPin, pid), s.get(MoodAsset, pid)
                if not pin or not asset:
                    continue
                items.append({'pin_id': pid, 'title': pin.title, 'description': '',
                              '_captured': (asset.data, asset.mime, pin.width, pin.height)})
        print(f'board {board.id}: {len(items)} pictures ({sum(verdict[i["pin_id"]] == "good" for i in items)} fits)', flush=True)
        for start in range(0, len(items), mb.JUDGE_BATCH):
            batch = items[start:start + mb.JUDGE_BATCH]
            ids = {i['pin_id'] for i in batch}
            own = dict(profile)
            own['liked_examples'] = [r['picture'][:180] for r in reviews if r['verdict'] == 'good' and r['picture']
                                     and r['pin_id'] not in ids][:board_brief.PROFILE_EXAMPLES]
            own['disliked_examples'] = [(r['picture'][:160] + (f' — owner: "{r["note"][:100]}"' if r['note'] else ''))
                                        for r in reviews if r['verdict'] == 'bad' and r['picture']
                                        and r['pin_id'] not in ids][:board_brief.PROFILE_EXAMPLES]
            queue_wait(args.metrics)
            t0 = time.time()
            descriptions = mb.describe_images(batch)
            try:
                ratings = mb.rate_images(batch, descriptions, own, strict, ai_mode, None)
            except Exception as exc:  # noqa: BLE001
                print(f'  rate failed: {type(exc).__name__}: {str(exc)[:120]}', flush=True)
                ratings = {}
            legacy = {}
            if args.legacy:
                try:
                    legacy = mb.judge_images(batch, descriptions, context, instructions, strict, ai_mode, None)
                except Exception as exc:  # noqa: BLE001
                    print(f'  judge failed: {type(exc).__name__}', flush=True)
            for i, item in enumerate(batch):
                r, j = ratings.get(i), legacy.get(i)
                out.write(json.dumps({
                    'board': board.id, 'pin': item['pin_id'], 'owner': verdict[item['pin_id']], 'strict': strict, 'child': child,
                    'rating': {k: r.get(k) for k in ('subject_fit', 'appeal', 'quality', 'more', 'less', 'hard_violations', 'why')} if r else None,
                    'legacy': {'keep_likelihood': j.get('keep_likelihood'), 'fits': j.get('fits')} if j else None,
                    'seconds': round(time.time() - t0, 1)}) + '\n')
            out.flush()
    out.close()


def analyze(path: str) -> None:
    sys.path.insert(0, os.environ.get('BT_BACKEND', '.'))
    from bt.hub import moodboards as mb
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rated = [r for r in rows if r['rating']]
    print(f'{len(rows)} pictures, {len(rated)} rated; fits {sum(r["owner"] == "good" for r in rated)}, '
          f'not a fit {sum(r["owner"] == "bad" for r in rated)}')

    def report(name, keep):
        good = [r for r in rated if r['owner'] == 'good']
        bad = [r for r in rated if r['owner'] == 'bad']
        kg, db = sum(keep(r) for r in good), sum(not keep(r) for r in bad)
        return kg / max(1, len(good)), db / max(1, len(bad)), f'{name}: kept {kg}/{len(good)} fits, dropped {db}/{len(bad)} not-a-fit'

    legacy = [r for r in rated if r['legacy'] and type(r['legacy']['keep_likelihood']) is int]
    if legacy:
        for floor in (10, 15):
            print(report(f'old judge, floor {floor}', lambda r: r['legacy'] and type(r['legacy']['keep_likelihood']) is int
                         and r['legacy']['keep_likelihood'] >= floor and (r['legacy']['fits'] or not r['child']))[2])
    print(report('rater, current constants',
                 lambda r: mb.score_rating(r['rating']) >= (mb.RATE['keep_strict'] if r['strict'] else mb.RATE['keep_balanced'])
                 and not (r['child'] and r['rating'].get('hard_violations')))[2])
    best = []
    for appeal, subject, floor, cap, keep in itertools.product((0.5, 0.6, 0.7, 0.8), (0.1, 0.15, 0.2, 0.3),
                                                               (15, 25, 35), (0, 8, 15, 25), range(30, 75, 5)):
        quality = round(1 - appeal - subject, 2)
        if quality < 0:
            continue
        w = {'appeal': appeal, 'subject': subject, 'quality': quality, 'subject_floor': floor, 'violation_pt': cap}
        recall, reject, text = report('', lambda r: mb.score_rating(r['rating'], w) >= keep
                                      and not (r['child'] and r['rating'].get('hard_violations')))
        best.append((recall + reject, recall, reject, keep, w, text))
    best.sort(key=lambda x: (-x[0], -x[1]))
    print('best settings (balanced accuracy):')
    for score, recall, reject, keep, w, text in best[:8]:
        print(f'  keep>={keep} {w} ->{text}')
    by_board = {}
    for r in rated:
        by_board.setdefault(r['board'], []).append(r)
    for bid, rs in sorted(by_board.items()):
        g = [mb.score_rating(r['rating']) for r in rs if r['owner'] == 'good']
        b = [mb.score_rating(r['rating']) for r in rs if r['owner'] == 'bad']
        print(f'board {bid}: mean score fits {sum(g) / max(1, len(g)):.0f} (n={len(g)}), not-a-fit {sum(b) / max(1, len(b)):.0f} (n={len(b)})')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--analyze')
    ap.add_argument('--boards', default='')
    ap.add_argument('--per-board', type=int, default=40)
    ap.add_argument('--min-reviews', type=int, default=10)
    ap.add_argument('--legacy', action='store_true')
    ap.add_argument('--metrics', default='')
    a = ap.parse_args()
    if a.analyze:
        analyze(a.analyze)
    elif a.out:
        replay(a)
    else:
        ap.error('--out or --analyze')
