"""Temporary public-channel availability evidence, retained across discovery replans."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from ..models import Category, now
from .runtime import iso, parse_ts

RETRY_S = 24 * 3600
MAX_FAILURES = 32


def key(cat: Category, ref: str) -> str | None:
    """Use the actual requested tab; handles ignore case, channel IDs do not."""
    from .. import ytdl
    try:
        url = ytdl._channel_url(ref, tab="shorts" if cat.kind == "shorts" else None)
    except (ytdl.YtError, TypeError, AttributeError):
        return None
    parts = urlsplit(url)
    path = parts.path if parts.path.startswith("/channel/") else parts.path.casefold()
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def active(plan: dict) -> dict:
    """Prune expired/malformed history and cap its size, newest failures first."""
    rows = plan.get("channel_failures")
    if not isinstance(rows, dict):
        return {}
    instant = now()
    keep = []
    for ref, row in rows.items():
        if not isinstance(ref, str) or not isinstance(row, dict):
            continue
        try:
            retry = parse_ts(row.get("retry_at"))
        except (TypeError, OverflowError):
            continue
        if retry and instant < retry <= instant + timedelta(seconds=RETRY_S):
            keep.append((ref, {"retry_at": iso(retry)}))
    keep.sort(key=lambda item: item[1]["retry_at"], reverse=True)
    return dict(keep[:MAX_FAILURES])


def available(cat: Category, plan: dict, ref: str) -> bool:
    return key(cat, ref) not in active(plan)


def unavailable(cat: Category, plan: dict, ref: str) -> str | None:
    normalized = key(cat, ref)
    if normalized is None:
        return None
    rows = active(plan)
    retry_at = iso(now() + timedelta(seconds=RETRY_S))
    rows.pop(normalized, None)
    rows = {normalized: {"retry_at": retry_at}, **rows}
    plan["channel_failures"] = dict(sorted(rows.items(), key=lambda item: item[1]["retry_at"], reverse=True)[:MAX_FAILURES])
    return retry_at


def succeeded(cat: Category, plan: dict, ref: str) -> None:
    rows = active(plan)
    rows.pop(key(cat, ref), None)
    plan["channel_failures"] = rows


def context(plan: dict) -> list[dict]:
    return [{"reference": ref, **row} for ref, row in active(plan).items()]
