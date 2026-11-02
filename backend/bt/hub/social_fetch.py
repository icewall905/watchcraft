"""Serialize public social requests per host and respect server retry delays.

Shared feed polling supplies the durable endpoint cache. These process-local host
budgets additionally prevent overlapping collectors from hammering one instance.
"""
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .. import curation

_guard = threading.Lock()
_hosts = {}


def fetch(url, **kwargs):
    host = urlsplit(url).hostname
    with _guard:
        # Bound idle host state without evicting active cooldowns.
        if len(_hosts) >= 256:
            for key, state in list(_hosts.items()):
                if state[1] <= time.monotonic() and not state[0].locked():
                    del _hosts[key]
        if host not in _hosts:
            if len(_hosts) >= 256:
                raise ValueError('Social source host budget is full. Try later.')
            _hosts[host] = [threading.Lock(), 0.0]
        state = _hosts[host]
    with state[0]:
        remaining = state[1] - time.monotonic()
        if remaining > 2:
            raise ValueError(f'This social instance requested a pause. Retry in {int(remaining) + 1} seconds.')
        if remaining > 0:
            time.sleep(remaining)
        try:
            return curation.fetch(url, **kwargs)
        except curation.FetchError as exc:
            if exc.status in (429, 503):
                delay = 300.0
                try:
                    delay = float(exc.retry_after)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(exc.retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        pass
                state[1] = time.monotonic() + max(30, min(delay, 86400))
            raise
        finally:
            state[1] = max(state[1], time.monotonic() + 1)
