"""Prepare the daily edition, overview and images away from visitor requests."""
import asyncio
import logging
import threading
import time

from sqlmodel import select

from ..db import get_settings, save_setting, session
from ..models import Profile
from . import ai, thumbs
from .models import MasterQueue

log = logging.getLogger(__name__)
EVERY = 5 * 60
PROSE_EVERY = 15 * 60
_last = 0.0
_requests: set[int] = set()
_request_lock = threading.Lock()
_last_prose: dict[int, float] = {}


def request_refresh(pid: int) -> None:
    with _request_lock:
        _requests.add(pid)


def build(pid: int) -> dict:
    from .master import edition, schedule, views
    from .master_api import build_overview

    with session() as s:
        if s.get(Profile, pid) is None:
            return {}
        ed = edition.patch(s, pid)
        lead = [e['item_id'] for sec in ed.sections if sec.get('key') == 'lead' for e in sec.get('entries', [])[:1]]
        now = time.time()
        need_prose = (lead and not ed.intro and not views.child(pid) and s.get(MasterQueue, pid) is None
                      and now - _last_prose.get(pid, 0) >= PROSE_EVERY)
    if need_prose and get_settings()['hub_master'].get('enabled', True) and ai.enabled():
        _last_prose[pid] = now
        schedule.mark(pid)

    data = build_overview(pid)
    save_setting(f'frontpage_overview:{pid}', data)
    ids = [p['item_id'] for page in data['pages'].values() for p in page['previews'] if p['has_image']]
    ids += [g['image_item_id'] for page in data['pages'].values() for g in page['groups'] if g['image_item_id']]
    with session() as s:
        thumbs.warm(s, ids, 480)
        thumbs.warm(s, lead, 800)
    return data


def tick() -> None:
    global _last
    now = time.time()
    with _request_lock:
        pids = set(_requests)
        _requests.clear()
    if now - _last >= EVERY:
        _last = now
        with session() as s:
            pids.update(s.exec(select(Profile.id)).all())
    for pid in sorted(pids):
        try:
            build(pid)
        except Exception:  # noqa: BLE001 — one profile never blocks the others
            log.exception('front page snapshot failed for profile %s', pid)


async def scheduler() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            log.exception('front page scheduler failed')
        await asyncio.sleep(10)
