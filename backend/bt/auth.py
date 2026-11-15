"""Optional shared-passphrase auth (BT_PASSWORD). Signed cookie session, 1 year."""
import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel

from .config import PASSWORD, SECRET

COOKIE = "bt_session"
_ser = URLSafeSerializer(SECRET, salt="bt-auth")
router = APIRouter()

# API paths reachable without a session (everything non-/api is the SPA shell).
OPEN = {"/api/health", "/api/auth/login", "/api/auth/status"}


def _authed(request: Request) -> bool:
    if not PASSWORD:
        return True
    try:
        return _ser.loads(request.cookies.get(COOKIE, "")) == "ok"
    except BadSignature:
        return False


class AuthMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming proxy/SSE responses pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            p = scope["path"]
            # /api/ext/v1/ is the browser extension: it authenticates with its own key (bt/hub/extension.py).
            # /api/public/ serves capability links (bt/hub/moodboard_share.py): the token in the path is the credential.
            if (p.startswith("/api/") and p not in OPEN and not p.startswith(("/api/ext/v1/", "/api/public/"))
                    and not _authed(Request(scope))):
                return await JSONResponse({"detail": "auth required"}, status_code=401)(scope, receive, send)
        await self.app(scope, receive, send)


class Login(BaseModel):
    password: str


@router.get("/auth/status")
def status(request: Request):
    return {"required": bool(PASSWORD), "authenticated": _authed(request)}


@router.post("/auth/login")
def login(body: Login, response: Response):
    if PASSWORD and not hmac.compare_digest(body.password, PASSWORD):
        raise HTTPException(401, "wrong password")
    response.set_cookie(COOKIE, _ser.dumps("ok"), max_age=365 * 86400, httponly=True, samesite="lax")
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    response.delete_cookie(COOKIE)
    for name in request.cookies:
        if name.startswith("profile_pin_"):
            response.delete_cookie(name)
    return {"ok": True}
