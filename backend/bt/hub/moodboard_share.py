"""Share Mood Board images: copy them to another household profile, or make a public link.

To a profile: the sender's visible images are copied (picture bytes included) into a board the recipient owns, so
profile isolation stays simple: nothing reads across profiles afterwards. A single image lands in the recipient's
"Shared with you" board; a whole board becomes a new, paused board. The recipient gets a `MoodShareNotice`.

Public link: an unguessable `wcs_` token, shown once; only its SHA-256 is stored (like `ExtensionKey`). Grown-up
profiles only. `/api/public/mood/` is exempt from the household passphrase (bt/auth.py) and serves exactly the
linked image or the board's visible images: titles, sizes and — only when the owner opted in — source links.
No profile data, notes, feedback, or listing. Every request re-checks the link, so revoking is immediate.
"""
import hashlib
import re
import secrets
import threading
import time
import ipaddress
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func
from sqlmodel import select

import logging

from ..access import boards_allowed, grown_up_access, hub_allowed, is_child, require_boards, require_hub
from ..db import session
from ..models import Profile
from .models import MoodAsset, MoodBoard, MoodBoardMember, MoodPin, MoodShareLink, MoodShareNotice
from .moodboards import MAX_PINS, accessible, member_ids, owned, pin_visible, profile_brief

log = logging.getLogger(__name__)


def _private(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(prefix='/hub/mood-share', tags=['Mood board sharing'], dependencies=[Depends(_private)])
public_router = APIRouter(prefix='/public/mood', tags=['Mood board sharing (public)'])

PREFIX = 'wcs_'
TOKEN_RE = re.compile(r'wcs_[A-Za-z0-9_-]{43}')
MAX_LINKS = 50
INBOX = 'Shared with you'
PAGE = 60
KID_CHECK_MAX = 40  # images checked per share to a child (vision model, in the request)
HEADERS = {'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow, noarchive',
           'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}
JSON_HEADERS = {**HEADERS, 'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _visible_pin(s, pid, bid, pin_id):
    pin = s.get(MoodPin, pin_id)
    if not pin or pin.board_id != bid or pin.hidden or not s.get(MoodAsset, pin_id):
        raise HTTPException(404, 'Image not found')
    return pin


# ---------- to a profile

@router.get('/targets')
def targets(pid: int = Depends(require_hub)):
    """Other household profiles that can receive Mood Board images, child profiles included."""
    with session() as s:
        rows = s.exec(select(Profile).where(Profile.id != pid).order_by(Profile.name)).all()
        # Accounts mode: a copy may go to any assigned profile on the server; the owner's name tells
        # same-named profiles ("Kids") apart. Unassigned profiles from before accounts are skipped.
        from ..accounts import is_open
        from ..models import Account
        names = {a.id: a.username for a in s.exec(select(Account)).all()}
        return {'profiles': [{'id': p.id, 'name': p.name, 'emoji': p.emoji, 'color': p.color, 'child': p.role == 'child',
                              'account': names.get(p.account_id)}
                             for p in rows if boards_allowed(s, p.id) and (is_open() or p.account_id in names)]}


class ToProfile(BaseModel):
    to: int
    pin_id: int | None = None


def _copy(s, pin, bid, recipient):
    """Copy one pin (with its picture) onto a recipient board. Returns True when a new image was added."""
    old = s.exec(select(MoodPin).where(MoodPin.board_id == bid, MoodPin.source_url == pin.source_url)).first()
    asset = s.get(MoodAsset, pin.id)
    if old:
        # Already there. A picture the recipient removed stays removed; one reshuffled away gets its picture back.
        if old.hidden or s.get(MoodAsset, old.id):
            return False
        s.add(MoodAsset(pin_id=old.id, mime=asset.mime, data=asset.data))
        return True
    copy = MoodPin(board_id=bid, profile_id=recipient, title=pin.title, source_url=pin.source_url,
                   image_url=pin.image_url, media_type=pin.media_type, width=pin.width, height=pin.height,
                   attribution=pin.attribution, license=pin.license, ai_flag=pin.ai_flag, saved=False)
    s.add(copy)
    s.flush()
    s.add(MoodAsset(pin_id=copy.id, mime=asset.mime, data=asset.data))
    return True


def _stored(s, bid):
    return s.exec(select(func.count()).select_from(MoodAsset).join(MoodPin, MoodPin.id == MoodAsset.pin_id)
                  .where(MoodPin.board_id == bid)).one()


def _kid_check(pictures: list[tuple[int, bytes]], unchecked: set[int] | None = None,
               reasons: dict[int, str] | None = None) -> set[int]:
    """Pin ids whose picture passes the kid-safe picture bar. Fail closed: errors approve nothing
    (their ids go into `unchecked` when given, so a re-check can tell "failed" from "not judged")."""
    from . import ai, kidsafe
    from .moodboards import vision_image
    if not pictures:
        return set()
    if not kidsafe._vision():
        raise HTTPException(409, 'Images for a child profile need a kid-safety check, and the vision model is off.')
    ok: set[int] = set()
    for start in range(0, len(pictures), kidsafe.IMAGE_BATCH):
        batch = pictures[start:start + kidsafe.IMAGE_BATCH]
        try:
            images = [vision_image(data) for _, data in batch]
            res = ai._call(kidsafe.SYSTEM + ' ' + kidsafe.IMAGE_RULE,
                           ai._data([{'type': 'image', 'title': 'Mood Board image'} for _ in batch]), kidsafe.SCHEMA,
                           max_tokens=600, images=images, role='worker')
        except Exception as e:  # noqa: BLE001 — unchecked pictures are not shared
            log.info('kid-safe share check failed: %s', e)
            if unchecked is not None:
                unchecked.update(i for i, _ in batch)
            continue
        for row in res.get('verdicts', []) if isinstance(res, dict) else []:
            n, safe, why = kidsafe.normalize(row)
            if n is None or not 1 <= n <= len(batch):
                continue
            if safe:
                ok.add(batch[n - 1][0])
            elif reasons is not None and why:
                reasons[batch[n - 1][0]] = why
    return ok


def _child_approved(bid: int, data: 'ToProfile', pid: int) -> tuple[set[int] | None, int]:
    """For a child recipient: (pin ids allowed through, pins held back). None means not a child.
    Every image is checked now, outside the write transaction (vision calls take seconds). A child-friendly
    board is not trusted: hand-added, extension, imported and moved images reach it without the child rule."""
    with session() as s:
        owned(s, pid, bid, manage=True)
        if not is_child(s, data.to):
            return None, 0
        if data.pin_id is not None:
            pins = [_visible_pin(s, pid, bid, data.pin_id)]
        else:
            pins = s.exec(select(MoodPin).join(MoodAsset, MoodAsset.pin_id == MoodPin.id)
                          .where(MoodPin.board_id == bid, MoodPin.hidden == False)  # noqa: E712
                          .order_by(MoodPin.id).limit(MAX_PINS)).all()
        ids = [p.id for p in pins]
        check = ids[:KID_CHECK_MAX]
        pictures = [(i, a.data) for i in check if (a := s.get(MoodAsset, i))]
    ok = _kid_check(pictures)
    return ok, len(ids) - len(ok)


@router.post('/boards/{bid}/to-profile')
def to_profile(bid: int, data: ToProfile, pid: int = Depends(require_hub)):
    with session() as s:
        recipient = s.get(Profile, data.to)
        from ..accounts import is_open
        if data.to == pid or not recipient or not boards_allowed(s, data.to) or \
                (not is_open() and recipient.account_id is None):
            raise HTTPException(404, 'That profile cannot receive Mood Board images.')
    approved, held = _child_approved(bid, data, pid)
    if approved is not None and not approved:
        raise HTTPException(409, f"{'This image' if data.pin_id is not None else 'None of these images'} passed the "
                                 f"kid-safety check, so nothing was sent to {recipient.name}.")
    child = approved is not None
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        board = owned(s, pid, bid, manage=True)
        recipient = s.get(Profile, data.to)
        if not recipient or not boards_allowed(s, data.to):
            raise HTTPException(404, 'That profile cannot receive Mood Board images.')
        sender = s.get(Profile, pid)
        if data.pin_id is not None:
            pins = [_visible_pin(s, pid, bid, data.pin_id)]
            target = s.exec(select(MoodBoard).where(MoodBoard.profile_id == data.to, MoodBoard.name == INBOX)
                            .order_by(MoodBoard.id)).first()
            if not target:
                target = MoodBoard(profile_id=data.to, name=INBOX, interests='Images other profiles shared with you',
                                   enabled=False, **({'child_friendly': True, 'strictness': 'strict'} if child else {}))
                s.add(target)
                s.flush()
            elif child and not target.child_friendly:
                target.child_friendly, target.strictness = True, 'strict'
                s.add(target)
            if _stored(s, target.id) >= MAX_PINS:
                raise HTTPException(409, f"{recipient.name}'s Shared with you board is full.")
        else:
            pins = s.exec(select(MoodPin).join(MoodAsset, MoodAsset.pin_id == MoodPin.id)
                          .where(MoodPin.board_id == bid, MoodPin.hidden == False)  # noqa: E712
                          .order_by(MoodPin.id).limit(MAX_PINS)).all()
            if not pins:
                raise HTTPException(409, 'This board has no images to share yet.')
            if child:
                pins = [p for p in pins if p.id in approved]
                if not pins:
                    raise HTTPException(409, f'None of these images passed the kid-safety check, so nothing was sent to {recipient.name}.')
            target = MoodBoard(profile_id=data.to, name=f'{board.name} · from {sender.name}'[:100],
                               interests=board.interests, enabled=False,
                               **({'child_friendly': True, 'strictness': 'strict'} if child else {}))
            s.add(target)
            s.flush()
        if child:
            pins = [p for p in pins if p.id in approved]  # a board edited during the check cannot slip new images in
        added = sum(_copy(s, pin, target.id, data.to) for pin in pins)
        if added:
            s.add(MoodShareNotice(recipient_id=data.to, sender_id=pid, kind='pin' if data.pin_id is not None else 'board',
                                  board_id=target.id, count=added))
        s.commit()
        return {'ok': True, 'added': added, 'recipient': recipient.name, 'held': held}


@router.get('/notices')
def notices(pid: int = Depends(require_boards)):
    with session() as s:
        rows = s.exec(select(MoodShareNotice).where(MoodShareNotice.recipient_id == pid, MoodShareNotice.seen == False)  # noqa: E712
                      .order_by(MoodShareNotice.id.desc()).limit(20)).all()
        out = []
        for n in rows:
            board = s.get(MoodBoard, n.board_id)
            if not board or not s.exec(select(MoodBoard.id).where(MoodBoard.id == board.id, accessible(pid))).first():
                continue
            sender = s.get(Profile, n.sender_id)
            out.append({'id': n.id, 'kind': n.kind, 'count': n.count, 'board_id': board.id, 'board_name': board.name,
                        'sender': sender.name if sender else 'Someone', 'sender_emoji': sender.emoji if sender else '',
                        'created_at': n.created_at})
        return {'notices': out}


class Seen(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=200)


@router.post('/notices/seen')
def notices_seen(data: Seen, pid: int = Depends(require_boards)):
    with session() as s:
        for n in s.exec(select(MoodShareNotice).where(MoodShareNotice.recipient_id == pid,
                                                      MoodShareNotice.id.in_(data.ids))).all():
            n.seen = True
            s.add(n)
        s.commit()
    return {'ok': True}


def unseen_count(s, pid) -> int:
    """Unseen notices whose board the recipient still has."""
    return s.exec(select(func.count()).select_from(MoodShareNotice).join(MoodBoard, MoodBoard.id == MoodShareNotice.board_id)
                  .where(MoodShareNotice.recipient_id == pid, MoodShareNotice.seen == False,  # noqa: E712
                         accessible(pid))).one()


# ---------- shared boards (members)

MAX_MEMBERS = 10
SWEEP_BATCH = 20


def notify_added(s, board, sender, pins):
    """Tell everyone else on a shared board that `sender` added images. One unseen notice per board and sender
    counts up. Children only hear about images they can see."""
    if not pins:
        return
    for rid in [board.profile_id, *member_ids(s, board.id)]:
        if rid == sender:
            continue
        count = sum(1 for p in pins if pin_visible(s, p, board, rid))
        if not count:
            continue
        row = s.exec(select(MoodShareNotice).where(MoodShareNotice.recipient_id == rid, MoodShareNotice.sender_id == sender,
                                                   MoodShareNotice.board_id == board.id, MoodShareNotice.kind == 'added',
                                                   MoodShareNotice.seen == False)).first()  # noqa: E712
        if row:
            row.count += count
            row.created_at = time.time()
        else:
            row = MoodShareNotice(recipient_id=rid, sender_id=sender, kind='added', board_id=board.id, count=count)
        s.add(row)


def members_json(s, board):
    owner = s.get(Profile, board.profile_id)
    return {'owner': profile_brief(owner) if owner else None,
            'members': [profile_brief(p) for m in member_ids(s, board.id) if (p := s.get(Profile, m))]}


def add_member(s, board, to: int, by: int):
    """Add a profile to a board (inside the caller's transaction). A child makes the whole board child-friendly;
    pictures already on it wait for the kid sweep before the child sees them."""
    recipient = s.get(Profile, to)
    from ..accounts import is_open
    if to == board.profile_id or not recipient or not boards_allowed(s, to) or \
            (not is_open() and recipient.account_id is None):
        raise HTTPException(404, 'That profile cannot join Mood Boards.')
    if s.get(MoodBoardMember, (board.id, to)):
        raise HTTPException(409, f'{recipient.name} already works on this board.')
    if len(member_ids(s, board.id)) >= MAX_MEMBERS:
        raise HTTPException(409, f'A board can be shared with up to {MAX_MEMBERS} profiles.')
    s.add(MoodBoardMember(board_id=board.id, profile_id=to, added_by=by))
    if recipient.role == 'child' and not (board.child_friendly and board.strictness == 'strict'):
        board.child_friendly, board.strictness = True, 'strict'
        board.revision += 1  # a collection started under the grown-up rules never lands
        board.lease, board.build_started = '', 0
        s.add(board)
    s.add(MoodShareNotice(recipient_id=to, sender_id=by, kind='member', board_id=board.id, count=1))
    return recipient


class MemberAdd(BaseModel):
    to: int


@router.get('/boards/{bid}/members')
def list_members(bid: int, pid: int = Depends(require_boards)):
    with session() as s:
        return members_json(s, owned(s, pid, bid))


@router.post('/boards/{bid}/members')
def invite(bid: int, data: MemberAdd, pid: int = Depends(require_hub)):
    """Share a board to work on together. Owner only; children cannot invite (require_hub)."""
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        board = owned(s, pid, bid, manage=True)
        recipient = add_member(s, board, data.to, pid)
        s.commit()
        return {**members_json(s, board), 'added': recipient.name}


@router.delete('/boards/{bid}/members/{member}')
def remove_member(bid: int, member: int, pid: int = Depends(require_boards)):
    """The owner removes anyone; a member leaves. The board stays with its owner (still child-friendly)."""
    with session() as s:
        board = owned(s, pid, bid)
        if member != pid and board.profile_id != pid:
            raise HTTPException(403, 'Only the board owner can remove others.')
        row = s.get(MoodBoardMember, (bid, member))
        if not row:
            raise HTTPException(404, 'Not on this board.')
        s.delete(row)
        s.exec(delete(MoodShareNotice).where(MoodShareNotice.recipient_id == member, MoodShareNotice.board_id == bid))
        s.commit()
        return members_json(s, board)


def kid_boards(s) -> list[MoodBoard]:
    """Boards a child owns or works on."""
    kids = select(Profile.id).where(Profile.role == 'child')
    joined = select(MoodBoardMember.board_id).where(MoodBoardMember.profile_id.in_(kids))
    return list(s.exec(select(MoodBoard).where(MoodBoard.profile_id.in_(kids) | MoodBoard.id.in_(joined))).all())


def kid_sweep(limit: int = SWEEP_BATCH) -> int:
    """Kid-check unchecked pictures on boards a child uses (a child joined, a model was down, an image moved).
    The child's own unchecked finds on the child's own board are trusted (the child rule found them).
    Errors leave pictures unchecked for the next sweep. Returns how many were judged."""
    from . import kidsafe
    if not kidsafe._vision():
        return 0
    with session() as s:
        ids = []
        for board in kid_boards(s):
            q = select(MoodPin.id).join(MoodAsset, MoodAsset.pin_id == MoodPin.id).where(
                MoodPin.board_id == board.id, MoodPin.hidden == False, MoodPin.kid_state == '')  # noqa: E712
            if is_child(s, board.profile_id):
                q = q.where(MoodPin.profile_id != board.profile_id)
            ids += s.exec(q.order_by(MoodPin.id.desc()).limit(limit - len(ids))).all()
            if len(ids) >= limit:
                break
        pictures = [(i, a.data) for i in ids if (a := s.get(MoodAsset, i))]
    if not pictures:
        return 0
    unchecked: set[int] = set()
    reasons: dict[int, str] = {}
    try:
        ok = _kid_check(pictures, unchecked, reasons)
    except HTTPException:
        return 0
    with session() as s:
        for pin_id, _ in pictures:
            pin = s.get(MoodPin, pin_id)
            if not pin or pin.kid_state or pin_id in unchecked:
                continue  # a grown-up decided meanwhile, or not judged
            pin.kid_state = 'ok' if pin_id in ok else 'hidden'
            pin.kid_reason = '' if pin_id in ok else (reasons.get(pin_id) or 'Did not pass the kid-safety check.')
            s.add(pin)
        s.commit()
    return len(pictures) - len(unchecked)


# ---------- public links (owner side)

def _require_grown_up(s, pid):
    p = s.get(Profile, pid)
    if not p or not grown_up_access(s, p):
        raise HTTPException(403, 'Only grown-up profiles can create public links.')


def _dead(s, row) -> bool:
    """A link whose board or image was deleted (or replaced under a reused id) can never work again."""
    board = s.get(MoodBoard, row.board_id)
    if not board or board.profile_id != row.profile_id or board.created_at > row.created_at:
        return True
    if row.pin_id:
        pin = s.get(MoodPin, row.pin_id)
        return not pin or pin.board_id != row.board_id or pin.created_at > row.created_at
    return False


def _prune(s, pid):
    """Drop this profile's dead links, so they neither show in the list nor count toward the limit."""
    gone = [r for r in s.exec(select(MoodShareLink).where(MoodShareLink.profile_id == pid)).all() if _dead(s, r)]
    for r in gone:
        s.delete(r)
    if gone:
        s.commit()


def link_json(s, row):
    board = s.get(MoodBoard, row.board_id)
    pin = s.get(MoodPin, row.pin_id) if row.pin_id else None
    return {'id': row.id, 'kind': row.kind, 'board_id': row.board_id, 'pin_id': row.pin_id,
            'board_name': board.name if board else '', 'pin_title': pin.title if pin else '',
            'hint': row.hint, 'include_source': row.include_source, 'expires_at': row.expires_at,
            'expired': bool(row.expires_at and row.expires_at <= time.time()),
            'created_at': row.created_at, 'views': row.views, 'last_viewed': row.last_viewed or None}


@router.get('/links')
def links(board_id: int | None = None, pid: int = Depends(require_hub)):
    with session() as s:
        _prune(s, pid)
        q = select(MoodShareLink).where(MoodShareLink.profile_id == pid)
        if board_id is not None:
            q = q.where(MoodShareLink.board_id == board_id)
        return {'links': [link_json(s, r) for r in s.exec(q.order_by(MoodShareLink.id.desc())).all()]}


class LinkCreate(BaseModel):
    pin_id: int | None = None
    expires_days: int | None = Field(default=None, ge=1, le=30)
    include_source: bool = False


@router.post('/boards/{bid}/links')
def create_link(bid: int, data: LinkCreate, pid: int = Depends(require_hub)):
    if data.expires_days not in (None, 1, 7, 30):
        raise HTTPException(422, 'Choose never, 1, 7 or 30 days.')
    with session() as s:
        _require_grown_up(s, pid)
        owned(s, pid, bid, manage=True)
        if data.pin_id is not None:
            _visible_pin(s, pid, bid, data.pin_id)
        _prune(s, pid)
        if s.exec(select(func.count()).select_from(MoodShareLink).where(MoodShareLink.profile_id == pid)).one() >= MAX_LINKS:
            raise HTTPException(409, f'Keep up to {MAX_LINKS} public links. Revoke an old one first.')
        token = PREFIX + secrets.token_urlsafe(32)
        row = MoodShareLink(profile_id=pid, kind='pin' if data.pin_id is not None else 'board', board_id=bid,
                            pin_id=data.pin_id, token_hash=_hash(token), hint=token[-4:],
                            include_source=data.include_source,
                            expires_at=time.time() + data.expires_days * 86400 if data.expires_days else None)
        s.add(row)
        s.commit()
        s.refresh(row)
        # The plain link is shown once; only its hash is kept.
        return {'path': f'/s/{token}', 'link': link_json(s, row)}


@router.delete('/links/{link_id}')
def revoke_link(link_id: int, pid: int = Depends(require_hub)):
    with session() as s:
        row = s.get(MoodShareLink, link_id)
        if not row or row.profile_id != pid:
            raise HTTPException(404, 'Link not found')
        s.delete(row)
        s.commit()
    return {'ok': True}


def delete_profile(s, pid):
    """Called inside household deletion's existing transaction."""
    s.exec(delete(MoodShareLink).where(MoodShareLink.profile_id == pid))
    s.exec(delete(MoodShareNotice).where((MoodShareNotice.recipient_id == pid) | (MoodShareNotice.sender_id == pid)))


# ---------- public links (anyone with the link)

class _Limiter:
    """Per-client sliding window, in memory (one process). Bounded: the least recently seen clients are forgotten
    first, so a flood of fresh addresses costs O(1) per request and fixed memory."""
    MAX_CLIENTS = 10_000

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.hits: OrderedDict[str, deque] = OrderedDict()
        self.lock = threading.Lock()

    def _recent(self, key, now):
        q = self.hits.get(key)
        if q is None:
            return None
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def hit(self, key) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self._recent(key, now)
            if q is None:
                q = self.hits[key] = deque()
            self.hits.move_to_end(key)
            while len(self.hits) > self.MAX_CLIENTS:
                self.hits.popitem(last=False)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def blocked(self, key) -> bool:
        with self.lock:
            q = self._recent(key, time.monotonic())
            return q is not None and len(q) >= self.limit

    def reset(self):
        with self.lock:
            self.hits.clear()


PAGE_LIMIT = _Limiter(60, 60)
MEDIA_LIMIT = _Limiter(600, 60)
MISS_LIMIT = _Limiter(20, 600)


def _client(request: Request) -> str:
    """Rate-limit key: the client address, with IPv6 grouped by /64 (one home or phone gets a whole /64)."""
    host = request.client.host if request.client else '?'
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6 and ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    return str(ipaddress.ip_network(f'{ip}/64', strict=False).network_address) if ip.version == 6 else host


def _unavailable():
    return HTTPException(404, 'This shared link is unavailable.', headers=JSON_HEADERS)


def _resolve(s, request, token, limiter):
    ip = _client(request)
    if MISS_LIMIT.blocked(ip) or not limiter.hit(ip):
        raise HTTPException(429, 'Too many requests. Try again in a few minutes.', headers={**JSON_HEADERS, 'Retry-After': '60'})
    row = s.exec(select(MoodShareLink).where(MoodShareLink.token_hash == _hash(token))).first() if TOKEN_RE.fullmatch(token) else None
    board = s.get(MoodBoard, row.board_id) if row else None
    ok = (row and board and board.profile_id == row.profile_id and board.created_at <= row.created_at
          and (not row.expires_at or row.expires_at > time.time()) and hub_allowed(s, row.profile_id))
    if ok and row.pin_id:
        pin = s.get(MoodPin, row.pin_id)
        ok = pin and pin.board_id == board.id and pin.profile_id == row.profile_id and pin.created_at <= row.created_at
    if not ok:
        MISS_LIMIT.hit(ip)
        raise _unavailable()
    return row, board


def _source(row, url):
    if not row.include_source:
        return None
    try:
        parts = urlsplit(url or '')
    except ValueError:
        return None
    return url if parts.scheme in ('http', 'https') and parts.hostname and not parts.username and not parts.password else None


def _scope(row):
    """The pins a link may show: exactly its image, or every visible image on its board."""
    q = select(MoodPin).join(MoodAsset, MoodAsset.pin_id == MoodPin.id).where(
        MoodPin.board_id == row.board_id, MoodPin.hidden == False)  # noqa: E712
    return q.where(MoodPin.id == row.pin_id) if row.pin_id else q


@public_router.get('/{token}')
def public_view(token: str, request: Request, response: Response, cursor: int | None = None):
    with session() as s:
        row, board = _resolve(s, request, token, PAGE_LIMIT)
        q = _scope(row)
        if cursor is not None:
            q = q.where(MoodPin.id < cursor)
        pins = s.exec(q.order_by(MoodPin.id.desc()).limit(PAGE + 1)).all()
        more = len(pins) > PAGE
        pins = pins[:PAGE]
        if row.kind == 'pin' and not pins:
            raise _unavailable()
        if cursor is None and time.time() - row.last_viewed > 60:
            row.views += 1
            row.last_viewed = time.time()
            s.add(row)
            s.commit()
        response.headers.update(JSON_HEADERS)
        out = []
        for p in pins:
            item = {'id': p.id, 'title': p.title, 'width': p.width, 'height': p.height, 'media_type': p.media_type}
            link = _source(row, p.source_url)
            if link:
                item['source_url'] = link
            out.append(item)
        return {'kind': row.kind, 'title': board.name if row.kind == 'board' else (out[0]['title'] if out else ''),
                'expires_at': row.expires_at, 'pins': out, 'next_cursor': pins[-1].id if more else None}


@public_router.get('/{token}/pins/{pin_id}/media')
def public_media(token: str, pin_id: int, request: Request):
    with session() as s:
        row, _board = _resolve(s, request, token, MEDIA_LIMIT)
        pin = s.exec(_scope(row).where(MoodPin.id == pin_id)).first()
        asset = s.get(MoodAsset, pin.id) if pin else None
        if not asset:
            raise _unavailable()
        return Response(asset.data, media_type=asset.mime, headers=HEADERS)
