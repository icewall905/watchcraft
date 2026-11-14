"""Profile-owned visual boards with independent, bounded image curators."""
import asyncio
import json
import secrets
import re
import time
from io import BytesIO
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, update
from sqlmodel import select

from .. import curation
from ..access import hub_allowed, require_hub
from ..db import session
from . import ai
from .models import MoodAsset, MoodBoard, MoodPin

router = APIRouter(prefix='/hub/boards', tags=['Mood boards'])
IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/gif', 'image/webp')
PAGE_SIZE = 40
MAX_PINS = 1000


class NotesUnavailable(ValueError):
    pass


class BoardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    interests: str = Field(min_length=1, max_length=1000)
    instructions: str = Field(default='', max_length=2000)
    enabled: bool = True
    interval_hours: int = Field(default=12, ge=1, le=168)


class BoardEdit(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    interests: str | None = Field(default=None, min_length=1, max_length=1000)
    instructions: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)


class PinEdit(BaseModel):
    saved: bool | None = None
    hidden: bool | None = None
    board_id: int | None = None


def owned(s, pid, bid):
    board = s.get(MoodBoard, bid)
    if not board or board.profile_id != pid:
        raise HTTPException(404, 'Board not found')
    return board


def board_json(s, board):
    result = board.model_dump(exclude={'lease', 'build_started', 'revision', 'source_offset'})
    result['pin_count'] = s.exec(select(func.count()).select_from(MoodPin).where(
        MoodPin.board_id == board.id, MoodPin.hidden == False)).one()
    result['cover_url'] = None
    pin = s.exec(select(MoodPin).where(MoodPin.board_id == board.id, MoodPin.hidden == False)
                 .order_by(MoodPin.id.desc()).limit(1)).first()
    if pin:
        result['cover_url'] = f'/api/hub/boards/{board.id}/pins/{pin.id}/media'
    return result


def pin_json(pin):
    result = pin.model_dump(exclude={'image_url'})
    result['image_url'] = f'/api/hub/boards/{pin.board_id}/pins/{pin.id}/media'
    return result


@router.get('')
def boards(pid: int = Depends(require_hub)):
    with session() as s:
        return {'boards': [board_json(s, b) for b in s.exec(select(MoodBoard).where(
            MoodBoard.profile_id == pid).order_by(MoodBoard.id.desc())).all()]}


@router.post('')
def create(data: BoardCreate, pid: int = Depends(require_hub)):
    values = data.model_dump()
    for key in ('name', 'interests', 'instructions'):
        values[key] = values[key].strip()
    if not values['name'] or not any(v.strip() for v in re.split(r'[,;\n]+', values['interests'])):
        raise HTTPException(422, 'Name and interests are required')
    with session() as s:
        if s.exec(select(func.count()).select_from(MoodBoard).where(MoodBoard.profile_id == pid)).one() >= 50:
            raise HTTPException(409, 'Keep up to 50 boards per profile')
        board = MoodBoard(profile_id=pid, **values)
        s.add(board)
        s.commit()
        s.refresh(board)
        return {'board': board_json(s, board)}


@router.get('/{bid}')
def detail(bid: int, cursor: int | None = None, saved: bool = False,
           pid: int = Depends(require_hub)):
    with session() as s:
        board = owned(s, pid, bid)
        query = select(MoodPin).where(MoodPin.board_id == bid, MoodPin.hidden == False)
        if cursor is not None:
            query = query.where(MoodPin.id < cursor)
        if saved:
            query = query.where(MoodPin.saved == True)
        pins = s.exec(query.order_by(MoodPin.id.desc()).limit(PAGE_SIZE + 1)).all()
        more = len(pins) > PAGE_SIZE
        pins = pins[:PAGE_SIZE]
        return {'board': board_json(s, board), 'pins': [pin_json(p) for p in pins],
                'next_cursor': str(pins[-1].id) if more else None, 'has_more': more}


@router.patch('/{bid}')
def edit(bid: int, data: BoardEdit, pid: int = Depends(require_hub)):
    with session() as s:
        board = owned(s, pid, bid)
        changes = data.model_dump(exclude_unset=True)
        for key, value in changes.items():
            if value is None:
                raise HTTPException(422, 'Board fields cannot be null')
            if isinstance(value, str):
                value = value.strip()
                if (key == 'name' and not value) or (key == 'interests' and not any(v.strip() for v in re.split(r'[,;\n]+', value))):
                    raise HTTPException(422, 'Name and interests are required')
            setattr(board, key, value)
        if 'interests' in changes:
            board.source_offset = 0
        board.revision += 1
        board.lease = ''
        board.build_started = 0
        board.next_run = 0
        s.add(board)
        s.commit()
        return {'board': board_json(s, board)}


def delete_profile(s, pid):
    """Called inside household deletion's existing transaction."""
    ids = select(MoodPin.id).where(MoodPin.profile_id == pid)
    s.exec(delete(MoodAsset).where(MoodAsset.pin_id.in_(ids)))
    s.exec(delete(MoodPin).where(MoodPin.profile_id == pid))
    s.exec(delete(MoodBoard).where(MoodBoard.profile_id == pid))


@router.delete('/{bid}')
def remove(bid: int, pid: int = Depends(require_hub)):
    with session() as s:
        owned(s, pid, bid)
        ids = select(MoodPin.id).where(MoodPin.board_id == bid)
        s.exec(delete(MoodAsset).where(MoodAsset.pin_id.in_(ids)))
        s.exec(delete(MoodPin).where(MoodPin.board_id == bid))
        s.exec(delete(MoodBoard).where(MoodBoard.id == bid))
        s.commit()
    return {'ok': True}


@router.post('/{bid}/refresh')
def refresh(bid: int, pid: int = Depends(require_hub)):
    with session() as s:
        board = owned(s, pid, bid)
        if board.lease or (board.last_run and time.time() - board.last_run < 300):
            return {'queued': False, 'status': 'Collecting' if board.lease else 'Try again in a few minutes'}
        board.next_run = 0
        board.last_status = 'queued'
        s.add(board)
        s.commit()
    return {'queued': True, 'status': 'queued'}


@router.patch('/{bid}/pins/{pin_id}')
def edit_pin(bid: int, pin_id: int, data: PinEdit, pid: int = Depends(require_hub)):
    with session() as s:
        owned(s, pid, bid)
        pin = s.get(MoodPin, pin_id)
        if not pin or pin.board_id != bid or pin.profile_id != pid:
            raise HTTPException(404, 'Pin not found')
        if data.board_id is not None and data.board_id != bid:
            owned(s, pid, data.board_id)
            if s.exec(select(MoodPin.id).where(MoodPin.board_id == data.board_id,
                                               MoodPin.source_url == pin.source_url)).first():
                raise HTTPException(409, 'This image is already on that board')
            if s.exec(select(func.count()).select_from(MoodPin).where(MoodPin.board_id == data.board_id)).one() >= MAX_PINS:
                raise HTTPException(409, 'The destination board has reached its image limit')
            pin.board_id = data.board_id
        for key in ('saved', 'hidden'):
            value = getattr(data, key)
            if value is not None:
                setattr(pin, key, value)
        s.add(pin)
        s.commit()
        return {'pin': pin_json(pin)}


@router.get('/{bid}/pins/{pin_id}/media')
def media(bid: int, pin_id: int, pid: int = Depends(require_hub)):
    with session() as s:
        owned(s, pid, bid)
        pin = s.get(MoodPin, pin_id)
        if not pin or pin.board_id != bid or pin.profile_id != pid:
            raise HTTPException(404, 'Pin not found')
        asset = s.get(MoodAsset, pin_id)
        if not asset:
            raise HTTPException(404, 'Image not available')
        return Response(asset.data, media_type=asset.mime,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


def capture_image(url):
    data, final, meta = curation.fetch(url, kinds=IMAGE_TYPES, limit=5_000_000, with_metadata=True)
    mime = meta['content_type']
    with Image.open(BytesIO(data)) as image:
        if image.format not in ('PNG', 'JPEG', 'GIF', 'WEBP') or image.width * image.height > 40_000_000:
            raise ValueError('Unsupported image')
        mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}[image.format]
        image.verify()
        width, height = image.size
    return data, mime, width, height



def select_candidates(candidates, interests, instructions):
    """Evaluate only supplied metadata; a model cannot add URLs or new pins."""
    if not instructions and not ai.enabled():
        return candidates
    if not ai.enabled():
        raise NotesUnavailable('notes unavailable')
    if not candidates:
        return []
    schema = {'type': 'object', 'properties': {'indices': {'type': 'array', 'maxItems': 30,
              'items': {'type': 'integer', 'minimum': 0, 'maximum': max(0, len(candidates)-1)}}},
              'required': ['indices'], 'additionalProperties': False}
    rows = [{'index': i, 'title': str(c.get('title', ''))[:500],
             'description': str(c.get('description', ''))[:1000]} for i, c in enumerate(candidates)]
    try:
        result = ai._call('Choose images for a personal hobby and interest mood board using the supplied '
                          'metadata. Apply the user interests and curator notes. Prefer visual inspiration '
                          'and relevant photographs/art/crafts. Exclude unrelated logos, screenshots, news '
                          'documents and explicit adult imagery. Return indices of suitable supplied images '
                          'only, in best-first order. Do not claim to see pixels; judge metadata only.',
                          'Interests: ' + ai._esc(interests) + '\nCurator notes: ' + ai._esc(instructions) +
                          '\n<data>' + ai._esc(json.dumps(rows)) + '</data>', schema, max_tokens=600, role='worker')
        indices = result.get('indices')
        if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(candidates) for i in indices):
            raise ValueError('Invalid candidate indices')
        return [candidates[i] for i in dict.fromkeys(indices)]
    except Exception:
        if instructions:
            raise NotesUnavailable('Curator notes could not be applied') from None
        return candidates

def collect(bid, lease, revision):
    """Network work stays outside transactions; lease+revision guards final writes."""
    from . import moodboard_sources
    with session() as s:
        board = s.get(MoodBoard, bid)
        if not board or board.lease != lease:
            return
        interests = [v.strip()[:250] for v in re.split(r'[,;\n]+', board.interests) if v.strip()]
        instructions = board.instructions
        offset = board.source_offset
        query = interests[(offset // 30) % len(interests)]
        page_offset = ((offset // (30 * len(interests))) * 30) % 300
        existing = set(s.exec(select(MoodPin.source_url).where(MoodPin.board_id == bid)).all())
    results, failures = [], 0
    error = ''
    try:
        candidates = moodboard_sources.search(query, limit=30, offset=page_offset)
        candidates = select_candidates(candidates, query, instructions)
        for item in candidates:
            if len(existing) >= MAX_PINS:
                break
            if item['source_url'] in existing:
                continue
            # Provider links remain constrained even if a future provider regresses.
            source = moodboard_sources._url(item['source_url'], 'commons.wikimedia.org', '/wiki/File:')
            image = (moodboard_sources._url(item['image_url'], 'upload.wikimedia.org', '/wikipedia/commons/')
                     or moodboard_sources._url(item['image_url'], 'thumb.wikimedia.org', '/wikipedia/commons/thumb/'))
            if not source or not image:
                continue
            item = {**item, 'source_url': source, 'image_url': image}
            try:
                data, mime, width, height = capture_image(item['image_url'])
                results.append((item, data, mime, width, height))
                existing.add(item['source_url'])
            except Exception:
                failures += 1
    except Exception as exc:
        error = ('Curator notes need an available AI model; try again or remove notes to collect by interest search.'
                 if isinstance(exc, NotesUnavailable) else 'Image source unavailable. Please try again later.')
    with session() as s:
        # Acquire SQLite writer lock before checking lease, preventing deletion/edit races.
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        board = s.get(MoodBoard, bid)
        if not board or board.lease != lease or board.revision != revision or not hub_allowed(s, board.profile_id):
            return
        for item, data, mime, width, height in results:
            if s.exec(select(MoodPin.id).where(MoodPin.board_id == bid, MoodPin.source_url == item['source_url'])).first():
                continue
            pin = MoodPin(board_id=bid, profile_id=board.profile_id, title=item['title'][:500],
                          source_url=item['source_url'], image_url=item['image_url'],
                          media_type='gif' if mime == 'image/gif' else 'image', width=width, height=height,
                          attribution=item.get('attribution', '')[:1000], license=item.get('license', '')[:200])
            s.add(pin)
            s.flush()
            s.add(MoodAsset(pin_id=pin.id, mime=mime, data=data))
        if not error:
            board.source_offset = (offset + 30) % (300 * len(interests))
        board.last_status = 'error' if error else ('partial' if failures else 'ok')
        board.error = error or ('This board has reached its 1,000-image limit. Move images to another board to make room.'
                               if len(existing) >= MAX_PINS else (f'{failures} images could not be captured.' if failures else ''))
        board.lease = ''
        board.build_started = 0
        s.add(board)
        s.commit()


def dispatch_due():
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        candidates = s.exec(select(MoodBoard).where(
            ((((MoodBoard.enabled == True) | (MoodBoard.last_status == 'queued')) &
              (MoodBoard.next_run <= now) & (MoodBoard.lease == '')) |
             ((MoodBoard.lease != '') & (MoodBoard.build_started < now - 1800))))
            .order_by(MoodBoard.next_run)).all()
        board = next((b for b in candidates if hub_allowed(s, b.profile_id)), None)
        if not board:
            return False
        lease = secrets.token_hex(16)
        board.lease = lease
        board.build_started = now
        board.last_run = now
        board.last_status = 'collecting'
        board.next_run = now + board.interval_hours * 3600
        s.add(board)
        s.commit()
        bid, revision = board.id, board.revision
    collect(bid, lease, revision)
    return True


async def scheduler():
    while True:
        try:
            worked = await asyncio.to_thread(dispatch_due)
        except Exception:
            worked = False
        await asyncio.sleep(2 if worked else 30)
