"""AI source discovery: propose communities for a thread's interests, adopt only ones that answer."""
import logging
import time

from sqlmodel import select

from ..db import session
from ..hub import ai
from . import store
from .adapters import ADAPTERS
from .models import CRThread, CRThreadSource

log = logging.getLogger(__name__)
EVERY_S = 7 * 86400
MAX_AI_SOURCES = 16
SCHEMA = {'type': 'object', 'properties': {'sources': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'platform': {'type': 'string'}, 'locator': {'type': 'string'}}, 'required': ['platform', 'locator']}}},
    'required': ['sources']}


def propose(thread: CRThread) -> list[tuple[str, str]]:
    platforms = [a for a in ADAPTERS.values() if a.discoverable]
    if not ai.enabled() or not platforms:
        return []
    listing = '\n'.join(f'- {a.key}: {a.label}, locator like {a.example!r}' for a in platforms)
    system = ('You find the best online communities for a person\'s interest. Suggest up to 20 real, active '
              'communities that exist today across these platforms, most valuable first. Use exactly the locator '
              'format shown. Prefer focused, high-quality communities over huge generic ones.'
              + (' The reader is a child: only suggest communities suitable for 10-12 year olds.' if thread.child_friendly else ''))
    user = (f'Platforms:\n{listing}\n\n<data>\n' +
            ai._esc(f'Thread: {thread.name}\nInterests: {thread.interests}\nInstructions: {thread.instructions}') + '\n</data>')
    try:
        res = ai._call(system, user, SCHEMA, max_tokens=1200, role='worker')
    except Exception as e:  # noqa: BLE001
        log.info('Common Room discovery failed: %s', e)
        return []
    out = []
    for row in res.get('sources', []) if isinstance(res, dict) else []:
        if isinstance(row, dict) and row.get('platform') in ADAPTERS:
            out.append((str(row['platform']), str(row.get('locator') or '')[:300]))
    return out


def validate(platform: str, locator: str) -> tuple[str, list[dict]]:
    """(normalized locator, first page) if the community exists and has posts; raises ValueError otherwise."""
    adapter = ADAPTERS[platform]
    loc = adapter.parse(locator)
    page = adapter.fetch(loc, adapter.sorts[0], '')
    if not page.posts:
        raise ValueError('That community has no posts.')
    return loc, page.posts


def run(thread: CRThread, lease: str = '') -> int:
    with session() as s:
        have = len(s.exec(select(CRThreadSource).where(CRThreadSource.thread_id == thread.id,
                                                       CRThreadSource.origin == 'ai')).all())
    adopted = 0
    for platform, locator in propose(thread):
        if have + adopted >= MAX_AI_SOURCES:
            break
        try:
            loc, posts = validate(platform, locator)
        except Exception as e:  # noqa: BLE001 — hallucinated or dead community
            log.info('Common Room discovery dropped %s %s: %s', platform, locator, e)
            continue
        sid = store.ensure_source(platform, loc)
        store.upsert_posts(posts, source_id=sid)
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            live = s.get(CRThread, thread.id)
            if not live or (lease and live.lease != lease):
                s.rollback()
                return adopted
            if not s.exec(select(CRThreadSource).where(CRThreadSource.thread_id == thread.id,
                                                       CRThreadSource.source_id == sid)).first():
                s.add(CRThreadSource(thread_id=thread.id, source_id=sid, origin='ai'))
                adopted += 1
            s.commit()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        t = s.get(CRThread, thread.id)
        if t:
            t.discovered_at = time.time()
            s.add(t)
        s.commit()
    return adopted
