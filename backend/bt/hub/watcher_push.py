"""Profile-scoped ntfy delivery. Watch history is the durable notification queue."""
import asyncio
import logging
import os
import time
from secrets import token_hex
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import Depends, HTTPException
from sqlalchemy import delete, update
from sqlmodel import Field, SQLModel, select

from ..access import hub_allowed, require_grown_up
from ..db import session
from .personal_watch import WatchEvent, router

log = logging.getLogger(__name__)


class WatchDevice(SQLModel, table=True):
    token: str = Field(primary_key=True)
    profile_id: int = Field(index=True)
    cursor: int
    next_attempt: float = Field(default=0, index=True)
    claim: str = ''


def endpoint():
    value = os.environ.get('BT_WATCHER_NTFY_URL', '').strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme == 'https' and parsed.hostname and not (
        parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
    ):
        return value
    return ''


@router.get('/android')
def status(pid: int = Depends(require_grown_up)):
    return {'configured': bool(endpoint())}


@router.post('/android')
def register(pid: int = Depends(require_grown_up)):
    base = endpoint()
    if not base:
        raise HTTPException(503, 'Android notifications are unavailable on this Hub.')
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        device = s.exec(select(WatchDevice).where(
            WatchDevice.profile_id == pid, WatchDevice.token.startswith('wc_', autoescape=True)
        )).first()
        if device is None:
            latest = s.exec(select(WatchEvent.id).where(WatchEvent.profile_id == pid)
                            .order_by(WatchEvent.id.desc()).limit(1)).first()
            device = WatchDevice(token='wc_' + token_hex(30), profile_id=pid, cursor=latest or 0)
            s.add(device)
            s.commit()
        return {'endpoint': f'{base}/{device.token}', 'since': int(time.time())}


@router.delete('/android')
def unregister(pid: int = Depends(require_grown_up)):
    with session() as s:
        s.execute(delete(WatchDevice).where(WatchDevice.profile_id == pid))
        s.commit()
    return {'enabled': False}


def send(device, event_id):
    token = os.environ.get('BT_WATCHER_NTFY_TOKEN', '')
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    response = httpx.post(endpoint(), headers=headers, timeout=15, follow_redirects=False,
        json={'topic': device.token, 'title': 'The Watcher', 'message': 'You have a new Watcher update.',
              'priority': 4, 'tags': [f'watcher-{event_id}']})
    response.raise_for_status()
    return True


def dispatch_one():
    if not endpoint():
        return False
    now = time.time()
    claim = uuid4().hex
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        pair = s.exec(select(WatchDevice, WatchEvent.id).join(
            WatchEvent, (WatchEvent.profile_id == WatchDevice.profile_id) & (WatchEvent.id > WatchDevice.cursor)
        ).where(WatchDevice.next_attempt <= now, WatchDevice.token.startswith('wc_', autoescape=True)).order_by(WatchEvent.id).limit(1)).first()
        if not pair:
            return False
        device, event_id = pair
        if not hub_allowed(s, device.profile_id):
            s.delete(device)
            s.commit()
            return True
        device.claim, device.next_attempt = claim, now + 300
        s.add(device)
        s.commit()

    keep, values = True, {'cursor': event_id, 'next_attempt': 0, 'claim': ''}
    try:
        keep = send(device, event_id)
    except Exception as exc:
        log.warning('Watcher Android delivery failed (%s)', type(exc).__name__)
        values = {'next_attempt': time.time() + 60, 'claim': ''}
    with session() as s:
        filt = (WatchDevice.token == device.token, WatchDevice.claim == claim)
        s.execute(update(WatchDevice).where(*filt).values(**values) if keep else delete(WatchDevice).where(*filt))
        s.commit()
    return True


async def scheduler():
    while True:
        try:
            worked = await asyncio.to_thread(dispatch_one)
        except Exception:
            log.exception('Watcher push scheduler failed')
            worked = False
        await asyncio.sleep(0 if worked else 5)
