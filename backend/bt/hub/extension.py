"""Browser extension API: right-click an image on any page and add it to a Mood Board.

The extension authenticates with a per-profile connection key (Bearer). Keys are created and revoked by
a signed-in, Hub-allowed profile on the Mood Board page; only their SHA-256 is stored. A key can list that
profile's boards, create one, and add images to them; nothing else. `/api/ext/v1/` is exempt from the household
passphrase cookie because the extension cannot hold it; the key is required instead.
"""
import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select

from ..access import hub_allowed, require_hub
from ..db import session
from ..models import Profile
from . import moodboards
from .models import ExtensionKey, MoodBoard, MoodPin

router = APIRouter(prefix='/ext', tags=['Browser extension'])
PREFIX = 'wcx_'
MAX_KEYS = 10


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_profile(authorization: str | None = Header(default=None)) -> int:
    token = (authorization or '').removeprefix('Bearer ').strip()
    if not token.startswith(PREFIX) or len(token) > 200:
        raise HTTPException(401, 'Connect the extension with a Hub key (Mood Board → Browser extension).')
    with session() as s:
        row = s.exec(select(ExtensionKey).where(ExtensionKey.key_hash == _hash(token))).first()
        if not row:
            raise HTTPException(401, 'This extension key was revoked or is unknown. Create a new one in The Hub.')
        if not hub_allowed(s, row.profile_id):
            raise HTTPException(403, 'This profile cannot use Mood Boards.')
        from ..accounts import is_open
        from ..models import Account, Profile
        if not is_open():  # keys die with a disabled account and on unassigned profiles
            p = s.get(Profile, row.profile_id)
            acc = s.get(Account, p.account_id) if p and p.account_id else None
            if not acc or acc.disabled:
                raise HTTPException(401, 'This extension key was revoked or is unknown. Create a new one in The Hub.')
        if time.time() - row.last_used > 300:
            row.last_used = time.time()
            s.add(row)
            s.commit()
        return row.profile_id


class KeyCreate(BaseModel):
    name: str = Field(default='Browser', max_length=60)


def key_json(row):
    return {'id': row.id, 'name': row.name, 'hint': row.hint, 'created_at': row.created_at, 'last_used': row.last_used or None}


@router.get('/keys')
def keys(pid: int = Depends(require_hub)):
    with session() as s:
        rows = s.exec(select(ExtensionKey).where(ExtensionKey.profile_id == pid).order_by(ExtensionKey.id.desc())).all()
        return {'keys': [key_json(r) for r in rows]}


@router.post('/keys')
def create_key(data: KeyCreate, pid: int = Depends(require_hub)):
    with session() as s:
        if s.exec(select(func.count()).select_from(ExtensionKey).where(ExtensionKey.profile_id == pid)).one() >= MAX_KEYS:
            raise HTTPException(409, f'Keep up to {MAX_KEYS} extension keys. Revoke an old one first.')
        token = PREFIX + secrets.token_urlsafe(32)
        row = ExtensionKey(profile_id=pid, name=data.name.strip() or 'Browser', key_hash=_hash(token), hint=token[-4:])
        s.add(row)
        s.commit()
        s.refresh(row)
        # The plain key is shown once; only its hash is kept.
        return {'key': token, 'info': key_json(row)}


@router.delete('/keys/{key_id}')
def revoke_key(key_id: int, pid: int = Depends(require_hub)):
    with session() as s:
        row = s.get(ExtensionKey, key_id)
        if not row or row.profile_id != pid:
            raise HTTPException(404, 'Key not found')
        s.delete(row)
        s.commit()
    return {'ok': True}


@router.get('/v1/boards')
def ext_boards(pid: int = Depends(key_profile)):
    with session() as s:
        profile = s.get(Profile, pid)
        rows = s.exec(select(MoodBoard).where(moodboards.accessible(pid)).order_by(MoodBoard.name)).all()
        counts = dict(s.exec(select(MoodPin.board_id, func.count()).where(MoodPin.board_id.in_([b.id for b in rows] or [0]),
                                                                          MoodPin.hidden == False)  # noqa: E712
                             .group_by(MoodPin.board_id)).all())
        return {'profile': profile.name if profile else '', 'boards': [{'id': b.id, 'name': b.name, 'pin_count': counts.get(b.id, 0)} for b in rows]}


class ExtBoardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    interests: str = Field(default='', max_length=1000)
    curate: bool = True


@router.post('/v1/boards')
def ext_create_board(data: ExtBoardCreate, pid: int = Depends(key_profile)):
    """Create a board from the right-click menu. Same name as an existing board → that board is reused."""
    name = data.name.strip()
    if not name:
        raise HTTPException(422, 'Give the board a name.')
    with session() as s:
        same = s.exec(select(MoodBoard).where(MoodBoard.profile_id == pid, func.lower(MoodBoard.name) == name.lower())).first()
        if same:
            return {'board': {'id': same.id, 'name': same.name}, 'created': False}
    board = moodboards.create(moodboards.BoardCreate(name=name, interests=data.interests.strip() or name,
                                                     enabled=data.curate), pid=pid)['board']
    return {'board': {'id': board['id'], 'name': board['name']}, 'created': True}


@router.post('/v1/boards/{bid}/images')
def ext_add_image(bid: int, data: moodboards.ImageInput, pid: int = Depends(key_profile)):
    pin = moodboards.add_image_for(pid, bid, data)['pin']
    with session() as s:
        board = s.get(MoodBoard, bid)
        return {'ok': True, 'pin_id': pin['id'], 'board': board.name if board else '', 'title': pin['title']}
