"""Process-wide cap on Hub agents working at once (curators, Source Scout, briefings).

The limit is the `team.max_parallel` setting, read live so changes apply without a restart. Hub work runs in
the API process; the video curator is a separate worker with its own single loop and is not counted here.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable

MAX_PARALLEL = 8
log = logging.getLogger(__name__)

_cond = threading.Condition()
_jobs: dict[str, dict] = {}  # key -> {kind, ref_id, profile_id, label, state, since}


class Busy(Exception):
    """No slot became free within the allowed wait."""


_cached = [0.0, 1]  # monotonic time read, limit


def limit() -> int:
    if time.monotonic() - _cached[0] < 2:
        return _cached[1]
    from ..db import get_settings
    try:
        n = int(get_settings()['team'].get('max_parallel', 1))
    except (TypeError, ValueError, KeyError):
        n = 1
    _cached[:] = [time.monotonic(), max(1, min(MAX_PARALLEL, n))]
    return _cached[1]


def invalidate() -> None:
    """Apply a changed setting at once and wake waiting agents."""
    _cached[0] = 0.0
    with _cond:
        _cond.notify_all()


def _running() -> int:
    return sum(1 for j in _jobs.values() if j['state'] == 'running')


def free() -> int:
    """Slots not taken by running or waiting jobs (what the scheduler may still dispatch)."""
    n = limit()
    with _cond:
        return max(0, n - len(_jobs))


def _reserve(key: str, kind: str, ref_id: int | None, profile_id: int | None, label: str) -> bool:
    with _cond:
        if key in _jobs:
            return False
        _jobs[key] = {'kind': kind, 'ref_id': ref_id, 'profile_id': profile_id, 'label': label,
                      'state': 'waiting', 'since': time.time()}
        return True


@contextmanager
def slot(key: str, kind: str, ref_id: int | None, profile_id: int | None, label: str,
         wait_s: float | None = None, on_wait: Callable[[], None] | None = None,
         priority: bool = False, _reserved: bool = False):
    """Hold one agent slot. Scheduled work waits (optionally bounded) while the team is at its limit;
    `priority` work the user started starts at once but still occupies a slot, so scheduled work holds back."""
    deadline = None if wait_s is None else time.monotonic() + wait_s
    if not _reserved and not _reserve(key, kind, ref_id, profile_id, label):
        raise Busy('This agent is already working.')
    notified = False
    try:
        while True:
            n = limit()  # may read the database, so never under the lock
            with _cond:
                if priority or _running() < n:
                    _jobs[key].update(state='running', since=time.time())
                    break
                if notified or not on_wait:
                    left = None if deadline is None else deadline - time.monotonic()
                    if left is not None and left <= 0:
                        raise Busy('All agents are busy. Try again shortly or allow more parallel agents in Settings.')
                    _cond.wait(2 if left is None else min(2, left))  # then re-read the live limit
                    continue
            notified = True
            on_wait()
    except BaseException:
        with _cond:
            _jobs.pop(key, None)
            _cond.notify_all()
        raise
    try:
        yield
    finally:
        with _cond:
            _jobs.pop(key, None)
            _cond.notify_all()


after_release: Callable[[], None] | None = None  # set by the scheduler: start the next due agent at once


def spawn(key: str, kind: str, ref_id: int | None, profile_id: int | None, label: str, fn: Callable, *args,
          priority: bool = False) -> bool:
    """Run fn(*args) in a background thread once a slot is free. False if that agent is already queued/running."""
    # Reserve the key now so the scheduler's next tick does not dispatch it twice.
    if not _reserve(key, kind, ref_id, profile_id, label):
        return False

    def body():
        try:
            with slot(key, kind, ref_id, profile_id, label, priority=priority, _reserved=True):
                fn(*args)
        finally:
            if after_release:
                try:
                    after_release()
                except Exception:  # noqa: BLE001 — the scheduler tick retries anyway
                    log.exception('dispatch after %s failed', key)
    try:
        threading.Thread(target=body, daemon=True, name=key).start()
    except BaseException:
        with _cond:
            _jobs.pop(key, None)
        raise
    return True


def snapshot(profile_id: int | None = None) -> dict:
    """Live team state; other profiles' jobs are counted but not described."""
    n = limit()
    with _cond:
        jobs = [{'key': k, **v} for k, v in _jobs.items() if profile_id is None or v['profile_id'] == profile_id]
        return {'limit': n, 'running': _running(), 'waiting': sum(1 for j in _jobs.values() if j['state'] == 'waiting'),
                'jobs': sorted(jobs, key=lambda j: j['since'])}
