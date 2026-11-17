"""Household authorization built on accounts and server-verified profile PIN sessions.

Accounts mode (docs/ACCOUNTS.md): the signed-in account owns its profiles; administrator rights
belong to the account and are used from its non-child profiles. Profile PINs are always optional.
Open mode (BT_AUTH=open, tests/dev) keeps the legacy household: role 'admin' on the profile.
Roles are dedicated fields; arbitrary profile preferences never grant access.
"""
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from . import profile_pins
from .accounts import PROFILE_COOKIE, is_open
from .db import session
from .models import Account, Profile


def has_children(s: Session, account_id: Optional[int] = None) -> bool:
    q = select(Profile.id).where(Profile.role == 'child')
    if account_id is not None:
        q = q.where(Profile.account_id == account_id)
    return s.exec(q).first() is not None


def account_of(s: Session, p: Profile) -> Optional[Account]:
    return s.get(Account, p.account_id) if p.account_id is not None else None


def is_admin_profile(s: Session, p: Profile) -> bool:
    """Profile that carries administrator rights (before any PIN requirement)."""
    if is_open():
        return p.role == 'admin'
    acc = account_of(s, p)
    return bool(acc and acc.role == 'admin' and not acc.disabled and p.role != 'child')


def _owned(s: Session, pid, account_id: int) -> Optional[Profile]:
    try:
        p = s.get(Profile, int(pid))
    except (TypeError, ValueError):
        return None
    return p if p and p.account_id == account_id else None


def profile_id(request: Request, x_profile_id: Optional[int] = Header(default=None)) -> int:
    with session() as s:
        if is_open():
            p = s.get(Profile, x_profile_id) if x_profile_id is not None else s.exec(select(Profile).order_by(Profile.id)).first()
            if not p:
                raise HTTPException(404 if x_profile_id is not None else 400,
                                    'profile not found' if x_profile_id is not None else 'no profiles')
        else:
            aid = getattr(request.state, 'account_id', None)
            if aid is None:
                raise HTTPException(401, 'auth required')
            if x_profile_id is not None:
                p = _owned(s, x_profile_id, aid)
                if not p:
                    raise HTTPException(404, 'profile not found')
            else:
                # Media tags can't send headers: the web app mirrors its choice in a plain cookie.
                public = getattr(request.state, 'session_public', '')
                p = _owned(s, request.cookies.get(PROFILE_COOKIE + public), aid) or \
                    s.exec(select(Profile).where(Profile.account_id == aid).order_by(Profile.id)).first()
                if not p:
                    raise HTTPException(400, 'no profiles')
        profile_pins.require(s, p.id, request)
        return p.id


def own_profile_ids(request: Request) -> Optional[set[int]]:
    """Profiles of the signed-in account; None in open mode (whole household)."""
    if is_open():
        return None
    aid = getattr(request.state, 'account_id', None)
    if aid is None:
        raise HTTPException(401, 'auth required')
    with session() as s:
        return set(s.exec(select(Profile.id).where(Profile.account_id == aid)).all())


def require_admin(pid: int = Depends(profile_id)) -> int:
    with session() as s:
        p = s.get(Profile, pid)
        if not p or not is_admin_profile(s, p):
            raise HTTPException(403, 'Administrator profile required. Switch to an administrator profile.'
                                if is_open() else 'Administrator account required.')
        if needs_pin(s, p) and not profile_pins.record(s, pid):
            raise HTTPException(403, 'An administrator PIN is required while child profiles are enabled.')
    return pid


def require_account_admin(request: Request, x_profile_id: Optional[int] = Header(default=None)) -> Optional[int]:
    """Admin-only in accounts mode; open mode keeps household-wide access (e.g. stats), no profile needed."""
    if is_open():
        return None
    return require_admin(profile_id(request, x_profile_id))


def require_manager(pid: int = Depends(profile_id)) -> int:
    """Manage the account's own profiles: any non-child profile of the account (open mode: an administrator)."""
    if is_open():
        return require_admin(pid)
    with session() as s:
        p = s.get(Profile, pid)
        if not p or p.role == 'child':
            raise HTTPException(403, 'Switch to a grown-up profile to manage profiles.')
    return pid


def needs_pin(s: Session, p: Profile) -> bool:
    """Whether this profile must have a PIN to keep its grown-up access: administrators do once
    child profiles exist; adult profiles may stay open."""
    if is_open():
        return p.role == 'admin' and has_children(s)
    return False  # accounts mode: PINs are always optional, the account owner decides


def grown_up_access(s: Session, p: Profile) -> bool:
    """Adult features (Hub, Music, unfiltered search) for this profile."""
    return p.role in ('admin', 'adult') and (not needs_pin(s, p) or profile_pins.record(s, p.id) is not None)


def hub_allowed(s: Session, pid: int) -> bool:
    p = s.get(Profile, pid)
    return bool(p and grown_up_access(s, p))


def is_child(s: Session, pid: int) -> bool:
    p = s.get(Profile, pid)
    return bool(p and p.role == 'child')


def boards_allowed(s: Session, pid: int) -> bool:
    """Mood Boards: grown-ups, plus child profiles, whose boards are always child-friendly."""
    p = s.get(Profile, pid)
    return bool(p and (grown_up_access(s, p) or p.role == 'child'))


def require_boards(pid: int = Depends(profile_id)) -> int:
    with session() as s:
        if not boards_allowed(s, pid):
            raise HTTPException(403, 'Mood Boards are unavailable for this profile.')
    return pid


# Hub routes a child profile may call, as (method, path below /hub). Everything else is
# grown-up only, so a new route stays closed to children until it is reviewed and listed here.
# Reads on these routes are kid-safe filtered server-side (see hub/kidsafe.py).
CHILD_HUB_ROUTES = frozenset({
    ('GET', '/shelf'), ('GET', '/registry'), ('GET', '/counts'),
    ('GET', '/items'), ('GET', '/items/{iid}'), ('GET', '/items/{iid}/image'),
    ('GET', '/items/{iid}/assets/{kind}'), ('HEAD', '/items/{iid}/assets/{kind}'),
    ('POST', '/items/{iid}/curator-feedback'), ('POST', '/items/{iid}/state'), ('POST', '/items/state'),
    ('POST', '/items/{iid}/feedback'), ('GET', '/items/{iid}/why'), ('GET', '/stories/{sid}'),
    ('GET', '/news'), ('POST', '/news/seen'),
    ('GET', '/collections'), ('POST', '/collections'), ('PATCH', '/collections/{cid}'), ('DELETE', '/collections/{cid}'),
    ('POST', '/collections/{cid}/items/{iid}'), ('DELETE', '/collections/{cid}/items/{iid}'),
    ('GET', '/team'), ('GET', '/sources'), ('POST', '/sources'),
    ('GET', '/curators'), ('GET', '/curators/templates'), ('POST', '/curators/draft'), ('POST', '/curators'),
    ('GET', '/curators/{cid}'), ('PATCH', '/curators/{cid}'), ('DELETE', '/curators/{cid}'),
    ('POST', '/curators/{cid}/{action}'),
    ('GET', '/topics'), ('POST', '/topics'), ('GET', '/topics/{tid}'), ('PATCH', '/topics/{tid}'),
    ('DELETE', '/topics/{tid}'), ('POST', '/topics/{tid}/items/{iid}'), ('DELETE', '/topics/{tid}/items/{iid}'),
    ('GET', '/scout-report/config'), ('PUT', '/scout-report/config'), ('GET', '/scout-report/issues'),
    ('GET', '/scout-report/preview'), ('POST', '/scout-report/generate'),
    ('GET', '/briefings'), ('POST', '/briefings'), ('PATCH', '/briefings/{bid}'), ('DELETE', '/briefings/{bid}'),
    ('POST', '/briefings/{bid}/generate'), ('GET', '/briefings/{bid}/issues'),
    ('GET', '/alerts'), ('POST', '/alerts/{aid}/state'),
    ('GET', '/search'), ('GET', '/search/semantic'), ('GET', '/search/semantic/status'),
    ('GET', '/saved-searches'), ('POST', '/saved-searches'), ('DELETE', '/saved-searches/{sid}'),
    ('GET', '/memes/items'), ('GET', '/memes/facets'), ('GET', '/memes/trends'), ('POST', '/memes/trends/{tid}/state'),
    ('GET', '/memes/formats'), ('GET', '/memes/formats/{fid}'), ('GET', '/memes/items/{iid}/similar'),
    ('POST', '/memes/languages/{code}/hide'),
    ('GET', '/today'), ('POST', '/today/rerun'),
    ('POST', '/boards/{bid}/images'),  # kid-safety checked in moodboards.add_image_for
})
# Curator actions a child may take; 'duplicate' copies sources, 'report' is the kid-safe report.
CHILD_CURATOR_ACTIONS = frozenset({'run', 'pause', 'resume', 'report', 'duplicate'})
KID_LOCKED = 'Not available on kid profiles.'


def hub_worker_allowed(s: Session, pid: int) -> bool:
    """Background Hub work for this profile: grown-ups, and child profiles in kid-safe mode."""
    p = s.get(Profile, pid)
    return bool(p and (grown_up_access(s, p) or p.role == 'child'))


def _hub_path(request: Request) -> str:
    route = request.scope.get('route')
    path = getattr(route, 'path', '') or request.url.path
    i = path.find('/hub/')
    return path[i + 4:] if i >= 0 else ''


def require_hub(request: Request, pid: int = Depends(profile_id)) -> int:
    with session() as s:
        if hub_allowed(s, pid):
            return pid
        if is_child(s, pid):
            if (request.method, _hub_path(request)) in CHILD_HUB_ROUTES:
                return pid
            raise HTTPException(403, KID_LOCKED)
        raise HTTPException(403, 'Hub discovery and research are unavailable for this profile.')


def require_grown_up(pid: int = Depends(profile_id)) -> int:
    """Hub features that stay closed to child profiles even inside an allowed router."""
    with session() as s:
        if not hub_allowed(s, pid):
            raise HTTPException(403, KID_LOCKED if is_child(s, pid) else 'Hub discovery and research are unavailable for this profile.')
    return pid


def validate_household(s: Session) -> None:
    """Validate after flushing profile/PIN mutations; a failure rolls them back."""
    profiles = s.exec(select(Profile)).all()
    if is_open():
        if not any(p.role == 'admin' for p in profiles):
            raise HTTPException(400, 'Keep at least one administrator profile.')
        missing = [p.name for p in profiles if p.role == 'admin' and not profile_pins.record(s, p.id)] \
            if any(p.role == 'child' for p in profiles) else []
    else:
        missing = [p.name for p in profiles if needs_pin(s, p) and not profile_pins.record(s, p.id)]
    if missing:
        raise HTTPException(400, 'Set a PIN on every administrator profile before enabling child profiles: ' + ', '.join(missing))
