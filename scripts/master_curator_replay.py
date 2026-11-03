#!/usr/bin/env python3
"""Run a bounded master-curator replay against private SQLite backups.

The source database is opened read-only and copied with SQLite's backup API.
All schema initialization and curator writes happen in private scratch copies.
Reports intentionally identify profiles only by numeric id and role.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Callable
import urllib.request


def queue_busy(metrics_url: str, wait: float = 600, poll: float = 15) -> Callable[[], bool]:
    """Live yield check for a shared vLLM server: busy while other requests queue.

    The replay is a separate process, so the snapshot's agent state cannot see the
    live video curator. Wait up to `wait` seconds for the queue to drain before
    reporting busy; any read failure counts as busy.
    """
    def waiting() -> float:
        with urllib.request.urlopen(metrics_url, timeout=5) as r:
            return sum(float(line.rsplit(' ', 1)[1]) for line in r.read().decode().splitlines()
                       if line.startswith('vllm:num_requests_waiting{'))

    def busy() -> bool:
        deadline = time.monotonic() + wait
        while waiting() > 0:
            if time.monotonic() > deadline:
                return True
            time.sleep(poll)
        return False
    return busy


def _copy_database(source: Path, destination: Path) -> None:
    uri = source.resolve(strict=True).as_uri() + '?mode=ro'
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    destination.chmod(0o600)


def _markdown(report: dict[str, Any]) -> str:
    lines = [f"# Master curator replay", '', f"Timestamp: `{report['now_utc']}`",
             f"Judge enabled: `{report['judge_enabled']}`", '',
             'This report is an offline replay on a SQLite backup. Profile names and settings are omitted.', '']
    if report['judge_enabled']:
        lines += ['Model judging uses the standard video-busy guard, `band=False`, and a 150-item limit. '
                  'It only considers items from the prior 48 hours; this is not a seven-day model judgment.', '']
    for profile in report['profiles']:
        lines += [f"## Profile {profile['profile_id']} ({profile['role']})", '']
        if not profile['hub_allowed']:
            lines += ['Skipped: profile does not pass `hub_allowed`.', '']
            continue
        for pass_name in ('baseline', 'model') if report['judge_enabled'] else ('baseline',):
            result = profile[pass_name]
            if result is None:
                continue
            lines += [f"### {pass_name.title()}", '',
                      f"Counts: {json.dumps(result['counts'], sort_keys=True)}", '']
            if pass_name == 'model':
                lines += [f"Judge: {json.dumps(result['judge'], sort_keys=True)}", '']
            for kind, type_result in sorted(result['by_type'].items()):
                lines += [f"#### {kind}", '', f"Counts: {json.dumps(type_result['counts'], sort_keys=True)}", '']
                for tier in ('feature', 'keep', 'set_aside', 'reject', 'waiting'):
                    rows = type_result['samples'].get(tier, [])
                    if rows:
                        lines += [f"##### {tier.replace('_', ' ').title()}", '']
                        for row in rows:
                            lines += [f"- **{row['title']}** (final {row['final']}) — "
                                      f"{'; '.join(row['reasons']) or 'no reason recorded'}"]
                        lines.append('')
            if result.get('media_analysis'):
                media = result['media_analysis']
                lines += [f"Meme media: {media['meme_items']} items; {media['with_local_image']} have captured image "
                          f"bytes; {media['without_local_image']} do not. A missing image limits visual duplicate "
                          'analysis; captured image assets are included in the database snapshot.', '']
        if report['judge_enabled']:
            lines += [f"### Baseline versus model", '',
                      f"Changed tiers: {json.dumps(profile['comparison']['tier_changes'], sort_keys=True)}", '']
    return '\n'.join(lines).rstrip() + '\n'


def _run_pass(db_path: Path, data_dir: Path, profile_ids: list[tuple[int, str]], now: float,
              judge_enabled: bool, busy_check: Callable[[], bool] | None = None,
              judge_call: Callable[..., dict] | None = None) -> dict[int, dict[str, Any]]:
    # The application reads BT_DATA_DIR while importing config/db. Set it before
    # any bt import, and point every pass at its own disposable backup.
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(db_path, data_dir / 'bt.sqlite3')
    (data_dir / 'bt.sqlite3').chmod(0o600)
    os.environ['BT_DATA_DIR'] = str(data_dir)
    import sys
    backend = str(Path(__file__).resolve().parents[1] / 'backend')
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from sqlmodel import Session, select
    from bt.db import engine, init_db
    from bt.access import hub_allowed
    from bt.models import Profile
    from bt.hub.models import ContentItem, CapturedAsset, ItemVerdict
    from bt.hub.master import pipeline, judge as master_judge

    init_db()
    original_video_busy = master_judge.video_busy
    original_model_call = master_judge.ai._call
    if busy_check is not None:
        def guarded_video_busy(now=None):
            try:
                return original_video_busy(now) or bool(busy_check())
            except Exception:  # noqa: BLE001 - live safety check must fail closed
                return True
        master_judge.video_busy = guarded_video_busy
    if judge_call is not None:
        master_judge.ai._call = judge_call
    def snapshot(session, pid, counts, judge_result):
        ids = pipeline.visible_recent(session, pid, now)
        rows = session.exec(select(ItemVerdict, ContentItem)
                            .join(ContentItem, ContentItem.id == ItemVerdict.item_id)
                            .where(ItemVerdict.profile_id == pid, ItemVerdict.item_id.in_([i.id for i in ids]))
                            .order_by(ItemVerdict.final.desc(), ContentItem.id)).all()
        tiers = ('feature', 'keep', 'set_aside', 'reject', 'waiting')
        samples = {k: [] for k in tiers}
        by_type: dict[str, dict[str, Any]] = {}
        for item in ids:
            by_type.setdefault(item.type or 'unknown', {
                'counts': {k: 0 for k in tiers}, 'samples': {k: [] for k in tiers}})
        verdict_items = {item.id for _, item in rows}
        waiting_rows = [item for item in ids if item.id not in verdict_items]
        for item in waiting_rows:
            entry = by_type[item.type or 'unknown']
            entry['counts']['waiting'] += 1
            row = {'title': item.title, 'type': item.type, 'final': None,
                   'reasons': ['awaiting meme image capture']}
            if len(entry['samples']['waiting']) < 5:
                entry['samples']['waiting'].append(row)
            if len(samples['waiting']) < 5:
                samples['waiting'].append(row)
        for verdict, item in rows:
            tier = verdict.tier if verdict.tier in samples else 'keep'
            row = {'title': item.title, 'type': item.type, 'final': verdict.final,
                   'reasons': verdict.reasons or []}
            entry = by_type[item.type or 'unknown']
            entry['counts'][tier] += 1
            if len(entry['samples'][tier]) < 5:
                entry['samples'][tier].append(row)
            if len(samples[tier]) < 5:
                samples[tier].append(row)
        meme_items = [i for i in ids if i.type == 'meme']
        captured = set(session.exec(select(CapturedAsset.item_id).where(
            CapturedAsset.item_id.in_([i.id for i in meme_items]), CapturedAsset.kind == 'image')).all()) if meme_items else set()
        return {'counts': {**counts, 'waiting': len(waiting_rows)}, 'judge': judge_result,
                'samples': samples, 'by_type': by_type, 'media_analysis': {
                    'meme_items': len(meme_items), 'with_local_image': len(captured),
                    'without_local_image': len(meme_items) - len(captured)}}

    outcomes: dict[int, dict[str, Any]] = {}
    try:
        with Session(engine, expire_on_commit=False) as session:
            for pid, role in profile_ids:
                if not hub_allowed(session, pid):
                    outcomes[pid] = {'hub_allowed': False, 'role': role}
                    continue
                baseline_counts = pipeline.run(session, pid, now)
                baseline = snapshot(session, pid, baseline_counts,
                                    {'judged': 0, 'skipped': 0, 'stopped': 'not requested'})
                model = None
                if judge_enabled:
                    judge_result = master_judge.judge(session, pid, now, limit=150, band=False)
                    counts = pipeline.run(session, pid, now)
                    model = snapshot(session, pid, counts, judge_result)
                outcomes[pid] = {'hub_allowed': True, 'role': role,
                                 'baseline': baseline, 'model': model}
    finally:
        master_judge.video_busy = original_video_busy
        master_judge.ai._call = original_model_call
        engine.dispose()
    return outcomes


def replay(database: str | Path, output: str | Path, now: str | None = None,
           with_judge: bool = False, busy_check: Callable[[], bool] | None = None,
           judge_call: Callable[..., dict] | None = None) -> dict[str, Any]:
    source = Path(database).resolve(strict=True)
    output_dir = Path(output).resolve()
    if source == output_dir or output_dir in source.parents:
        raise ValueError('output directory must not contain the source database')
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('output directory must be empty')
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    instant = datetime.fromisoformat((now or datetime.now(timezone.utc).isoformat()).replace('Z', '+00:00'))
    if instant.tzinfo is None:
        raise ValueError('--now must include a timezone')
    instant = instant.astimezone(timezone.utc)
    timestamp = instant.timestamp()

    scratch_root = Path(tempfile.mkdtemp(prefix='watchcraft-master-replay-'))
    scratch_root.chmod(0o700)
    try:
        seed = scratch_root / 'seed.sqlite3'
        _copy_database(source, seed)
        # Read numeric profile ids and roles from the database-only snapshot.
        # The report never serializes profile names, preferences or settings.
        src = sqlite3.connect(seed.as_uri() + '?mode=ro', uri=True)
        try:
            profiles = [(int(pid), role) for pid, role in
                        src.execute('SELECT id, role FROM profile ORDER BY id').fetchall()]
        finally:
            src.close()

        outcomes = _run_pass(seed, scratch_root / 'working-data', profiles, timestamp,
                             with_judge, busy_check, judge_call)
        report_profiles = []
        for pid, role in profiles:
            outcome = outcomes.get(pid) or {'hub_allowed': False}
            allowed = bool(outcome.get('hub_allowed'))
            b, m = outcome.get('baseline'), outcome.get('model')
            comparison = {'tier_changes': {}}
            if allowed and with_judge and m:
                for tier in ('feature', 'keep', 'set_aside', 'reject', 'waiting'):
                    before = (b.get('counts') or {}).get(tier, 0)
                    after = (m.get('counts') or {}).get(tier, 0)
                    comparison['tier_changes'][tier] = after - before
            report_profiles.append({'profile_id': pid, 'role': role, 'hub_allowed': allowed,
                                    'baseline': b if allowed else None,
                                    'model': m if allowed and with_judge else None,
                                    'comparison': comparison})
        report = {'now_utc': instant.isoformat(), 'judge_enabled': with_judge,
                  'profiles': report_profiles,
                  'notes': ['Profile names and settings are deliberately omitted.',
                            'The judge uses the existing enabled model configuration and standard video-busy policy.',
                            'A judge-only pass covers fresh items from 48 hours, not the full seven-day curator window.']}
        for filename, payload in (('report.json', json.dumps(report, indent=2, ensure_ascii=False) + '\n'),
                                  ('report.md', _markdown(report))):
            path = output_dir / filename
            path.write_text(payload, encoding='utf-8')
            path.chmod(0o600)
        return report
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, help='source SQLite database; opened read-only')
    parser.add_argument('--output', required=True, help='new or empty private report directory')
    parser.add_argument('--now', help='fixed ISO timestamp with timezone; shared by both passes')
    parser.add_argument('--judge', action='store_true', help='run the optional local judge against the backup')
    parser.add_argument('--yield-metrics', help='vLLM /metrics URL; judging waits while its queue is non-empty')
    args = parser.parse_args(argv)
    try:
        replay(args.database, args.output, args.now, args.judge,
               queue_busy(args.yield_metrics) if args.yield_metrics else None)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(f'Replay reports written to {Path(args.output).resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
