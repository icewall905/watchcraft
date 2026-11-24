"""Optional household profile PINs, independent of the shared app password."""
import hashlib
import hmac
import re
import secrets
import threading
import time

from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlmodel import Session

from .config import SECRET
from .models import Setting

_signer = URLSafeSerializer(SECRET, salt='bt-profile-pin')
# One unlock attempt at a time: the lockout check, the hash and the failure count must not interleave, or parallel
# guesses all read failures=0 and the lockout never trips. Only the bt-api process serves unlocks.
_unlock_lock = threading.Lock()


def key(pid: int) -> str:
    return f'profile_pin_{pid}'


def record(s: Session, pid: int):
    return s.get(Setting, key(pid))


def version(entry):
    return hashlib.sha256(entry.value['hash'].encode()).hexdigest()


def locked(s: Session, pid: int, request: Request) -> bool:
    entry = record(s, pid)
    if not entry:
        return False
    try:
        token = _signer.loads(request.cookies.get(key(pid), ''))
        return token != {'id': pid, 'version': version(entry)}
    except (BadSignature, KeyError):
        return True


def require(s: Session, pid: int, request: Request):
    if locked(s, pid, request):
        raise HTTPException(403, 'profile PIN required')


def validate(pin: str | None):
    if pin and not re.fullmatch(r'[0-9]{4,8}', pin):
        raise HTTPException(400, 'Use a PIN with 4 to 8 digits.')


def set_pin(s: Session, pid: int, pin: str):
    validate(pin)
    old = record(s, pid)
    if not pin:
        if old:
            s.delete(old)
        return
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 260_000).hex()
    s.merge(Setting(key=key(pid), value={'salt': salt, 'hash': digest, 'failures': 0, 'retry_at': 0}))


def unlock_cookie(s: Session, pid: int, response: Response):
    entry = record(s, pid)
    if entry:
        response.set_cookie(key(pid), _signer.dumps({'id': pid, 'version': version(entry)}), httponly=True, samesite='lax')
    else:
        response.delete_cookie(key(pid))


def unlock(s: Session, pid: int, pin: str, response: Response):
    validate(pin)
    with _unlock_lock:
        s.rollback()  # end any read snapshot taken before the lock, so the failure count read below is current
        entry = record(s, pid)
        if not entry:
            return
        value = dict(entry.value)
        if value.get('retry_at', 0) > time.time():
            raise HTTPException(429, 'Too many attempts. Wait 30 seconds and try again.')
        digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(value['salt']), 260_000).hex()
        if not hmac.compare_digest(digest, value['hash']):
            value['failures'] = value.get('failures', 0) + 1
            if value['failures'] >= 5:
                value['failures'] = 0
                value['retry_at'] = time.time() + 30
            entry.value = value
            s.add(entry)
            s.commit()
            raise HTTPException(403, 'Incorrect PIN. Try again.')
        entry.value = {**value, 'failures': 0, 'retry_at': 0}
        s.add(entry)
        s.commit()
    unlock_cookie(s, pid, response)
