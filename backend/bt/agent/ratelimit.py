"""YouTube request throttle + circuit breaker for the worker.

* one request at a time (the worker is single-threaded),
* jittered delay `min_delay_s..max_delay_s` since the previous worker request,
* hourly / daily caps counted from `YtRequest` rows (origin=worker),
* quiet hours (local time, "HH:MM-HH:MM", may wrap midnight),
* hold-off while someone is playing (throttle.pause_while_playing + control.playing_until),
* circuit breaker on YtBlocked: backoff = min(backoff_max, backoff_min * 2**n), n/until persisted in agent_state.

Settings are re-read on every check so UI edits apply live.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import col, func, select

from ..db import session
from ..models import Category, YtRequest, now
from .runtime import Runtime, aware, iso
from . import timing


FOLLOW_UP_DELAY_S = (1.0, 4.0)  # gap between requests for one video page, capped by the configured delays


def quiet_remaining(spec: str, local_now: Optional[datetime] = None) -> float:
    """Seconds until quiet hours end (0 if not in quiet hours or spec empty/invalid)."""
    if not spec or "-" not in spec:
        return 0.0
    try:
        a, b = (x.strip() for x in spec.split("-", 1))
        ah, am = (int(x) for x in a.split(":"))
        bh, bm = (int(x) for x in b.split(":"))
    except ValueError:
        return 0.0
    t = local_now or datetime.now()
    start = t.replace(hour=ah, minute=am, second=0, microsecond=0)
    end = t.replace(hour=bh, minute=bm, second=0, microsecond=0)
    if start == end:
        return 0.0
    if start < end:
        return (end - t).total_seconds() if start <= t < end else 0.0
    # wraps midnight, e.g. 23:00-07:00
    if t >= start:
        return (end + timedelta(days=1) - t).total_seconds()
    if t < end:
        return (end - t).total_seconds()
    return 0.0


def _window(since: datetime) -> tuple[int, Optional[datetime]]:
    with session() as s:
        q = select(func.count(), func.min(YtRequest.ts)).where(YtRequest.origin == "worker", YtRequest.ts >= since)
        n, oldest = s.exec(q).one()
        return int(n or 0), aware(oldest)


def _last_request() -> Optional[datetime]:
    with session() as s:
        return aware(s.exec(select(YtRequest.ts).where(YtRequest.origin == "worker")
                            .order_by(col(YtRequest.ts).desc())).first())


class RateLimiter:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.last_at: Optional[datetime] = None
        self._next_at: Optional[datetime] = None
        self._next_follow_up = False

    def budget(self) -> dict:
        t = now()
        return {"hour": _window(t - timedelta(hours=1))[0], "day": _window(t - timedelta(days=1))[0]}

    def blocker(self) -> Optional[tuple[float, str, str]]:
        """Long-lived reason YouTube may not be used now: (seconds, state, detail) or None.

        Does not include the short per-request jitter delay (handled in wait_turn)."""
        rt, th, t = self.rt, self.rt.throttle(), now()
        if rt.backoff_until:
            if rt.backoff_until > t:
                # no clock time here: the frontend shows the end in the viewer's timezone
                return ((rt.backoff_until - t).total_seconds(), "backoff",
                        "YouTube bot-check — backing off")
            rt.backoff_until = None  # expired; n is kept until the next success
        q = quiet_remaining(th.get("quiet_hours") or "")
        if q > 0:
            return q, "sleeping", f"Quiet hours ({th['quiet_hours']})"
        p = rt.playing(th=th)
        if p:
            return (p - t).total_seconds(), "waiting", "Someone is watching — holding YouTube requests"
        for span, cap, label in ((timedelta(hours=1), th.get("max_per_hour") or 0, "hourly"),
                                 (timedelta(days=1), th.get("max_per_day") or 0, "daily")):
            if cap <= 0:
                continue
            n, oldest = _window(t - span)
            if n >= cap:
                secs = max(1.0, ((oldest or t) + span - t).total_seconds())
                return secs, "sleeping", f"YouTube {label} budget used ({n}/{cap})"
        return None

    def wait_blocker(self, max_wait: Optional[float] = None, category: Optional[Category] = None) -> bool:
        """Sleep while a blocker is active (at most max_wait). Returns True if a blocker was found."""
        b = self.blocker()
        if not b:
            return False
        secs, state, detail = b
        wait = secs if max_wait is None else min(secs, max_wait)

        def done(ctl) -> bool:
            if state == "backoff":
                return bool(ctl.get("backoff_reset")) or self.rt.backoff_until is None
            if state == "waiting":
                return self.rt.playing(ctl) is None
            return False

        with timing.span("limiter_wait", category_id=category.id if category else None,
                         profile_id=category.profile_id if category else None):
            self.rt.sleep(wait, state, detail, category, until_check=done,
                          until_end=iso(now() + timedelta(seconds=secs)))
        return True

    def wait_turn(self, kind: str, category: Optional[Category] = None, *, follow_up: bool = False,
                  **state_kw) -> None:
        """Block until a worker YouTube request of `kind` may be made. Raises Interrupt on pause/stop.

        `follow_up`: the request belongs to the same video page as the previous one (captions and comments
        after its metadata), so it waits a short browser-like gap instead of a full jittered turn.
        Blockers (backoff, quiet hours, playback, hourly/daily caps) apply either way."""
        rt = self.rt
        while True:
            rt.check()
            if self.wait_blocker(category=category):
                continue
            th = rt.throttle()
            if self._next_follow_up != follow_up:
                self._next_at = None  # an interrupted short gap must not carry over to a full turn
            if self._next_at is None:
                self._next_follow_up = follow_up
                last = max(filter(None, [self.last_at, _last_request()]), default=None)
                lo, hi = float(th.get("min_delay_s") or 0), float(th.get("max_delay_s") or 0)
                if follow_up:
                    lo, hi = min(lo, FOLLOW_UP_DELAY_S[0]), min(max(lo, hi), FOLLOW_UP_DELAY_S[1])
                delay = rt.rng.uniform(lo, max(lo, hi))
                self._next_at = (last + timedelta(seconds=delay)) if last else now()
            left = (self._next_at - now()).total_seconds()
            if left > 0:
                with timing.span("limiter_wait", category_id=category.id if category else None,
                                 profile_id=category.profile_id if category else None):
                    rt.sleep(left, "sleeping", f"Throttle: next YouTube request ({kind}) in {int(left + 0.5)}s",
                             category, **state_kw)
                continue
            self._next_at = None
            return

    def success(self) -> None:
        self.last_at = now()
        if self.rt.backoff_n or self.rt.backoff_until:
            self.rt.reset_backoff()

    def failed(self) -> None:
        """A non-blocking error still counts as a request for spacing purposes."""
        self.last_at = now()

    def blocked(self, err: Exception, category: Optional[Category] = None) -> float:
        rt, th = self.rt, self.rt.throttle()
        self.last_at = now()
        lo, hi = float(th.get("backoff_min_s") or 3600), float(th.get("backoff_max_s") or 21600)
        secs = min(hi, lo * (2 ** rt.backoff_n))
        rt.backoff_n += 1
        rt.backoff_until = now() + timedelta(seconds=secs)
        human = f"{secs / 3600:.1f}h".replace(".0h", "h") if secs >= 3600 else f"{int(secs // 60)} min"
        rt.event("backoff", f"🛑 YouTube bot-check — backing off {human}", "error", category,
                 payload={"error": str(err)[:500], "seconds": secs, "n": rt.backoff_n, "until": iso(rt.backoff_until)})
        rt.state("backoff", "YouTube bot-check — backing off", category,
                 until=iso(rt.backoff_until))
        return secs
