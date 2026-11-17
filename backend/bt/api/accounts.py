"""Account administration (docs/ACCOUNTS.md). Admins manage accounts; they never act inside them."""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, func, select

from .. import accounts as A
from ..access import require_admin
from ..auth import PLACEHOLDER
from ..db import get_session
from ..models import Account, AccountSession, Profile, now
from .core import purge_profile

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _closed():
    if A.is_open():
        raise HTTPException(404, "Accounts are off (BT_AUTH=open).")


def _account(s: Session, aid: int) -> Account:
    acc = s.get(Account, aid)
    if not acc:
        raise HTTPException(404, "not found")
    return acc


def _profile_out(p: Profile) -> dict:
    return {"id": p.id, "name": p.name, "emoji": p.emoji, "color": p.color, "role": p.role}


def account_out(s: Session, acc: Account) -> dict:
    inv = A.open_invite(s, acc.id)
    return {"id": acc.id, "username": None if acc.username.startswith(PLACEHOLDER) else acc.username,
            "role": acc.role, "disabled": acc.disabled, "registered": bool(acc.password_hash),
            "created_at": A.utc(acc.created_at),
            "profiles": [_profile_out(p) for p in s.exec(select(Profile).where(Profile.account_id == acc.id)
                                                         .order_by(Profile.id)).all()],
            "sessions": s.exec(select(func.count()).select_from(AccountSession).where(
                AccountSession.account_id == acc.id, AccountSession.revoked_at == None)).one(),  # noqa: E711
            "invite": {"purpose": inv.purpose, "expires_at": A.utc(inv.expires_at)} if inv else None}


def _keep_an_admin(s: Session, changing: Account) -> None:
    others = s.exec(select(Account).where(Account.role == "admin", Account.disabled == False,  # noqa: E712
                                          Account.id != changing.id)).all()
    if not others:
        raise HTTPException(400, "Keep at least one enabled administrator account.")


@router.get("/accounts")
def list_accounts(s: Session = Depends(get_session)):
    _closed()
    return [account_out(s, a) for a in s.exec(select(Account).order_by(Account.id)).all()]


class NewAccount(BaseModel):
    username: Optional[str] = None
    role: Literal["admin", "user"] = "user"


@router.post("/accounts")
def create_account(body: NewAccount, request: Request, s: Session = Depends(get_session)):
    _closed()
    name = body.username.strip() if body.username else ""
    if name.lower().startswith(PLACEHOLDER):
        raise HTTPException(400, "That username is reserved.")
    acc = A.new_account(s, name, body.role, placeholder=not name)
    token = A.create_invite(s, acc, "register", request.state.account_id)
    return {"account": account_out(s, acc), "link": A.invite_link(token)}


class AccountPatch(BaseModel):
    role: Optional[Literal["admin", "user"]] = None
    disabled: Optional[bool] = None


@router.patch("/accounts/{aid}")
def patch_account(aid: int, body: AccountPatch, s: Session = Depends(get_session)):
    _closed()
    acc = _account(s, aid)
    if (body.role == "user" and acc.role == "admin") or (body.disabled and acc.role == "admin"):
        _keep_an_admin(s, acc)
    if body.role is not None:
        acc.role = body.role
    if body.disabled is not None:
        acc.disabled = body.disabled
        if body.disabled:
            A.revoke_account_sessions(s, acc.id)
    s.add(acc)
    s.commit()
    return account_out(s, acc)


@router.delete("/accounts/{aid}")
def delete_account(aid: int, request: Request, s: Session = Depends(get_session)):
    _closed()
    acc = _account(s, aid)
    if acc.id == request.state.account_id:
        raise HTTPException(400, "You can't delete your own account.")
    if acc.role == "admin":
        _keep_an_admin(s, acc)
    for p in s.exec(select(Profile).where(Profile.account_id == acc.id)).all():
        purge_profile(s, p)
    from ..models import AccountInvite, DeviceCode
    for model in (AccountSession, AccountInvite):
        for row in s.exec(select(model).where(model.account_id == acc.id)).all():
            s.delete(row)
    for row in s.exec(select(DeviceCode).where(DeviceCode.account_id == acc.id)).all():
        s.delete(row)
    s.delete(acc)
    s.commit()
    return {"ok": True}


@router.post("/accounts/{aid}/reset")
def reset_link(aid: int, request: Request, s: Session = Depends(get_session)):
    _closed()
    acc = _account(s, aid)
    token = A.create_invite(s, acc, "reset" if acc.password_hash else "register", request.state.account_id)
    return {"link": A.invite_link(token)}


@router.delete("/accounts/{aid}/sessions")
def sign_out_everywhere(aid: int, s: Session = Depends(get_session)):
    _closed()
    acc = _account(s, aid)
    A.revoke_account_sessions(s, acc.id)
    s.commit()
    return {"ok": True}


@router.get("/profiles/unowned")
def unowned_profiles(s: Session = Depends(get_session)):
    _closed()
    return [_profile_out(p) for p in s.exec(select(Profile).where(Profile.account_id == None)  # noqa: E711
                                            .order_by(Profile.id)).all()]


class Assign(BaseModel):
    account_id: int


@router.post("/profiles/{pid}/assign")
def assign_profile(pid: int, body: Assign, s: Session = Depends(get_session)):
    _closed()
    p = s.get(Profile, pid)
    if not p:
        raise HTTPException(404, "not found")
    if p.account_id is not None:  # admins manage accounts, they don't move (or peek into) owned profiles
        raise HTTPException(400, "Only unassigned profiles can be assigned.")
    acc = _account(s, body.account_id)
    if p.role == "admin":  # legacy household role; administrator rights now belong to the account
        p.role = "adult"
    p.account_id = acc.id
    s.add(p)
    s.commit()
    return _profile_out(p) | {"account_id": acc.id, "assigned_at": now()}
