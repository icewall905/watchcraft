"""Reddit app-only OAuth (client_credentials) for Common Room. Never logs the secret or token."""
from __future__ import annotations

import threading
import time

import httpx

from ..db import get_settings

TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
USER_AGENT = 'WatchCraft/0.2 (+https://github.com/icewall905/watchcraft)'
MIN_GAP_S = 0.7

_lock = threading.Lock()
_token = ''
_expires = 0.0
_key = ('', '')  # credentials the cached token belongs to
_pace_lock = threading.Lock()
_last = 0.0
_blocked_until = 0.0


def _creds() -> tuple[str, str]:
    cfg = get_settings().get('commonroom') or {}
    return (cfg.get('reddit_client_id') or '').strip(), (cfg.get('reddit_secret') or '').strip()


def configured() -> bool:
    cid, secret = _creds()
    return bool(cid and secret)


def _request_token(cid: str, secret: str) -> dict:
    r = httpx.post(TOKEN_URL, auth=(cid, secret), data={'grant_type': 'client_credentials'},
                   headers={'User-Agent': USER_AGENT}, timeout=15, follow_redirects=False)
    if r.status_code == 429:
        raise ValueError('Reddit asked us to slow down; try again later.')
    if r.status_code != 200:
        raise ValueError(f'Reddit rejected the API credentials (HTTP {r.status_code}).')
    data = r.json()
    if not isinstance(data, dict) or not data.get('access_token'):
        raise ValueError('Reddit rejected the API credentials.')
    return data


def token() -> str:
    global _token, _expires, _key
    cid, secret = _creds()
    if not (cid and secret):
        raise ValueError('Reddit API credentials are not configured.')
    with _lock:
        if _token and _key == (cid, secret) and time.monotonic() < _expires:
            return _token
        data = _request_token(cid, secret)
        _token = str(data['access_token'])
        _key = (cid, secret)
        _expires = time.monotonic() + max(float(data.get('expires_in') or 3600) - 60, 30)
        return _token


def headers() -> dict:
    return {'Authorization': f'bearer {token()}', 'User-Agent': USER_AGENT}


def pace() -> None:
    """Process-wide: at most one request per MIN_GAP_S; stops callers while a 429 cooldown lasts."""
    global _last
    with _pace_lock:
        now = time.monotonic()
        if now < _blocked_until:
            raise ValueError(f'Reddit asked us to slow down; trying again in {int(_blocked_until - now) + 1}s.')
        slot = max(now, _last + MIN_GAP_S)
        _last = slot
    if slot > now:
        time.sleep(slot - now)


def rate_limited(exc: Exception) -> ValueError | None:
    """If exc is a 429, start a cooldown from retry_after and return a ValueError for callers to raise."""
    global _blocked_until
    if getattr(exc, 'status', None) != 429:
        return None
    ra = str(getattr(exc, 'retry_after', '') or '')
    secs = int(ra) if ra.isdigit() else 60
    with _pace_lock:
        _blocked_until = max(_blocked_until, time.monotonic() + min(max(secs, 30), 3600))
    return ValueError('Reddit asked us to slow down; trying again later.')


def drop_token() -> None:
    """Forget the cached token only (e.g. after a 401); the cooldown stays."""
    global _token, _expires
    with _lock:
        _token, _expires = '', 0.0


def reset() -> None:
    """Forget cached token and cooldown (tests, credential changes)."""
    global _token, _expires, _last, _blocked_until
    with _lock:
        _token, _expires = '', 0.0
    with _pace_lock:
        _last = _blocked_until = 0.0
