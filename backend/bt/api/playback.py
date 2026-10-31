"""Browser playback receivers. Commands are short-lived, acknowledged, paired mailboxes."""
import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text, update
from sqlmodel import Session, select

from ..db import get_session
from ..models import Category, PlaybackTarget, Profile
from .core import profile_id

router = APIRouter(prefix="/playback")
ONLINE_SECONDS = 20
COMMAND_SECONDS = 30
MAX_TARGETS = 10
MAX_PAIRED = 20
SEND_WINDOW = 10.0
SEND_LIMIT = 30
PAIR_WINDOW = 600.0
PAIR_FAILS = 5


class Registration(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class PairCode(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class Heartbeat(BaseModel):
    ack: str = ""
    video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")
    paused: bool = True
    position_s: float = Field(default=0, ge=0, le=864000, allow_inf_nan=False)
    error: str = Field(default="", max_length=200)


class Command(BaseModel):
    action: str = Field(pattern=r"^(play|pause|resume|stop)$")
    video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")
    category_id: int | None = None


def _pair(target) -> dict:
    p = target.pair or {}
    return {"code": str(p.get("code") or ""), "profiles": [int(x) for x in p.get("profiles", [])]}


def _paired(target, pid: int) -> bool:
    return pid in _pair(target)["profiles"]


def _household(s, pid: int) -> Profile | None:
    profile = s.get(Profile, pid)
    if not profile or profile.role == "child":
        return None
    return profile


def _public(target, pid: int):
    return {"id": target.id, "name": target.name,
            "online": time.time() - target.last_seen < ONLINE_SECONDS, "state": target.state,
            "paired": _paired(target, pid)}


def _receiver(s, target_id, token):
    target = s.get(PlaybackTarget, target_id)
    digest = hashlib.sha256((token or "").encode()).hexdigest()
    if not target or not hmac.compare_digest(target.token_hash, digest):
        raise HTTPException(403, "Invalid playback receiver credentials")
    return target


def _target(s, target_id):
    target = s.get(PlaybackTarget, target_id)
    if not target:
        raise HTTPException(404, "Playback target no longer exists")
    return target


@router.get("/targets")
def targets(pid: int = Depends(profile_id), s: Session = Depends(get_session)):
    if s.get(Profile, pid).role == "child":
        return []
    return [_public(t, pid) for t in s.exec(select(PlaybackTarget).order_by(PlaybackTarget.name)).all()]


@router.post("/targets")
def register(body: Registration, s: Session = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Enter a screen name")
    # Serialize the cap check and row creation so two screens cannot exceed it together.
    s.exec(text("BEGIN IMMEDIATE"))
    if len(s.exec(select(PlaybackTarget.id)).all()) >= MAX_TARGETS:
        raise HTTPException(422, "Maximum 10 registered screens. Unregister one first.")
    token = secrets.token_urlsafe(32)
    pair = {"code": str(secrets.randbelow(900000) + 100000), "profiles": []}
    target = PlaybackTarget(id=secrets.token_urlsafe(16), name=name,
                            token_hash=hashlib.sha256(token.encode()).hexdigest(),
                            last_seen=time.time(), pair=pair)
    s.add(target)
    s.commit()
    return {**_public(target, -1), "token": token, "pair_code": pair["code"]}


@router.post("/targets/{target_id}/pair")
def pair(target_id: str, body: PairCode, pid: int = Depends(profile_id),
         s: Session = Depends(get_session)):
    """Enter the pairing code shown on the screen to allow this profile to command it."""
    s.exec(text("BEGIN IMMEDIATE"))
    target = _target(s, target_id)
    if not _household(s, pid):
        raise HTTPException(403, "Open household profiles pair with screens; children cannot.")
    info = {**(target.pair or {}), **_pair(target)}
    now = time.time()
    fails = [f for f in info.get("fails", []) if now - f < PAIR_WINDOW]
    if len(fails) >= PAIR_FAILS:
        raise HTTPException(429, "Too many wrong pairing codes for this screen. Try again in a few minutes.")
    if not info["code"] or not hmac.compare_digest(info["code"], body.code.strip()):
        # Count the miss before refusing so a 6-digit code cannot be guessed by trying them all.
        target.pair = {**info, "fails": fails + [now]}
        s.add(target)
        s.commit()
        raise HTTPException(403, "That pairing code does not match this screen.")
    if pid not in info["profiles"]:
        if len(info["profiles"]) >= MAX_PAIRED:
            raise HTTPException(422, "This screen is paired with too many profiles.")
        info["profiles"].append(pid)
        target.pair = info
        s.add(target)
        s.commit()
    return {"id": target.id, "paired": True}


@router.post("/targets/{target_id}/heartbeat")
def heartbeat(target_id: str, body: Heartbeat, x_receiver_token: str = Header(default=""),
              s: Session = Depends(get_session)):
    target = _receiver(s, target_id, x_receiver_token)
    command = target.command
    # Update only receiver-owned columns; a concurrent sender must not be overwritten.
    s.exec(update(PlaybackTarget).where(PlaybackTarget.id == target_id).values(
        last_seen=time.time(), state=body.model_dump(exclude={"ack"})))
    s.commit()
    pending = command if (command and command["id"] != body.ack
                          and time.time() - command["created_at"] < COMMAND_SECONDS) else None
    # The code lets upgraded screens rediscover their pairing code after registration.
    return {"command": pending, "pair_code": _pair(target)["code"]}


@router.post("/targets/{target_id}/commands")
def send_command(target_id: str, body: Command, pid: int = Depends(profile_id),
                 s: Session = Depends(get_session)):
    # Serialize pairing and the rate window so two senders cannot exceed SEND_LIMIT together.
    s.exec(text("BEGIN IMMEDIATE"))
    target = _target(s, target_id)
    if not _household(s, pid):
        raise HTTPException(403, "Open household profiles control connected screens; children cannot.")
    if not _paired(target, pid):
        raise HTTPException(403, "Pair this screen once in the Play on picker before sending.")
    if time.time() - target.last_seen >= ONLINE_SECONDS:
        raise HTTPException(409, "This screen is offline. Open BetterTube on it first.")
    if body.action == "play" and not body.video_id:
        raise HTTPException(422, "Choose a video to play")
    if body.category_id is not None:
        category = s.get(Category, body.category_id)
        if not category or category.profile_id != pid:
            raise HTTPException(404, "Playlist not found")
    now = time.time()
    window = target.rate or {}
    count = int(window.get("n", 0)) if now - float(window.get("ts", 0)) < SEND_WINDOW else 0
    if count >= SEND_LIMIT:
        raise HTTPException(429, "Too many commands to this screen. Wait a moment.")
    command = {**body.model_dump(), "id": secrets.token_urlsafe(16),
               "profile_id": pid, "created_at": now}
    s.exec(update(PlaybackTarget).where(PlaybackTarget.id == target_id).values(
        command=command, rate={"ts": now if count == 0 else window["ts"], "n": count + 1}))
    s.commit()
    return {"command_id": command["id"]}


@router.delete("/targets/{target_id}")
def unregister(target_id: str, x_receiver_token: str = Header(default=""),
               s: Session = Depends(get_session)):
    target = _receiver(s, target_id, x_receiver_token)
    s.delete(target)
    s.commit()
    return {"ok": True}
