"""Household authorization built on server-verified profile PIN sessions.

An open household keeps its first administrator accessible without a PIN. Child
profiles require PINs on every administrator profile. Adult profiles may stay
open by the household's choice: anyone on the device, children included, can
then switch to them, so they are the household's own call.
Roles are dedicated fields; arbitrary profile preferences never grant access.
"""
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from . import profile_pins
from .db import session
from .models import Profile


def has_children(s: Session) -> bool:
    return s.exec(select(Profile.id).where(Profile.role == 'child')).first() is not None


def profile_id(request: Request, x_profile_id: Optional[int] = Header(default=None)) -> int:
    with session() as s:
        p = s.get(Profile, x_profile_id) if x_profile_id is not None else s.exec(select(Profile).order_by(Profile.id)).first()
        if not p:
            raise HTTPException(404 if x_profile_id is not None else 400,
                                'profile not found' if x_profile_id is not None else 'no profiles')
        profile_pins.require(s, p.id, request)
        return p.id


def require_admin(pid: int = Depends(profile_id)) -> int:
    with session() as s:
        p = s.get(Profile, pid)
        if not p or p.role != 'admin':
            raise HTTPException(403, 'Administrator profile required. Switch to an administrator profile.')
        if has_children(s) and not profile_pins.record(s, pid):
            raise HTTPException(403, 'An administrator PIN is required while child profiles are enabled.')
    return pid


def needs_pin(s: Session, p: Profile) -> bool:
    """Whether this profile must have a PIN to keep its grown-up access: administrators do once
    child profiles exist; adult profiles may stay open."""
    return p.role == 'admin' and has_children(s)


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
    if not any(p.role == 'admin' for p in profiles):
        raise HTTPException(400, 'Keep at least one administrator profile.')
    if any(p.role == 'child' for p in profiles):
        missing = [p.name for p in profiles if p.role == 'admin' and not profile_pins.record(s, p.id)]
        if missing:
            raise HTTPException(400, 'Set a PIN on every administrator profile before enabling child profiles: ' + ', '.join(missing))
