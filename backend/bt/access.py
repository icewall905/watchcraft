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


def require_hub(pid: int = Depends(profile_id)) -> int:
    with session() as s:
        if not hub_allowed(s, pid):
            raise HTTPException(403, 'Hub discovery and research are unavailable for this profile.')
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
