"""When the master curator runs: shortly after curator runs bring something new, debounced so a
burst of runs becomes one pass, leased so two processes never run the same profile at once."""
import logging
import time

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import select

from ...access import hub_allowed
from ...db import get_kv, get_settings, save_setting, session
from ...models import Profile
from ..models import Curator, MasterQueue
from . import edition, judge, pipeline, prefs

log = logging.getLogger(__name__)

DEBOUNCE = 60.0       # wait this long after the latest run for others to land
MAX_DELAY = 300.0     # but never longer than this after the first unjudged change
LEASE = 900.0
NIGHT_LEASE = 3 * 3600.0  # a nightly pass reads up to 150 items and writes prose
RETRY = 600.0          # model judging was skipped (GPU busy): try again this much later
CATCHUP = 900.0
CONTINUE = 120.0
MAX_ATTEMPTS = 3       # after that the nightly pass reads what is left


def mark(pid: int, now: float | None = None) -> None:
    """Something new arrived for this profile. Safe to call from any curator thread."""
    now = now or time.time()
    with session() as s:
        s.exec(insert(MasterQueue).values(profile_id=pid, first_at=now, due_at=now + DEBOUNCE, lease_until=0, gen=1, attempts=0)
               .on_conflict_do_update(index_elements=['profile_id'], set_={
                   'due_at': func.min(MasterQueue.first_at + MAX_DELAY, now + DEBOUNCE),
                   'gen': MasterQueue.gen + 1}))  # attempts stay: retries are bounded per queued burst
        s.commit()


def catch_up(now: float | None = None) -> None:
    """Refresh active profiles periodically even when collectors found no new items."""
    now = now or time.time()
    if not get_settings()['hub_master'].get('enabled', True):
        return
    with session() as s:
        pids = s.exec(select(Curator.profile_id).where(Curator.enabled == True).distinct()).all()  # noqa: E712
        for pid in pids:
            if not hub_allowed(s, pid) or s.get(MasterQueue, pid):
                continue
            key = f'watchcraft_master_catchup:{pid}'
            if now - get_kv(key, {}, s=s).get('at', 0) < CATCHUP:
                continue
            queued = s.exec(insert(MasterQueue).values(profile_id=pid, first_at=now, due_at=now,
                                                       gen=1, lease_until=0, attempts=0)
                            .on_conflict_do_nothing(index_elements=['profile_id']))
            if not queued.rowcount:
                continue
            save_setting(key, {'at': now}, s=s)
        s.commit()


def due(now: float | None = None) -> list[int]:
    now = now or time.time()
    with session() as s:
        return list(s.exec(select(MasterQueue.profile_id).where(MasterQueue.due_at <= now, MasterQueue.lease_until <= now)
                           .order_by(MasterQueue.due_at)).all())


def claim(pid: int, now: float | None = None, lease: float = LEASE) -> int | None:
    """Lease a due profile. Returns the change generation it covers (the pass's identity), or None."""
    now = now or time.time()
    with session() as s:
        row = s.get(MasterQueue, pid)
        if not row or row.due_at > now or row.lease_until > now:
            return None
        res = s.exec(update(MasterQueue).where(MasterQueue.profile_id == pid, MasterQueue.due_at == row.due_at,
                                                MasterQueue.lease_until <= now)
                     # Changes from here on are a new burst with their own debounce window.
                     .values(lease_until=now + lease, first_at=now))
        s.commit()
        return row.gen if res.rowcount == 1 else None


def release(pid: int) -> None:
    """Give a lease back when its pass could not be started."""
    with session() as s:
        s.exec(update(MasterQueue).where(MasterQueue.profile_id == pid).values(lease_until=0))
        s.commit()


def claim_now(pid: int, now: float | None = None) -> int | None:
    """Queue and lease a profile at once (the nightly pass), through the same lease as post-run
    passes so the two never work on one profile together."""
    now = now or time.time()
    with session() as s:
        # A pass already holding the lease is left alone: bumping its generation would make it run again.
        s.exec(insert(MasterQueue).values(profile_id=pid, first_at=now, due_at=now, lease_until=0, gen=1, attempts=0)
               .on_conflict_do_update(index_elements=['profile_id'], set_={'due_at': now, 'gen': MasterQueue.gen + 1},
                                      where=MasterQueue.lease_until <= now))
        s.commit()
    return claim(pid, now, NIGHT_LEASE)


def _manual_pending(pid: int) -> bool:
    state = get_kv(f'watchcraft_master_manual:{pid}', {})
    return state.get('requested_at', 0) > state.get('completed_at', 0)


def _manual_rebuild(s, pid: int, out: dict, band: bool = True) -> None:
    """Release seen placements only after review succeeds; retries retain the request."""
    key = f'watchcraft_master_manual:{pid}'
    state = get_kv(key, {}, s=s)
    if state.get('requested_at', 0) <= state.get('completed_at', 0):
        return
    if out.get('model', {}).get('stopped') in {'video busy', 'model error'}:
        return
    if judge.ai.enabled() and judge.candidates(s, pid, time.time(), 1, band=band):
        return
    ed = s.exec(select(edition.Edition).where(edition.Edition.profile_id == pid, edition.Edition.day == edition.today())).first()
    if ed:
        ed.sections, ed.intro, ed.intro_refs = [], '', []
        s.add(ed)
        s.flush()
    save_setting(key, {**state, 'completed_at': time.time()}, s=s)


def light_pass(pid: int, claimed: int) -> dict:
    """Rules for everything new, the model for the uncertain few, then tiers again with its scores."""
    if not get_settings()['hub_master'].get('enabled', True):
        release(pid)
        return {'skipped': 'master disabled'}
    with session() as s:
        if not hub_allowed(s, pid):
            s.exec(delete(MasterQueue).where(MasterQueue.profile_id == pid))
            s.commit()
            return {'skipped': 'hub not allowed'}
    out: dict = {}
    try:
        # Preference CRUD queues this pass too; refresh the core before any judging
        # so edits and deletions reach the reader prompt on the next scheduled pass.
        with session() as s:
            core = prefs.rebuild_core(s, pid)
            s.commit()
            out['preference_core_chars'] = len(core.text)
        with session() as s:
            out['rules'] = pipeline.run(s, pid)
        with session() as s:
            out['model'] = judge.judge(s, pid)
        if out['model']['judged']:
            with session() as s:
                out['rules'] = pipeline.run(s, pid)
        with session() as s:
            _manual_rebuild(s, pid, out)
            ed = edition.patch(s, pid)
            out['prose'] = edition.write(s, pid, ed, busy=judge.video_busy)
    except Exception:  # noqa: BLE001 — retried a few times, then left to the nightly pass
        log.exception('master curator pass for profile %s failed', pid)
        _finish(pid, claimed, retry=True, persistent=_manual_pending(pid))
        return {**out, 'failed': True}
    with session() as s:
        remaining = bool(judge.candidates(s, pid, time.time(), 1)) if judge.ai.enabled() else False
    _finish(pid, claimed, retry=out['model']['stopped'] in {'video busy', 'model error'} or (_manual_pending(pid) and remaining and not out['model']['judged']),
            more=remaining and bool(out['model']['judged']), persistent=_manual_pending(pid))
    log.info('master curator pass for profile %s: %s', pid, out)
    return out


def _finish(pid: int, claimed: int, retry: bool, more: bool = False, persistent: bool = False) -> None:
    now = time.time()
    with session() as s:
        row = s.get(MasterQueue, pid)
        if not row:
            return
        if row.gen != claimed:
            row.lease_until = 0  # new items arrived during the pass: run again when due
        elif more:
            row.attempts, row.due_at, row.lease_until = 0, now + CONTINUE, 0
        elif retry and row.attempts + 1 < MAX_ATTEMPTS:
            row.attempts, row.due_at, row.lease_until = row.attempts + 1, now + RETRY, 0
        elif retry and persistent:
            row.attempts, row.due_at, row.lease_until = 0, now + CATCHUP, 0
        else:
            s.delete(row)
            s.commit()
            return
        s.add(row)
        s.commit()


# ---------- nightly

NIGHT_KEY = 'watchcraft_master_nightly'


def nightly_day(now: float | None = None) -> str | None:
    """Today after the configured start, allowing a busy morning to catch up later."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    st = get_settings()
    if not st['hub_master'].get('enabled', True):
        return None
    instant = datetime.fromtimestamp(now or time.time(), timezone.utc)
    local = instant.astimezone(ZoneInfo(st['memory']['timezone']))
    hour, minute = map(int, st['hub_master']['time'].split(':'))
    return local.date().isoformat() if (local.hour, local.minute) >= (hour, minute) else None


def nightly_due(now: float | None = None) -> list[int]:
    """Profiles still waiting for tonight's pass: hub allowed, with at least one curator."""
    day = nightly_day(now)
    if not day:
        return []
    with session() as s:
        pids = s.exec(select(Profile.id).where(Profile.id.in_(select(Curator.profile_id)))).all()
        ready = []
        for pid in pids:
            queued = s.get(MasterQueue, pid)
            if queued and queued.due_at > (now or time.time()):
                continue
            if get_kv(f'{NIGHT_KEY}:{pid}', {}, s=s).get('day') != day and hub_allowed(s, pid):
                ready.append(pid)
        return ready


def _night_done(pid: int, day: str) -> None:
    # One key per profile: passes finishing together cannot overwrite each other's mark.
    save_setting(f'{NIGHT_KEY}:{pid}', {'day': day})


def night_pass(pid: int, claimed: int, day: str) -> dict:
    """Everything recent gets a model's reading, then a fresh morning edition with its prose."""
    out: dict = {}
    if not get_settings()['hub_master'].get('enabled', True):
        release(pid)
        return {'skipped': 'master disabled'}
    with session() as s:
        if not hub_allowed(s, pid):
            s.exec(delete(MasterQueue).where(MasterQueue.profile_id == pid))
            s.commit()
            return {'skipped': 'hub not allowed'}
    try:
        with session() as s:
            out['preferences'] = prefs.collect(s, pid)
        with session() as s:
            out['rules'] = pipeline.run(s, pid)
        with session() as s:
            out['model'] = judge.judge(s, pid, limit=judge.NIGHT_LIMIT, band=False)
        with session() as s:
            out['rules'] = pipeline.run(s, pid)
        with session() as s:
            _manual_rebuild(s, pid, out, band=False)
            ed = edition.patch(s, pid)
            out['prose'] = edition.write(s, pid, ed, busy=judge.video_busy)
    except Exception:  # noqa: BLE001 — tomorrow's pass and the post-run passes carry on
        log.exception('nightly master curator pass for profile %s failed', pid)
        out['failed'] = True
    with session() as s:
        remaining = bool(judge.candidates(s, pid, time.time(), 1, band=False)) if judge.ai.enabled() else False
    stopped = out.get('model', {}).get('stopped', '')
    retry = bool(out.get('failed')) or stopped in {'video busy', 'model error'}
    more = remaining and bool(out.get('model', {}).get('judged')) and not retry
    # Only a completed reading (or an intentionally absent model) finishes today's
    # full pass. Its queue otherwise retains the nightly identity via nightly_due.
    if not retry and not remaining:
        _night_done(pid, day)
    _finish(pid, claimed, retry=retry or (remaining and not more), more=more, persistent=True)
    log.info('nightly master curator pass for profile %s: %s', pid, out)
    return out
