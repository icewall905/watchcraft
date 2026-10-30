"""Bounded, durable shared polling for public feed providers.

Only provider + exact endpoint identifies a response. Query providers and AI
interpretations never enter this cache. Network calls run outside DB transactions.
"""
import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import text, update
from sqlmodel import select

from ..db import session
from .models import SourcePoll

FRESH_SECONDS = 300
ERROR_SECONDS = 30
LEASE_SECONDS = 180
WAIT_SECONDS = 190
MAX_ENTRIES = 256
MAX_RESPONSE_BYTES = 2_000_000
log = logging.getLogger(__name__)


@dataclass
class Result:
    items: list
    fetched_at: float
    shared: bool
    error: str = ''


def fetch(provider, url: str, format_error, *, force: bool = False) -> Result:
    """Reuse recent feed results; a forced check requires a response newer than its request."""
    if provider.mode != 'feed':
        raise ValueError('Shared polling only supports feed providers.')
    key = hashlib.sha256((provider.key + '\0' + url).encode()).hexdigest()
    requested = time.time()
    deadline = time.monotonic() + WAIT_SECONDS
    token = uuid.uuid4().hex
    while True:
        with session() as s:
            s.exec(text('BEGIN IMMEDIATE'))
            # A response may have completed while we waited for the DB lock.
            now = time.time()
            row = s.get(SourcePoll, key)
            if row:
                age = now - row.fetched_at
                fresh = row.fetched_at > 0 and 0 <= age < (ERROR_SECONDS if row.error else FRESH_SECONDS)
                if fresh and (not force or row.fetched_at >= requested):
                    return Result(row.items, row.fetched_at, True, row.error)
            if not row or row.lease_until <= now:
                row = row or SourcePoll(key=key)
                row.lease_token, row.lease_until = token, now + LEASE_SECONDS
                s.add(row); s.commit()
                break
        if time.monotonic() >= deadline:
            return Result([], time.time(), False, 'Another source check is still running. Try again shortly.')
        time.sleep(0.1)
    stopped = threading.Event()
    def renew():
        while not stopped.wait(max(0.05, LEASE_SECONDS / 3)):
            try:
                with session() as s:
                    renewed = s.exec(update(SourcePoll).where(SourcePoll.key == key, SourcePoll.lease_token == token)
                                     .values(lease_until=time.time() + LEASE_SECONDS))
                    s.commit()
                    if not renewed.rowcount:
                        return
            except Exception:
                log.warning('Could not renew a shared source-poll lease', exc_info=True)
    heartbeat = threading.Thread(target=renew, daemon=True, name='source-poll-lease')
    try:
        heartbeat.start()
        try:
            items, error = provider.fetch(url, ''), ''
            if len(json.dumps(items, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
                raise ValueError('Source result is too large to share.')
        except Exception as exc:  # One failed endpoint does not fail a curator.
            items, error = [], format_error(exc) or 'Source check failed.'
        finished = time.time()
        with session() as s:
            s.exec(text('BEGIN IMMEDIATE'))
            row = s.get(SourcePoll, key)
            if row and row.lease_token == token:
                row.items, row.error, row.fetched_at = items, error, finished
                row.lease_token, row.lease_until = '', 0
                s.add(row)
                # Keep active leases, and at most 256 completed endpoint responses.
                stale = s.exec(select(SourcePoll).where(SourcePoll.key != key, SourcePoll.lease_until <= finished)
                               .order_by(SourcePoll.fetched_at.desc()).offset(MAX_ENTRIES - 1)).all()
                for old in stale:
                    s.delete(old)
                s.commit()
        return Result(items, finished, False, error)
    finally:
        stopped.set()
        if heartbeat.ident is not None:
            heartbeat.join(timeout=2)
        # Interrupted checks release their own lease; process death recovers by expiry.
        with session() as s:
            s.exec(text('BEGIN IMMEDIATE'))
            row = s.get(SourcePoll, key)
            if row and row.lease_token == token:
                row.lease_token, row.lease_until = '', 0
                s.add(row); s.commit()
