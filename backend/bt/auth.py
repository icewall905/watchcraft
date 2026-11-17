"""Account sign-in: session middleware and /auth routes (docs/ACCOUNTS.md).

A client keeps one httponly cookie per signed-in account (`wc_s_<public_id>`) and picks the active
one with `wc_active` or the per-tab `X-Account` header. BT_AUTH=open skips all of this (tests/dev).
"""
from typing import Optional

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from . import accounts as A
from .db import session
from .models import Account, AccountInvite, AccountSession, DeviceCode, Profile, now

router = APIRouter()

# /api paths reachable without a signed-in account (everything non-/api is the SPA shell).
OPEN = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/bootstrap", "/api/auth/register",
        "/api/auth/device/start", "/api/auth/device/poll"}
# /api/ext/v1/ is the browser extension (own key, bt/hub/extension.py); /api/public/ serves capability
# links (the token in the path is the credential); /api/auth/invite/ reads a registration link.
OPEN_PREFIXES = ("/api/ext/v1/", "/api/public/", "/api/auth/invite/")


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def _cookie_kwargs(request: Request) -> dict:
    return {"httponly": True, "samesite": "lax", "secure": _https(request), "path": "/"}


def set_session_cookie(response: Response, request: Request, public_id: str, token: str) -> None:
    response.set_cookie(A.SESSION_COOKIE + public_id, token, max_age=A.COOKIE_MAX_AGE, **_cookie_kwargs(request))


def set_active_cookie(response: Response, request: Request, public_id: str) -> None:
    response.set_cookie(A.ACTIVE_COOKIE, public_id, max_age=A.COOKIE_MAX_AGE, **_cookie_kwargs(request))


def _lookup(cookies: dict, header: str):
    with session() as s:
        found = A.resolve(s, cookies, header)
        if not found:
            return None
        row, acc = found
        refresh = A.touch(s, row)
        return {"account_id": acc.id, "account_role": acc.role, "session_id": row.id,
                "session_public": row.public_id, "refresh": refresh}


class AuthMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming proxy/SSE responses pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/") or A.is_open():
            return await self.app(scope, receive, send)
        p = scope["path"]
        request = Request(scope)
        cookies = dict(request.cookies)
        found = await anyio.to_thread.run_sync(_lookup, cookies, request.headers.get("x-account", ""))
        if not found:
            if p not in OPEN and not p.startswith(OPEN_PREFIXES):
                return await JSONResponse({"detail": "auth required"}, status_code=401)(scope, receive, send)
            return await self.app(scope, receive, send)
        state = scope.setdefault("state", {})
        for k in ("account_id", "account_role", "session_id", "session_public"):
            state[k] = found[k]
        if not found["refresh"] or p == "/api/auth/logout":
            return await self.app(scope, receive, send)
        # Sliding lifetime: re-send the session cookie (400-day browser cap) at most once a day.
        pid = found["session_public"]
        carrier = Response()
        set_session_cookie(carrier, request, pid, A.session_cookies(cookies)[pid])
        extra = [(k, v) for k, v in carrier.raw_headers if k == b"set-cookie"]

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def account_id(request: Request) -> Optional[int]:
    return getattr(request.state, "account_id", None)


def require_account(request: Request) -> int:
    aid = account_id(request)
    if aid is None:
        raise HTTPException(401, "auth required")
    return aid


def _label(request: Request, given: str = "") -> str:
    if given:
        return given
    ua = request.headers.get("user-agent", "")
    if "WatchCraftAndroid/" in ua:
        return "Android app"
    for key, name in (("SMART-TV", "TV browser"), ("Tizen", "TV browser"), ("Web0S", "TV browser"),
                      ("Android", "Android browser"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                      ("Windows", "Windows browser"), ("Macintosh", "Mac browser"), ("Linux", "Linux browser")):
        if key in ua:
            return name
    return "Browser"


def _account_out(acc: Account) -> dict:
    return {"id": acc.id, "username": acc.username, "role": acc.role}


def _status(s: Session, request: Request, response: Optional[Response] = None, cookies: Optional[dict] = None,
            active: Optional[str] = None) -> dict:
    if A.is_open():
        return {"mode": "open", "bootstrap": False, "required": False, "authenticated": True,
                "active": None, "account": None, "accounts": []}
    cookies = request.cookies if cookies is None else cookies
    rows = A.signed_in(s, cookies)
    if active is None:
        found = A.resolve(s, cookies, request.headers.get("x-account", ""))
        active = found[0].public_id if found else None
    current = next((acc for row, acc in rows if row.public_id == active), None)
    return {"mode": "accounts", "bootstrap": A.bootstrap_open(s), "required": True,
            "authenticated": current is not None, "active": active if current else None,
            "account": _account_out(current) if current else None,
            "accounts": [{"public_id": row.public_id, "username": acc.username, "role": acc.role,
                          "active": row.public_id == active} for row, acc in rows]}


def _sign_in(s: Session, request: Request, response: Response, acc: Account, device_label: str = "") -> dict:
    """Reuse this client's session for the account if it has one, else open a new one; make it active."""
    for row, other in A.signed_in(s, request.cookies):
        if other.id == acc.id:
            set_active_cookie(response, request, row.public_id)
            return {"public_id": row.public_id, "account": _account_out(acc)}
    row, token = A.create_session(s, acc, _label(request, device_label), request.headers.get("user-agent", ""))
    set_session_cookie(response, request, row.public_id, token)
    set_active_cookie(response, request, row.public_id)
    return {"public_id": row.public_id, "account": _account_out(acc)}


def _client_ip(request: Request) -> str:
    # The reverse proxy appends the real peer last; earlier entries are client-supplied.
    fwd = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    return fwd[-1] if fwd else (request.client.host if request.client else "?")


# ---------- routes

@router.get("/auth/status")
def status(request: Request):
    with session() as s:
        return _status(s, request)


class Login(BaseModel):
    username: str = ""
    password: str
    device_label: str = ""


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    if A.is_open():
        return {"ok": True}
    name = (body.username or "").strip().lower()
    keys = ("user:" + name, "login-ip:" + _client_ip(request))
    A.throttle_check(*keys, limit=A.LOGIN_USER_LIMIT)
    with session() as s:
        acc = s.exec(select(Account).where(Account.username == name)).first()
        if not acc or not acc.password_hash or acc.disabled:
            A.burn_time(body.password)
            ok = False
        else:
            ok = A.check_password(body.password, acc.password_hash)
        if not ok:
            A.throttle_fail(*keys)
            raise HTTPException(401, "Wrong username or password.")
        A.throttle_clear(keys[0])
        return _sign_in(s, request, response, acc, body.device_label)


class Bootstrap(BaseModel):
    username: str
    password: str
    device_label: str = ""


@router.post("/auth/bootstrap")
def bootstrap(body: Bootstrap, request: Request, response: Response):
    with session() as s:
        s.connection().exec_driver_sql("BEGIN IMMEDIATE")  # two first-run requests can't both win
        if not A.bootstrap_open(s):
            raise HTTPException(403, "This server already has accounts.")
        A.validate_password(body.password)
        acc = A.new_account(s, body.username, "admin")
        acc.password_hash = A.hash_password(body.password)
        s.add(acc)
        s.commit()
        A.ensure_profile(s, acc)
        return _sign_in(s, request, response, acc, body.device_label)


PLACEHOLDER = "pending-"


@router.get("/auth/invite/{token}")
def invite(token: str):
    with session() as s:
        inv, acc = A.find_invite(s, token)
        return {"username": None if acc.username.startswith(PLACEHOLDER) else acc.username, "role": acc.role,
                "purpose": inv.purpose}


class Register(BaseModel):
    token: str
    password: str
    username: Optional[str] = None
    device_label: str = ""


@router.post("/auth/register")
def register(body: Register, request: Request, response: Response):
    A.throttle_check("ip:" + _client_ip(request))
    with session() as s:
        try:
            inv, acc = A.find_invite(s, body.token)
        except HTTPException:
            A.throttle_fail("ip:" + _client_ip(request))
            raise
        A.validate_password(body.password)
        if acc.username.startswith(PLACEHOLDER):
            name = A.normalize_username(body.username or "")
            if name.startswith(PLACEHOLDER) or s.exec(select(Account).where(Account.username == name)).first():
                raise HTTPException(409, "That username is taken.")
            acc.username = name
        acc.password_hash = A.hash_password(body.password)
        s.add(acc)
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, "That username is taken.")
        # Consume the link atomically: a second redeem racing in another process gets nothing.
        used = s.exec(update(AccountInvite).where(AccountInvite.id == inv.id, AccountInvite.used_at == None)  # noqa: E711
                      .values(used_at=now()))
        if used.rowcount != 1:
            s.rollback()
            raise HTTPException(404, "This link is no longer valid. Ask an administrator for a new one.")
        A.revoke_account_sessions(s, acc.id)  # a reset signs the account out everywhere
        s.add(acc)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise HTTPException(409, "That username is taken.")
        A.ensure_profile(s, acc)
        return _sign_in(s, request, response, acc, body.device_label)


class DeviceStart(BaseModel):
    device_label: str = ""


@router.post("/auth/device/start")
def device_start(body: DeviceStart, request: Request):
    A.throttle_check("device:" + _client_ip(request))
    A.throttle_fail("device:" + _client_ip(request))  # at most 5 codes per 5 minutes per address
    with session() as s:
        code, poll = A.new_device_code(s, _label(request, body.device_label), request.headers.get("user-agent", ""))
    return {"code": f"{code[:4]}-{code[4:]}", "poll": poll, "expires_in": int(A.DEVICE_TTL.total_seconds()),
            "link": f"/link?code={code}"}


class DevicePoll(BaseModel):
    poll: str


@router.post("/auth/device/poll")
def device_poll(body: DevicePoll, request: Request, response: Response):
    with session() as s:
        row = s.exec(select(DeviceCode).where(DeviceCode.poll_hash == A.digest(body.poll or ""))).first()
        if not row or row.claimed or A.utc(row.expires_at) < now():
            raise HTTPException(410, "This code expired. Start again.")
        if row.account_id is None:
            return {"status": "pending"}
        acc = s.get(Account, row.account_id)
        if not acc or acc.disabled:
            raise HTTPException(410, "This code expired. Start again.")
        row.claimed = True
        s.add(row)
        s.commit()
        out = _sign_in(s, request, response, acc, row.device_label)
        return {"status": "approved", **out}


class DeviceApprove(BaseModel):
    code: str


@router.post("/auth/device/approve")
def device_approve(body: DeviceApprove, request: Request):
    aid = require_account(request)
    code = A.clean_code(body.code)
    A.throttle_check(f"approve:{aid}")
    with session() as s:
        row = s.exec(select(DeviceCode).where(DeviceCode.code == code)).first()
        if not row or row.claimed or row.account_id is not None or A.utc(row.expires_at) < now():
            A.throttle_fail(f"approve:{aid}")
            raise HTTPException(404, "That code is not valid. Check the code on the TV.")
        row.account_id = aid
        s.add(row)
        s.commit()
        return {"username": s.get(Account, aid).username, "device_label": row.device_label}


class Switch(BaseModel):
    public_id: str


@router.post("/auth/switch")
def switch(body: Switch, request: Request, response: Response):
    with session() as s:
        if not any(row.public_id == body.public_id for row, _ in A.signed_in(s, request.cookies)):
            raise HTTPException(404, "That account is not signed in on this device.")
        set_active_cookie(response, request, body.public_id)
        return _status(s, request, active=body.public_id)


class Logout(BaseModel):
    public_id: Optional[str] = None
    all: bool = False


def _clear_pins(s: Session, request: Request, response: Response, account_ids: set[int]) -> None:
    pids = {str(p) for p in s.exec(select(Profile.id).where(Profile.account_id.in_(account_ids))).all()} if account_ids else set()
    for name in request.cookies:
        if name.startswith("profile_pin_") and (name[len("profile_pin_"):] in pids):
            response.delete_cookie(name)


@router.post("/auth/logout")
def logout(request: Request, response: Response, body: Optional[Logout] = None):
    body = body or Logout()
    if A.is_open():  # legacy: logging out relocks every PIN profile on this client
        for name in request.cookies:
            if name.startswith("profile_pin_"):
                response.delete_cookie(name)
        return {"ok": True}
    with session() as s:
        rows = A.signed_in(s, request.cookies)
        active = getattr(request.state, "session_public", None)
        target = {row.public_id for row, _ in rows} if body.all else {body.public_id or active}
        gone = set()
        for row, acc in rows:
            if row.public_id in target:
                row.revoked_at = now()
                s.add(row)
                gone.add(acc.id)
        s.commit()
        cookies = dict(request.cookies)
        for pid in target:
            if pid:
                response.delete_cookie(A.SESSION_COOKIE + pid, path="/")
                response.delete_cookie(A.PROFILE_COOKIE + pid, path="/")
                cookies.pop(A.SESSION_COOKIE + pid, None)
        _clear_pins(s, request, response, gone)
        remaining = [row.public_id for row, _ in rows if row.public_id not in target]
        nxt = active if active in remaining else (remaining[0] if remaining else None)
        if nxt:
            set_active_cookie(response, request, nxt)
        else:
            response.delete_cookie(A.ACTIVE_COOKIE, path="/")
        cookies[A.ACTIVE_COOKIE] = nxt or ""
        return _status(s, request, cookies=cookies, active=nxt)


class PasswordChange(BaseModel):
    current: str
    new: str


@router.post("/auth/password")
def change_password(body: PasswordChange, request: Request):
    aid = require_account(request)
    A.throttle_check(f"pw:{aid}")
    with session() as s:
        acc = s.get(Account, aid)
        if not A.check_password(body.current, acc.password_hash):
            A.throttle_fail(f"pw:{aid}")
            raise HTTPException(403, "Current password is wrong.")
        A.validate_password(body.new)
        acc.password_hash = A.hash_password(body.new)
        s.add(acc)
        A.revoke_account_sessions(s, aid, keep=request.state.session_id)
        s.commit()
    return {"ok": True}


@router.get("/auth/devices")
def devices(request: Request):
    aid = require_account(request)
    with session() as s:
        rows = s.exec(select(AccountSession).where(AccountSession.account_id == aid, AccountSession.revoked_at == None)  # noqa: E711
                      .order_by(AccountSession.last_seen_at.desc())).all()
        return [{"public_id": r.public_id, "device_label": r.device_label, "user_agent": r.user_agent,
                 "created_at": A.utc(r.created_at), "last_seen_at": A.utc(r.last_seen_at),
                 "current": r.id == request.state.session_id} for r in rows]


@router.delete("/auth/devices/{public_id}")
def revoke_device(public_id: str, request: Request):
    aid = require_account(request)
    with session() as s:
        row = s.exec(select(AccountSession).where(AccountSession.public_id == public_id,
                                                  AccountSession.account_id == aid)).first()
        if not row:
            raise HTTPException(404, "not found")
        row.revoked_at = now()
        s.add(row)
        s.commit()
    return {"ok": True}
