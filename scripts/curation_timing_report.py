#!/usr/bin/env python3
"""Summarize sanitized curation timing logs from stdin or files.

Example: python scripts/curation_timing_report.py data/curation-timing.log*
Percentiles describe individual overlapping spans; do not add them together.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone

PREFIX = "curation_timing "
STAGES = {"pass", "research", "ai", "discovery", "metadata", "transcript",
          "comments", "judgment", "limiter_wait", "download", "model_wait", "metadata_cache", "caption_check"}
RESULTS = {"hit", "miss", "no_track", "available", "accepted", "rejected"}


def _parse_time(value):
    if not isinstance(value, str):
        raise ValueError('invalid timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamps require a timezone')
    return parsed.astimezone(timezone.utc)


def summarize(lines, since=None):
    samples = defaultdict(list)
    for line in lines:
        if PREFIX not in line:
            continue
        try:
            item = json.loads(line.split(PREFIX, 1)[1])
            stage, outcome, duration = item["stage"], item["outcome"], item["duration_ms"]
            if (item.get("v") != 1 or stage not in STAGES or outcome not in {"ok", "error", "cancelled"}
                    or isinstance(duration, bool) or not isinstance(duration, (int, float))
                    or not math.isfinite(duration) or not 0 <= duration <= 86400000):
                continue
            if since is not None and _parse_time(item['timestamp']) < since:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        result = item.get("result", "")
        if not isinstance(result, str) or (result and result not in RESULTS):
            continue
        samples[(stage, outcome, result)].append(duration)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="log files (default: standard input)")
    parser.add_argument("--since", help="include events at or after an ISO timestamp with timezone")
    args = parser.parse_args()
    try:
        since = _parse_time(args.since) if args.since else None
    except ValueError as exc:
        parser.error(str(exc))
    import fileinput
    print("stage\toutcome\tresult\tn\tp50_ms\tp95_ms\tmax_ms")
    for (stage, outcome, result), values in sorted(summarize(fileinput.input(files=args.files or ('-',)), since).items()):
        values.sort()
        percentile = lambda p: values[max(0, math.ceil(p * len(values)) - 1)]
        print(f"{stage}\t{outcome}\t{result or '-'}\t{len(values)}\t{percentile(.5):.3f}\t{percentile(.95):.3f}\t{values[-1]:.3f}")


if __name__ == "__main__":
    main()
