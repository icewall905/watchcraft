#!/usr/bin/env python3
"""Deterministic offline comparison of the AI-lane scheduling quantum.

Models an AI backlog on one worker with synthetic verdict and research times.
This exercises the production AI-work loop with fake dependencies: it performs
no network, model, downloads or changes to a real database. It is a scheduling
experiment, not a prediction of end-to-end production throughput.
"""
import argparse
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_temp_data = tempfile.TemporaryDirectory(prefix='wc-curation-benchmark-')
os.environ['BT_DATA_DIR'] = _temp_data.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from bt.agent.loop import Worker  # noqa: E402


def simulate(research_seconds=75, verdict_seconds=20, backlog=20, adaptive=True):
    if research_seconds < 0 or verdict_seconds <= 0 or not 1 <= backlog <= 200:
        raise ValueError('Use nonnegative research time, positive verdict time and 1–200 candidates.')
    remaining = [SimpleNamespace(id=n, category_id=1) for n in range(1, backlog + 1)]
    current = 0.0
    decided = []
    research_done = threading.Event()
    worker = object.__new__(Worker)
    worker.rt = SimpleNamespace(llm_ok=lambda: True, check=lambda: None)

    def verdict(_runtime, candidate):
        nonlocal current
        current += verdict_seconds
        remaining.remove(candidate)
        decided.append(candidate.id)
        if current >= research_seconds:
            research_done.set()
        return 'accepted'

    with patch('bt.agent.loop.learn.process', return_value=False), \
         patch('bt.agent.loop.learn.pending_count', return_value=0), \
         patch('bt.agent.loop.memory.process', return_value=False), \
         patch('bt.agent.loop.judge.pending', side_effect=lambda: list(remaining[:20])), \
         patch('bt.agent.loop.judge.judge_one', side_effect=verdict):
        passes, first_yield = 0, None
        while remaining:
            worker._ai_work(research_done if adaptive else None)
            passes += 1
            if first_yield is None:
                first_yield = current
    return {'first_yield_s': first_yield, 'verdicts': len(decided), 'passes': passes,
            'total_verdict_time_s': current, 'all_decided_once': decided == list(range(1, backlog + 1))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-seconds', type=float, default=75)
    parser.add_argument('--verdict-seconds', type=float, default=20)
    parser.add_argument('--backlog', type=int, default=20)
    args = parser.parse_args()
    config = (args.research_seconds, args.verdict_seconds, args.backlog)
    print(json.dumps({'assumptions': {'research_s': config[0], 'verdict_s': config[1], 'backlog': config[2]},
                      'prior_drain': simulate(*config, adaptive=False), 'adaptive': simulate(*config, adaptive=True),
                      'limitations': 'Synthetic AI-loop benchmark; discovery, I/O waits and downloads are not modelled.'}, indent=2))


if __name__ == '__main__':
    main()
