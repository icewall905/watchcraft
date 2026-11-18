"""Front Page pre-building: each profile's page overview and its picture previews are made ahead of
time by the hub scheduler, so opening Today never waits for them."""
import logging
import time

from sqlmodel import select

from ..db import save_setting, session
from ..models import Profile
from . import thumbs
from .models import Edition

log = logging.getLogger(__name__)
EVERY = 5 * 60
_last = 0.0


def build(pid: int) -> dict:
    from .master import edition
    from .master_api import build_overview
    data = build_overview(pid)
    save_setting(f'frontpage_overview:{pid}', data)
    ids = [p['item_id'] for page in data['pages'].values() for p in page['previews'] if p['has_image']]
    ids += [g['image_item_id'] for page in data['pages'].values() for g in page['groups'] if g['image_item_id']]
    with session() as s:
        thumbs.warm(s, ids, 480)
        ed = s.exec(select(Edition).where(Edition.profile_id == pid, Edition.day == edition.today(time.time()))).first()
        lead = [e['item_id'] for sec in (ed.sections if ed else []) if sec.get('key') == 'lead' for e in sec.get('entries', [])[:1]]
        thumbs.warm(s, lead, 800)
    return data


def tick(now: float | None = None) -> None:
    """Rebuild every profile's Front Page snapshot at most every EVERY seconds."""
    global _last
    now = now or time.time()
    if now - _last < EVERY:
        return
    _last = now
    with session() as s:
        pids = [p.id for p in s.exec(select(Profile)).all()]
    for pid in pids:
        try:
            build(pid)
        except Exception:  # noqa: BLE001 — one profile never blocks the others
            log.exception('front page snapshot failed for profile %s', pid)
