"""Cast grants: a screen plays a Marquee title as the profile that sent it.

The screen may be signed in to another account (a shared "tv" account), so it cannot switch to the
sender's profile. The command it receives carries a signed, short-lived grant instead; Jellyfin
requests with that grant act as the sender. Only the paired screen gets it (via its receiver token),
and the sender had to pair with that screen first.
"""
from __future__ import annotations

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..config import SECRET

HEADER = 'x-cast-grant'
TTL_S = 6 * 3600  # long enough for a film plus a few episodes after it
_sig = URLSafeTimedSerializer(SECRET, salt='jellyfin-cast')


def grant(pid: int, target_id: str) -> str:
    return _sig.dumps({'p': pid, 't': target_id})


def grant_pid(token: str | None) -> int | None:
    """The sending profile of a valid, unexpired grant, else None."""
    if not token:
        return None
    try:
        data = _sig.loads(token, max_age=TTL_S)
    except BadSignature:  # includes SignatureExpired
        return None
    p = data.get('p') if isinstance(data, dict) else None
    return p if isinstance(p, int) else None
