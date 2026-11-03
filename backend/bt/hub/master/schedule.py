"""When the master curator runs: shortly after curator runs bring something new, debounced so a
burst of runs becomes one pass, leased so two processes never run the same profile at once."""
import logging
import time

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import select

from ...access import hub_allowed
from ...db import session
from ..models import MasterQueue
from . import judge, pipeline

log = logging.getLogger(__name__)

DEBOUNCE = 300.0       # wait this long after the latest run for others to land
MAX_DELAY = 1800.0     # but never longer than this after the first unjudged change
LEASE = 900.0
RETRY = 600.0          # model judging was skipped (GPU busy): try again this much later
MAX_ATTEMPTS = 3       # after that the nightly pass reads what is left


def mark(pid: int, now: float | None = None) -> None:
    """Something new arrived for this profile. Safe to call from any curator thread."""
    now = now or time.time()
    with session() as s:
        s.exec(insert(MasterQueue).values(profile_id=pid, first_at=now, due_at=now + DEBOUNCE, lease_until=0, gen=1, attempts=0)
               .on_conflict_do_update(index_elements=['profile_id'], set_={
                   'due_at': func.min(MasterQueue.first_at + MAX_DELAY, now + DEBOUNCE),
                   'gen': MasterQueue.gen + 1, 'attempts': 0}))
        s.commit()


def due(now: float | None = None) -> list[int]:
    now = now or time.time()
    with session() as s:
        return list(s.exec(select(MasterQueue.profile_id).where(MasterQueue.due_at <= now, MasterQueue.lease_until <= now)
                           .order_by(MasterQueue.due_at)).all())


def claim(pid: int, now: float | None = None) -> int | None:
    """Lease a due profile. Returns the change generation it covers (the pass's identity), or None."""
    now = now or time.time()
    with session() as s:
        row = s.get(MasterQueue, pid)
        if not row or row.due_at > now or row.lease_until > now:
            return None
        res = s.exec(update(MasterQueue).where(MasterQueue.profile_id == pid, MasterQueue.due_at == row.due_at,
                                                MasterQueue.lease_until <= now)
                     # Changes from here on are a new burst with their own debounce window.
                     .values(lease_until=now + LEASE, first_at=now))
        s.commit()
        return row.gen if res.rowcount == 1 else None


def light_pass(pid: int, claimed: int) -> dict:
    """Rules for everything new, the model for the uncertain few, then tiers again with its scores."""
    with session() as s:
        if not hub_allowed(s, pid):
            s.exec(delete(MasterQueue).where(MasterQueue.profile_id == pid))
            s.commit()
            return {'skipped': 'hub not allowed'}
    out: dict = {}
    try:
        with session() as s:
            out['rules'] = pipeline.run(s, pid)
        with session() as s:
            out['model'] = judge.judge(s, pid)
        if out['model']['judged']:
            with session() as s:
                out['rules'] = pipeline.run(s, pid)
    except Exception:  # noqa: BLE001 — retried a few times, then left to the nightly pass
        log.exception('master curator pass for profile %s failed', pid)
        _finish(pid, claimed, retry=True)
        return {**out, 'failed': True}
    _finish(pid, claimed, retry=out['model']['stopped'] == 'video busy')
    log.info('master curator pass for profile %s: %s', pid, out)
    return out


def _finish(pid: int, claimed: int, retry: bool) -> None:
    now = time.time()
    with session() as s:
        row = s.get(MasterQueue, pid)
        if not row:
            return
        if row.gen != claimed:
            row.lease_until = 0  # new items arrived during the pass: run again when due
        elif retry and row.attempts + 1 < MAX_ATTEMPTS:
            row.attempts, row.due_at, row.lease_until = row.attempts + 1, now + RETRY, 0
        else:
            s.delete(row)
            s.commit()
            return
        s.add(row)
        s.commit()
