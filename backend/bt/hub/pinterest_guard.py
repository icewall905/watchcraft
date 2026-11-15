"""Opt-in, slow and honest access to Pinterest search, with durable budgets and auto-pause.

Pinterest's robots.txt disallows non-allowlisted bots and its terms forbid scraping; the owner
opted in knowingly (Settings → Search, off by default). This module keeps that access small:

* every request uses the normal WatchCraft User-Agent through ``social_fetch`` (per-host lock,
  Retry-After cooldowns) - no cookies, spoofed fingerprints, proxies or captcha solving;
* requests are spaced by a random 12-30 s and capped per 10 minutes (curator), hour and day,
  shared across processes through one settings row;
* the first sign of a block (401/403/429/503, an HTML/login/captcha page where JSON was
  expected, or an unsuccessful resource response) pauses Pinterest for 24 h (48 h when it
  repeats), switches the source off and records why, so Settings can tell the owner.
"""
import random
import threading
import time

from .. import curation
from ..db import emit, get_settings, save_setting, session
from ..models import Setting
from . import social_fetch

KEY = 'pinterest_guard'
SPACING = (12.0, 30.0)
CURATOR_WINDOW, CURATOR_PER_WINDOW = 600, 2
HOUR_BUDGET, DAY_BUDGET, IMAGE_DAY_BUDGET = 15, 80, 400
LIVE_MAX_WAIT = 4.0  # A live search waits at most this long for its slot; otherwise it reports a retry time.
BLOCK_S, REPEAT_BLOCK_S = 86_400, 172_800
BLOCK_STATUSES = (401, 403, 429, 503)
_lock = threading.Lock()


class Paused(ValueError):
    """Pinterest is not asked right now: a budget, spacing or block pause, with a retry time."""
    def __init__(self, message, retry_after=0, blocked=False):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.blocked = blocked


def _load(s):
    row = s.get(Setting, KEY)
    state = dict(row.value) if row and isinstance(row.value, dict) else {}
    state.setdefault('requests', [])
    state.setdefault('curator', [])
    state.setdefault('images', [])
    return state


def _store(s, state):
    row = s.get(Setting, KEY) or Setting(key=KEY)
    row.value = state
    s.add(row)
    s.commit()


def _update(fn):
    """Atomic read-modify-write of the shared state (API and worker processes share it)."""
    with _lock, session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        state = _load(s)
        result = fn(state)
        _store(s, state)
        return result


def _prune(state, now):
    state['requests'] = [t for t in state['requests'] if now - t < 86_400][-DAY_BUDGET * 2:]
    state['curator'] = [t for t in state['curator'] if now - t < CURATOR_WINDOW]
    state['images'] = [t for t in state['images'] if now - t < 86_400][-IMAGE_DAY_BUDGET * 2:]


def status(now=None):
    now = now or time.time()
    with session() as s:
        state = _load(s)
    _prune(state, now)
    hour = [t for t in state['requests'] if now - t < 3600]
    blocked_until = float(state.get('blocked_until') or 0)
    return {'enabled': bool(get_settings().get('moodboard_sources', {}).get('pinterest')),
            'blocked': blocked_until > now, 'blocked_until': int(blocked_until) if blocked_until > now else 0,
            'reason': state.get('reason', '') if blocked_until > now else '',
            'last_block_at': int(state.get('blocked_at') or 0), 'last_reason': state.get('reason', ''),
            'hour_left': max(0, HOUR_BUDGET - len(hour)), 'day_left': max(0, DAY_BUDGET - len(state['requests'])),
            'images_left': max(0, IMAGE_DAY_BUDGET - len(state['images']))}


def blocked(now=None):
    now = now or time.time()
    with session() as s:
        return float(_load(s).get('blocked_until') or 0) > now


def _reserve(origin):
    def take(state):
        now = time.time()
        _prune(state, now)
        until = float(state.get('blocked_until') or 0)
        if until > now:
            raise Paused('Pinterest paused itself after a block: ' + str(state.get('reason') or 'blocked'),
                         until - now, blocked=True)
        hour = [t for t in state['requests'] if now - t < 3600]
        if len(state['requests']) >= DAY_BUDGET:
            raise Paused('Pinterest daily request budget used up.', 86_400 - (now - state['requests'][-DAY_BUDGET]))
        if len(hour) >= HOUR_BUDGET:
            raise Paused('Pinterest hourly request budget used up.', 3600 - (now - hour[-HOUR_BUDGET]))
        if origin == 'curator' and len(state['curator']) >= CURATOR_PER_WINDOW:
            raise Paused('Pinterest curator budget for this run used up.', CURATOR_WINDOW - (now - state['curator'][0]))
        wait = float(state.get('next_at') or 0) - now
        if wait > (LIVE_MAX_WAIT if origin == 'live' else 0):
            raise Paused('Pinterest requests are spaced out. Try again shortly.', wait)
        slot = now + max(0.0, wait)
        state['requests'].append(slot)
        if origin == 'curator':
            state['curator'].append(slot)
        state['next_at'] = slot + random.uniform(*SPACING)
        return max(0.0, wait)
    return _update(take)


def block(reason):
    """Record a block: pause, switch the source off, tell the owner once."""
    def mark(state):
        now = time.time()
        repeat = now - float(state.get('blocked_at') or 0) < 7 * 86_400
        state.update(blocked_until=now + (REPEAT_BLOCK_S if repeat else BLOCK_S), blocked_at=now, reason=reason[:200])
    _update(mark)
    sources = dict(get_settings().get('moodboard_sources') or {})
    if sources.get('pinterest'):
        sources['pinterest'] = False
        save_setting('moodboard_sources', sources)
    try:
        emit('pinterest', f'Pinterest search paused itself and was switched off: {reason[:200]}', level='warn')
    except Exception:
        pass


def fetch(url, origin='curator', kinds=('application/json',), limit=2_000_000):
    """One guarded pinterest.com request. Raises Paused (budget/spacing/block) or the fetch error."""
    wait = _reserve(origin)
    if wait:
        time.sleep(wait)
    try:
        return social_fetch.fetch(url, kinds=kinds, limit=limit)
    except curation.FetchError as exc:
        if exc.status in BLOCK_STATUSES:
            block(f'HTTP {exc.status} from Pinterest')
            raise Paused('Pinterest refused the request and is now paused.', BLOCK_S, blocked=True) from None
        raise
    except ValueError as exc:
        text = str(exc).lower()
        if 'content type' in text:  # A login, consent or captcha page instead of JSON.
            block('Pinterest answered with a web page (login or captcha) instead of results')
            raise Paused('Pinterest asked for a login or captcha and is now paused.', BLOCK_S, blocked=True) from None
        if 'requested a pause' in text:
            raise Paused('Pinterest asked WatchCraft to slow down.', 300) from None
        raise


def take_image():
    """Count one i.pinimg.com capture against the daily image budget."""
    def take(state):
        now = time.time()
        _prune(state, now)
        if len(state['images']) >= IMAGE_DAY_BUDGET:
            raise Paused('Pinterest daily image budget used up.', 86_400 - (now - state['images'][-IMAGE_DAY_BUDGET]))
        state['images'].append(now)
    _update(take)

