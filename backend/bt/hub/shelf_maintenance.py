"""Bounded durable shelf upkeep using existing collector schedules and quality gates."""
import logging
import time

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import select

from ..access import hub_allowed
from ..db import get_kv, save_setting, session
from ..models import Setting
from .models import Curator

log = logging.getLogger(__name__)
KEY = 'watchcraft_shelf_maintenance'
CHECK_INTERVAL = 15 * 60
MIN_COLLECTOR_INTERVAL = 60 * 60
MAX_ADVANCED = 3


def status(s, pid: int) -> dict:
    state = get_kv(KEY, {}, s=s)
    own = state.get('profiles', {}).get(str(pid), {})
    queued = []
    for cid in own.get('collectors_queued', []):
        c = s.get(Curator, cid)
        if (c and c.profile_id == pid and c.enabled and c.kind == 'watch' and c.interval_hours > 0
                and (c.next_run <= time.time() or c.last_status in ('running', 'queued'))):
            queued.append(cid)
    return {'last_checked_at': own.get('checked_at'),
            'next_check_at': state.get('next_check_at'),
            'collectors_queued': queued,
            'status': own.get('status', 'Waiting for the first shelf check')}


def _claim(now: float) -> bool:
    """CAS lease across processes: a restart never turns checks into a tight loop."""
    with session() as s:
        s.exec(insert(Setting).values(key=KEY, value={}).on_conflict_do_nothing(index_elements=['key']))
        s.commit()
        row = s.get(Setting, KEY)
        old = row.value or {}
        if old.get('next_check_at', 0) > now:
            return False
        result = s.exec(update(Setting).where(Setting.key == KEY, Setting.value == old)
                        .values(value={**old, 'next_check_at': now + CHECK_INTERVAL}))
        s.commit()
        return result.rowcount == 1


def maintain(now: float | None = None) -> dict:
    """Bring at most three underserved-interest collectors forward per profile per check.

    No external work runs here. Existing leases, slots, backoff, manual schedules,
    disabled agents and user quality decisions remain authoritative.
    """
    now = time.time() if now is None else now
    if not _claim(now):
        return {'checked': False}
    from .shelf import snapshot
    profiles = {}
    with session() as s:
        pids = s.exec(select(Curator.profile_id).where(Curator.enabled.is_(True)).distinct()).all()
        for pid in pids:
            if not hub_allowed(s, pid):
                continue
            inventory = snapshot(s, pid, now)['articles']
            low = {r['id']: r for r in inventory['interests'] if r['needs_refill']}
            # The overall ready/candidate buffer can need work even with healthy interests.
            if inventory['needs_refill'] and not low:
                low = {r['id']: r for r in inventory['interests']}
            # Saturated interests never get nudged; quiet ones keep their backed-off spacing.
            low = {cid: r for cid, r in low.items() if r.get('boost', True)}
            candidates = []
            for cid, interest in low.items():
                c = s.get(Curator, cid)
                if (not c or not c.enabled or c.kind != 'watch' or c.interval_hours <= 0
                        or c.last_status in ('running', 'queued', 'failed', 'partial') or c.error):
                    continue
                # Recheck the live row before changing any schedule. In-flight claims
                # are protected by the conditional UPDATE below as well.
                spacing = max(MIN_COLLECTOR_INTERVAL, interest.get('backoff_seconds') or 0)
                if c.last_run is not None and c.last_run > now - spacing:
                    continue
                # Most starved first: unseen stock against its share, then daily intake.
                fill = (interest.get('unseen_ready', interest['ready'])
                        / max(1, interest.get('unseen_target', interest['target'])))
                candidates.append((fill, c.last_run or 0, c))
            advanced = []
            for _, _, c in sorted(candidates, key=lambda row: row[:2]):
                if len(advanced) >= MAX_ADVANCED:
                    break
                if c.next_run <= now:
                    continue  # already due; the normal dispatcher owns it
                changed = s.exec(update(Curator).where(
                    Curator.id == c.id, Curator.enabled.is_(True), Curator.next_run == c.next_run,
                    Curator.last_status == c.last_status, Curator.kind == 'watch',
                    Curator.interval_hours == c.interval_hours, Curator.interval_hours > 0,
                    Curator.error == '', Curator.last_run == c.last_run).values(next_run=now))
                if changed.rowcount:
                    advanced.append(c.id)
            profiles[str(pid)] = {'checked_at': now, 'collectors_queued': advanced,
                                  'status': 'Refill scheduled' if advanced else
                                  'Shelf needs fresh coverage' if inventory['needs_refill'] else 'Shelf stocked'}
        s.commit()
        state = get_kv(KEY, {}, s=s)
        save_setting(KEY, {**state, 'profiles': profiles}, s=s)
        s.commit()
    return {'checked': True, 'profiles': profiles}


def tick() -> None:
    try:
        maintain()
    except Exception:
        log.exception('shelf maintenance check failed')
