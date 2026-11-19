"""Profile-scoped Firebase delivery. Watch history is the durable notification queue."""
import asyncio
import logging
import os
import time
from functools import lru_cache
from uuid import uuid4

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field as Input
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


class DeviceInput(BaseModel):
    token: str = Input(min_length=20, max_length=4096, pattern=r'^[A-Za-z0-9:_-]+$')


def project():
    return os.environ.get('BT_WATCHER_FCM_PROJECT', '').strip()


@router.get('/android')
def status(pid: int = Depends(require_grown_up)):
    return {'configured': bool(project())}


@router.put('/android')
def register(body: DeviceInput, pid: int = Depends(require_grown_up)):
    if not project():
        raise HTTPException(503, 'Android push is unavailable on this Hub.')
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        device = s.get(WatchDevice, body.token)
        if device and device.profile_id == pid:
            return {'enabled': True}
        if len(s.exec(select(WatchDevice.token).where(WatchDevice.profile_id == pid).limit(10)).all()) >= 10:
            raise HTTPException(409, 'This profile already has ten Android registrations.')
        latest = s.exec(select(WatchEvent.id).where(WatchEvent.profile_id == pid)
                        .order_by(WatchEvent.id.desc()).limit(1)).first()
        s.merge(WatchDevice(token=body.token, profile_id=pid, cursor=latest or 0))
        s.commit()
    return {'enabled': True}


@router.delete('/android')
def unregister(body: DeviceInput, pid: int = Depends(require_grown_up)):
    with session() as s:
        s.execute(delete(WatchDevice).where(WatchDevice.token == body.token, WatchDevice.profile_id == pid))
        s.commit()
    return {'enabled': False}


@lru_cache(maxsize=1)
def credentials():
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/firebase.messaging'])
    return creds, Request()


def send(device, event_id):
    creds, transport = credentials()
    headers = {}
    creds.before_request(transport, 'POST', '', headers)
    # A shared phone or lock screen should never show a tracking number or watch contents.
    response = httpx.post(
        f'https://fcm.googleapis.com/v1/projects/{project()}/messages:send',
        headers=headers, timeout=15,
        json={'message': {'token': device.token, 'data': {'watcher_update': str(event_id)},
                          'android': {'priority': 'HIGH', 'ttl': '3600s', 'collapse_key': 'watcher'}}})
    if response.status_code == 404 and any(
        detail.get('errorCode') == 'UNREGISTERED'
        for detail in response.json().get('error', {}).get('details', [])
    ):
        return False
    response.raise_for_status()
    return True


def dispatch_one():
    if not project():
        return False
    now = time.time()
    claim = uuid4().hex
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        pair = s.exec(select(WatchDevice, WatchEvent.id).join(
            WatchEvent, (WatchEvent.profile_id == WatchDevice.profile_id) & (WatchEvent.id > WatchDevice.cursor)
        ).where(WatchDevice.next_attempt <= now).order_by(WatchEvent.id).limit(1)).first()
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
