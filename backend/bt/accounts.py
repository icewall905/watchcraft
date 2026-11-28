"""Accounts, sessions, invites and TV device codes (docs/ACCOUNTS.md).

Passwords use stdlib scrypt. Session, invite and device-poll tokens are random and stored only
as SHA-256. Sessions never expire on their own; see SESSION_IDLE.
"""
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session, select

from . import config
from .models import Account, AccountInvite, AccountSession, DeviceCode, Profile, now

ACCOUNT_KEYS = frozenset({'appearance', 'player', 'sponsorblock', 'extras'})
SESSION_IDLE = timedelta(days=3 * 365)  # unused this long → signed out
COOKIE_MAX_AGE = 400 * 86400  # browser cap; re-sent daily while in use
COOKIE_REFRESH = timedelta(days=1)
SEEN_EVERY = timedelta(minutes=10)
INVITE_TTL = timedelta(days=14)
DEVICE_TTL = timedelta(minutes=10)
SESSION_COOKIE = 'wc_s_'
ACTIVE_COOKIE = 'wc_active'
PROFILE_COOKIE = 'wc_profile_'
USERNAME = re.compile(r'[a-z0-9][a-z0-9._-]{1,31}')
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no 0/O/1/I


def is_open() -> bool:
    return config.AUTH_MODE == 'open'


def utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; everything here is UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024)
    return f'scrypt$15$8$1${salt.hex()}${dk.hex()}'


def check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split('$')
        if algo != 'scrypt':
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


_DUMMY = None


def burn_time(password: str) -> None:
    """Same cost as a real check, so unknown usernames don't answer faster."""
    global _DUMMY
    _DUMMY = _DUMMY or hash_password('dummy-password')
    check_password(password, _DUMMY)


def validate_password(password: str) -> None:
    if len(password or '') < 8:
        raise HTTPException(400, 'Use a password with at least 8 characters.')
    if len(password) > 256:
        raise HTTPException(400, 'That password is too long.')


def normalize_username(name: str) -> str:
    name = (name or '').strip().lower()
    if not USERNAME.fullmatch(name) or name.startswith('pending-'):
        raise HTTPException(400, 'Usernames are 2–32 characters: letters, digits, dot, dash or underscore.')
    return name


# ---------- login throttling (per api process)

_failures: dict[str, list[float]] = {}
FAIL_LIMIT = 5
FAIL_WINDOW = 300.0
LOGIN_USER_LIMIT = 10  # wrong passwords per username per window
LOGIN_IP_LIMIT = 20  # per address: a household shares one IP across several people


def throttle_check(*keys: str, limit: int = FAIL_LIMIT) -> None:
    t = time.time()
    for k in keys:
        recent = [x for x in _failures.get(k, []) if t - x < FAIL_WINDOW]
        _failures[k] = recent
        if len(recent) >= (LOGIN_IP_LIMIT if k.startswith('login-ip:') else limit):
            wait = int(FAIL_WINDOW - (t - recent[0])) + 1
            raise HTTPException(429, f'Too many attempts. Try again in {max(wait // 60, 1)} minute(s).')


def throttle_fail(*keys: str) -> None:
    for k in keys:
        _failures.setdefault(k, []).append(time.time())


def throttle_clear(*keys: str) -> None:
    for k in keys:
        _failures.pop(k, None)


# ---------- sessions

def create_session(s: Session, account: Account, device_label: str = '', user_agent: str = '') -> tuple[AccountSession, str]:
    token = secrets.token_urlsafe(32)
    row = AccountSession(public_id=secrets.token_urlsafe(9), token_hash=digest(token), account_id=account.id,
                         device_label=(device_label or '')[:80], user_agent=(user_agent or '')[:300])
    s.add(row)
    s.commit()
    s.refresh(row)
    return row, token


def session_cookies(request_cookies: dict) -> dict[str, str]:
    """public_id → token for every session cookie the client sent."""
    return {k[len(SESSION_COOKIE):]: v for k, v in request_cookies.items() if k.startswith(SESSION_COOKIE) and v}


def valid_session(s: Session, public_id: str, token: str) -> Optional[tuple[AccountSession, Account]]:
    row = s.exec(select(AccountSession).where(AccountSession.token_hash == digest(token))).first()
    if not row or row.public_id != public_id or row.revoked_at is not None:
        return None
    if utc(row.last_seen_at) < now() - SESSION_IDLE:
        return None
    acc = s.get(Account, row.account_id)
    if not acc or acc.disabled or not acc.password_hash:
        return None
    return row, acc


def resolve(s: Session, cookies: dict, header_account: str = '') -> Optional[tuple[AccountSession, Account]]:
    """The active session: X-Account header (no fallback: a tab never silently acts as another
    account), else the wc_active cookie, else any valid session cookie."""
    jar = session_cookies(cookies)
    if header_account:
        return valid_session(s, header_account, jar[header_account]) if header_account in jar else None
    order = [x for x in (header_account, cookies.get(ACTIVE_COOKIE, '')) if x and x in jar]
    order += [x for x in jar if x not in order]
    for pid in order:
        found = valid_session(s, pid, jar[pid])
        if found:
            return found
    return None


def signed_in(s: Session, cookies: dict) -> list[tuple[AccountSession, Account]]:
    out = []
    for pid, token in session_cookies(cookies).items():
        found = valid_session(s, pid, token)
        if found:
            out.append(found)
    return out


def touch(s: Session, row: AccountSession) -> bool:
    """Throttled last_seen update. Returns True when the cookie should be re-sent (daily)."""
    seen = utc(row.last_seen_at)
    t = now()
    refresh = seen < t - COOKIE_REFRESH
    if seen < t - SEEN_EVERY:
        row.last_seen_at = t
        s.add(row)
        s.commit()
    return refresh


def revoke_account_sessions(s: Session, account_id: int, keep: Optional[int] = None) -> None:
    for row in s.exec(select(AccountSession).where(AccountSession.account_id == account_id,
                                                    AccountSession.revoked_at == None)).all():  # noqa: E711
        if row.id != keep:
            row.revoked_at = now()
            s.add(row)


# ---------- invites

def create_invite(s: Session, account: Account, purpose: str, created_by: Optional[int] = None) -> str:
    for old in s.exec(select(AccountInvite).where(AccountInvite.account_id == account.id,
                                                   AccountInvite.used_at == None)).all():  # noqa: E711
        old.used_at = now()  # a new link replaces older unused ones
        s.add(old)
    token = 'wcj_' + secrets.token_urlsafe(24)
    s.add(AccountInvite(token_hash=digest(token), account_id=account.id, purpose=purpose, created_by=created_by,
                        expires_at=now() + INVITE_TTL))
    s.commit()
    return token


def open_invite(s: Session, account_id: int) -> Optional[AccountInvite]:
    return s.exec(select(AccountInvite).where(AccountInvite.account_id == account_id, AccountInvite.used_at == None,  # noqa: E711
                                              AccountInvite.expires_at > now())).first()


def find_invite(s: Session, token: str) -> tuple[AccountInvite, Account]:
    inv = s.exec(select(AccountInvite).where(AccountInvite.token_hash == digest(token or ''))).first()
    if not inv or inv.used_at is not None or utc(inv.expires_at) < now():
        raise HTTPException(404, 'This link is no longer valid. Ask an administrator for a new one.')
    acc = s.get(Account, inv.account_id)
    if not acc or acc.disabled:
        raise HTTPException(404, 'This link is no longer valid. Ask an administrator for a new one.')
    return inv, acc


def invite_link(token: str) -> str:
    return f'/join/{token}'


def new_account(s: Session, username: str, role: str, placeholder: bool = False) -> Account:
    """placeholder: invite without a username yet ('pending-…'); the person picks one when registering."""
    if role not in ('admin', 'user'):
        raise HTTPException(400, 'role must be admin or user')
    name = 'pending-' + secrets.token_hex(4) if placeholder else normalize_username(username)
    if s.exec(select(Account).where(Account.username == name)).first():
        raise HTTPException(409, 'That username is taken.')
    acc = Account(username=name, role=role)
    s.add(acc)
    s.commit()
    s.refresh(acc)
    return acc


def ensure_profile(s: Session, acc: Account) -> None:
    """A newly registered account starts with one profile named after it."""
    if not s.exec(select(Profile.id).where(Profile.account_id == acc.id)).first():
        s.add(Profile(name=acc.username.capitalize(), emoji='🙂', role='adult', account_id=acc.id))
        s.commit()


def bootstrap_open(s: Session) -> bool:
    """First admin may be created from the sign-in screen only on a fresh install."""
    if is_open() or s.exec(select(Account.id)).first():
        return False
    return not s.exec(select(Profile.id).where(Profile.account_id == None)).first()  # noqa: E711


# ---------- device codes

def new_device_code(s: Session, device_label: str, user_agent: str) -> tuple[str, str]:
    for old in s.exec(select(DeviceCode).where(DeviceCode.expires_at < now() - timedelta(hours=1))).all():
        s.delete(old)
    if len(s.exec(select(DeviceCode.id).where(DeviceCode.expires_at > now(), DeviceCode.claimed == False)).all()) >= 50:  # noqa: E712
        raise HTTPException(429, 'Too many sign-in codes are waiting. Try again in a few minutes.')
    while True:
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not s.exec(select(DeviceCode).where(DeviceCode.code == code)).first():
            break
    poll = secrets.token_urlsafe(24)
    s.add(DeviceCode(code=code, poll_hash=digest(poll), device_label=(device_label or '')[:80],
                     user_agent=(user_agent or '')[:300], expires_at=now() + DEVICE_TTL))
    s.commit()
    return code, poll


def clean_code(code: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', (code or '').upper())


__all__ = ['ACCOUNT_KEYS', 'is_open', 'hash_password', 'check_password', 'create_session', 'resolve']
