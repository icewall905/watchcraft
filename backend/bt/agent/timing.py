"""Small, non-secret timing events for video curation.

Events use an intentionally closed vocabulary: callers cannot accidentally log
prompts, URLs, exception messages, or arbitrary metadata. Logs retain events
according to the deployment's existing log retention policy.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger("bt.agent.timing")
PREFIX = "curation_timing "
STAGES = frozenset({
    "pass", "research", "ai", "discovery", "metadata", "transcript",
    "comments", "judgment", "limiter_wait", "download", "model_wait", "metadata_cache", "caption_check",
})
OUTCOMES = frozenset({"ok", "error", "cancelled"})
RESULTS = frozenset({"hit", "miss", "no_track", "available", "accepted", "rejected"})
_current_trace: ContextVar[str | None] = ContextVar("curation_trace", default=None)


class _PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        # Every rollover must retain private permissions, independent of umask.
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(fd)
            raise


def configure(data_dir: Path) -> bool:
    """Keep worker timing records in a small local rotating log, never Docker's unbounded log."""
    if any(getattr(handler, "_curation_timing", False) for handler in log.handlers):
        return True
    try:
        path = Path(data_dir) / "curation-timing.log"
        for suffix in (".1", ".2", ".3"):
            backup = Path(str(path) + suffix)
            if backup.is_symlink():
                raise OSError("Timing log backup must not be a symbolic link")
            if backup.exists():
                backup.chmod(0o600)
        handler = _PrivateRotatingHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler._curation_timing = True
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
        return True
    except OSError:
        # Instrumentation must not interrupt curation or fall back to unbounded output.
        log.disabled = True
        logging.getLogger("bt.agent").warning("Curation timing log unavailable; timing collection disabled")
        return False


@dataclass
class SpanResult:
    """Allow callers to classify handled failures without logging their contents."""
    outcome: str = "ok"
    result: str | None = None


def new_trace_id() -> str:
    """Return a random correlation ID without exposing source data."""
    return uuid.uuid4().hex


def current_trace() -> str | None:
    return _current_trace.get()


def _id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise ValueError("identifier must be a positive integer")
    return value


def _trace(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("trace ID must be a lowercase 32-character hex string")
    return value


@contextmanager
def span(stage: str, *, trace_id: str | None = None, candidate_id: int | None = None,
         category_id: int | None = None, profile_id: int | None = None,
         clock: Callable[[], float] = time.monotonic) -> Iterator[SpanResult]:
    """Emit one duration, including on exceptions and cancellation.

    Correlation IDs are optional; supplied values are validated before work
    begins. BaseException subclasses other than Exception represent cancellation
    or interruption, and are re-raised unchanged.
    """
    if stage not in STAGES:
        raise ValueError("unknown timing stage")
    trace_id = _trace(trace_id) or _current_trace.get()
    event = {"v": 1, "timestamp": datetime.now(timezone.utc).isoformat(), "stage": stage, "trace_id": trace_id,
             "candidate_id": _id(candidate_id), "category_id": _id(category_id),
             "profile_id": _id(profile_id)}
    token = _current_trace.set(trace_id)
    start = clock()
    result = SpanResult()
    try:
        yield result
    except Exception as exc:
        from .runtime import Interrupt
        result.outcome = "cancelled" if isinstance(exc, Interrupt) else "error"
        raise
    except BaseException:
        result.outcome = "cancelled"
        raise
    finally:
        # Fail closed if a caller accidentally supplies a non-allowlisted value.
        event["outcome"] = result.outcome if result.outcome in OUTCOMES else "error"
        if result.result in RESULTS:
            event["result"] = result.result
        event["duration_ms"] = round(max(0.0, clock() - start) * 1000, 3)
        log.info("%s%s", PREFIX, json.dumps(event, separators=(",", ":"), allow_nan=False))
        _current_trace.reset(token)
