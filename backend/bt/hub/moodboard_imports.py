"""Public Pinterest board-feed snapshots with bounded local image capture."""
import asyncio
import re
import secrets
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlalchemy import func

from ..access import require_hub, hub_allowed
from ..db import session
from . import moodboards, social_fetch
from .models import MoodImport, MoodBoard, MoodPin, MoodAsset

router = APIRouter(prefix='/hub/boards', tags=['Mood boards'])
LIMIT = 25


def board_url(value):
    try:
        p = urlsplit(value.strip())
        if (p.scheme != 'https' or p.hostname not in ('pinterest.com', 'www.pinterest.com')
                or p.username or p.password or p.port not in (None, 443)):
            raise ValueError()
        parts = p.path.strip('/').removesuffix('.rss').split('/')
        if len(parts) != 2 or any(not re.fullmatch(r'[\w-]{1,100}', v, re.ASCII) for v in parts):
            raise ValueError()
        if parts[0].lower() in ('pin', 'ideas', 'search', 'settings', 'business', 'login') or parts[1].lower() in ('feed', '_saved', '_created'):
            raise ValueError()
        return 'https://www.pinterest.com/' + '/'.join(parts)
    except (ValueError, AttributeError):
        raise ValueError('Paste a public Pinterest board link: https://www.pinterest.com/username/board-name/') from None


def pin_url(value):
    try:
        p = urlsplit(value)
        if (p.scheme == 'https' and p.hostname in ('pinterest.com', 'www.pinterest.com')
                and not p.username and not p.password and p.port in (None, 443)
                and re.fullmatch(r'/pin/[A-Za-z0-9_-]{1,150}/?', p.path)):
            return 'https://www.pinterest.com' + p.path.rstrip('/') + '/'
    except (ValueError, TypeError):
        pass
    return ''


class Description(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.image = ''
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img' and not self.image:
            value = dict(attrs).get('src', '')
            try:
                p = urlsplit(value)
                if (p.scheme == 'https' and p.hostname == 'i.pinimg.com' and p.port in (None, 443)
                        and not p.username and not p.password and re.fullmatch(r'/[A-Za-z0-9_/.-]+\.(?:jpg|jpeg|png|webp|gif)', p.path, re.I)):
                    self.image = p._replace(query='', fragment='').geturl()
            except ValueError:
                pass

    def handle_data(self, value):
        self.text.append(value)


def feed(url):
    raw = social_fetch.fetch(board_url(url) + '.rss', kinds=('application/rss+xml', 'application/xml', 'text/xml'), limit=1_000_000)
    # No DTD/entity expansion, even when a provider response is compromised.
    if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Unsupported Pinterest feed')
    root = ET.fromstring(raw)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('Pinterest did not return a public board feed')
    result, seen = [], set()
    for item in root.findall('./channel/item')[:LIMIT]:
        source = pin_url(item.findtext('link', ''))
        desc = Description()
        desc.feed(item.findtext('description', '')[:20000])
        if not source or not desc.image or source in seen:
            continue
        title = ' '.join((item.findtext('title', '') or ' '.join(desc.text)).split())[:500] or 'Pinterest image'
        result.append(dict(source_url=source, image_url=desc.image, title=title))
        seen.add(source)
    return result


def payload(job):
    return job.model_dump(exclude={'profile_id', 'lease', 'lease_until', 'processed', 'candidates', 'fetched'})


class ImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)


@router.post('/{bid}/pinterest')
def enqueue(bid: int, data: ImportRequest, pid: int = Depends(require_hub)):
    try:
        url = board_url(data.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        moodboards.owned(s, pid, bid)
        active = s.exec(select(MoodImport).where(MoodImport.profile_id == pid, MoodImport.status.in_(['queued', 'importing']))).first()
        if active:
            raise HTTPException(409, 'A Pinterest import is already preparing. Wait for it to finish.')
        if s.exec(select(func.count()).select_from(MoodPin).where(MoodPin.board_id == bid)).one() >= moodboards.MAX_PINS:
            raise HTTPException(409, 'This board has reached its image limit.')
        # Keep a bounded job history for each profile.
        old = s.exec(select(MoodImport).where(MoodImport.profile_id == pid).order_by(MoodImport.id.desc()).offset(19)).all()
        for row in old:
            s.delete(row)
        job = MoodImport(board_id=bid, profile_id=pid, url=url)
        s.add(job)
        s.commit()
        s.refresh(job)
        return {'import': payload(job)}


@router.get('/{bid}/pinterest')
def status(bid: int, pid: int = Depends(require_hub)):
    with session() as s:
        moodboards.owned(s, pid, bid)
        job = s.exec(select(MoodImport).where(MoodImport.profile_id == pid, MoodImport.board_id == bid).order_by(MoodImport.id.desc())).first()
        return {'import': payload(job) if job else None}


def run(jid, lease):
    with session() as s:
        job = s.get(MoodImport, jid)
        if not job or job.lease != lease:
            return
        url, bid, pid = job.url, job.board_id, job.profile_id
        fetched, items = job.fetched, job.candidates
    try:
        if not fetched:
            items = feed(url)
            with session() as s:
                job = s.get(MoodImport, jid)
                if not job or job.lease != lease:
                    return
                job.total = len(items)
                job.candidates, job.fetched = items, True
                s.add(job)
                s.commit()
        for item in items:
            with session() as s:
                job = s.get(MoodImport, jid)
                board = s.get(MoodBoard, bid)
                if not job or job.lease != lease or not board or board.profile_id != pid or not hub_allowed(s, pid):
                    return
                if item['source_url'] in job.processed:
                    continue
                duplicate = s.exec(select(MoodPin.id).where(MoodPin.board_id == bid, MoodPin.source_url == item['source_url'])).first()
            captured = None
            if not duplicate:
                try:
                    captured = moodboards.capture_image(item['image_url'])
                except Exception:
                    pass
            with session() as s:
                s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                job = s.get(MoodImport, jid)
                board = s.get(MoodBoard, bid)
                if not job or job.lease != lease or not board or board.profile_id != pid or not hub_allowed(s, pid):
                    return
                duplicate = s.exec(select(MoodPin.id).where(MoodPin.board_id == bid, MoodPin.source_url == item['source_url'])).first()
                count = s.exec(select(func.count()).select_from(MoodPin).where(MoodPin.board_id == bid)).one()
                if captured and not duplicate and count < moodboards.MAX_PINS:
                    data, mime, width, height = captured
                    pin = MoodPin(board_id=bid, profile_id=pid, **item, saved=True,
                                  media_type='gif' if mime == 'image/gif' else 'image', width=width, height=height,
                                  attribution='Imported from Pinterest', license='Rights retained by original creators')
                    s.add(pin)
                    s.flush()
                    s.add(MoodAsset(pin_id=pin.id, data=data, mime=mime))
                    job.imported += 1
                else:
                    job.skipped += 1
                job.processed = [*job.processed, item['source_url']]
                job.lease_until = time.time() + 900
                s.add(job)
                s.commit()
        error = '' if items else 'No image pins were available in this public feed. Private boards require an authenticated Pinterest connection.'
    except Exception:
        error = 'Pinterest could not provide this public board feed. Check the link and try later. Private boards cannot be imported through a public link.'
    with session() as s:
        job = s.get(MoodImport, jid)
        if job and job.lease == lease:
            job.status = 'error' if error else 'complete'
            job.error = error
            job.lease = ''
            job.lease_until = 0
            s.add(job)
            s.commit()


def dispatch_due():
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        jobs = s.exec(select(MoodImport).where((MoodImport.status == 'queued') | ((MoodImport.status == 'importing') & (MoodImport.lease_until < time.time()))).order_by(MoodImport.id)).all()
        job = None
        for candidate in jobs:
            board = s.get(MoodBoard, candidate.board_id)
            if not board or board.profile_id != candidate.profile_id or not hub_allowed(s, candidate.profile_id):
                candidate.status = 'error'
                candidate.error = 'Board or profile is no longer available.'
                candidate.lease = ''
                s.add(candidate)
            else:
                job = candidate
                break
        if not job:
            s.commit()
            return False
        job.status = 'importing'
        job.lease = secrets.token_hex(16)
        job.lease_until = time.time() + 900
        s.add(job)
        s.commit()
        jid, lease = job.id, job.lease
    run(jid, lease)
    return True


async def scheduler():
    while True:
        try:
            worked = await asyncio.to_thread(dispatch_due)
        except Exception:
            worked = False
        await asyncio.sleep(2 if worked else 10)
