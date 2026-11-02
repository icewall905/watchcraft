"""Overlap learning/judging with one video research; then discover if needed."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
import traceback
from typing import Optional

from sqlmodel import col, select

from ..db import session
from ..models import Candidate, Video
from . import data, enrich, judge, learn, memory, planner, timing
from .ratelimit import RateLimiter
from ..models import now
from .runtime import Disabled, Interrupt, Paused, Runtime, Stopped

log = logging.getLogger("bt.agent")

IDLE_S = 300  # nothing to do: re-check after this (run_now / new feedback wake earlier)
YT_WAIT_CHUNK_S = 60  # while YouTube is blocked (backoff/quiet/playing/cap), wake this often for LLM work
ERROR_SLEEP_S = 30
IDLE_MIN_S = 5  # idle never wakes for feedback sooner than this (prevents busy loops)


class Worker:
    def __init__(self, rt: Optional[Runtime] = None, **kw):
        self.rt = rt or Runtime(**kw)
        self.rl = RateLimiter(self.rt)
        self._paused_announced = False

    # ------------------------------------------------------------------ one iteration
    def iterate(self) -> bool:
        """Run one pass of the pipeline. Returns True if any work was done (or a YT wait happened)."""
        rt = self.rt
        rt.check()
        rt.take_run_now()
        if self._paused_announced:
            self._paused_announced = False
            rt.event("control", "▶️ Resumed", "info")
        did = False

        # Research and AI have independent resources; keep one task per lane.
        # Join before discovery so planning sees all newly accepted picks.
        started = monotonic()
        trace_id = timing.new_trace_id()
        research_done = threading.Event()
        with timing.span("pass", trace_id=trace_id):
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="curator-ai") as pool:
                ai = pool.submit(self._timed_ai, research_done, trace_id)
                try:
                    did = self._research_one(trace_id)
                finally:
                    research_done.set()
                did = ai.result() or did
        log.info("curator research/judge pass finished in %.2fs", monotonic() - started)

        # (3)+(4) YouTube work
        rt.check()
        with session() as s:
            needs = data.category_needs(s, rt.focus_category_id)
        if not needs:
            return did
        # If a cached video or AI work just advanced, run another pass first:
        # an external blocker must not delay network-free research or judging.
        if self.rl.blocker() and did:
            return True
        if self.rl.wait_blocker(max_wait=YT_WAIT_CHUNK_S, category=needs[0].cat):
            return True
        yt = rt.yt
        cat = None
        try:
            rt.check()
            with session() as s:
                needs = data.category_needs(s, rt.focus_category_id)
            for need in needs:
                pend = need.counts.get("found", 0) + need.counts.get("enriched", 0)
                if pend < data.LOW_WATER:
                    cat = need.cat
                    with timing.span("discovery", trace_id=trace_id, category_id=cat.id, profile_id=cat.profile_id):
                        res = planner.discover(rt, self.rl, cat)
                    if rt.focus_category_id == cat.id:
                        rt.focus_category_id = None
                    if res is not None:
                        did = True
                        break
        except planner._Skip:
            did = True
        except Exception as e:  # noqa: BLE001
            if isinstance(e, yt.YtBlocked):
                self.rl.blocked(e, cat)
                return True
            raise
        return did

    def _timed_ai(self, research_done: threading.Event, trace_id: str) -> bool:
        with timing.span("ai", trace_id=trace_id):
            return self._ai_work(research_done, trace_id)

    def _ai_work(self, research_done: threading.Event | None = None, trace_id: str | None = None) -> bool:
        rt = self.rt
        did = False
        # (1) learn — LLM only, highest priority
        if rt.llm_ok() and learn.process(rt):
            did = True

        if rt.llm_ok() and not learn.pending_count() and memory.process(rt):
            did = True

        # (2) judge everything already enriched
        for cand in judge.pending():
            rt.check()
            if not rt.llm_ok():
                break
            with timing.span("judgment", trace_id=trace_id, candidate_id=cand.id, category_id=cand.category_id) as measured:
                verdict = judge.judge_one(rt, cand)
                if verdict:
                    measured.result = verdict
                    did = True
                else:
                    measured.outcome = "error"
            if learn.pending_count() and rt.llm_ok():  # feedback arrived meanwhile → learn first
                learn.process(rt)
            # Keep the AI busy while research is running, but after research
            # finishes yield the lane after this verdict so the next video can
            # start. The first verdict is always allowed to avoid starvation.
            if research_done is not None and research_done.is_set():
                break

        return did

    def _research_one(self, trace_id: str | None = None) -> bool:
        rt = self.rt
        rt.check()
        with session() as s:
            needs = data.category_needs(s, rt.focus_category_id)
        # Cached research requires no YouTube request and can advance during
        # backoff, quiet hours, playback or exhausted request budgets.
        blocked = bool(self.rl.blocker())
        cand = self._next_found(needs, cached_only=blocked)
        if cand is None:
            return False
        cat = next(n.cat for n in needs if n.cat.id == cand.category_id)
        try:
            with timing.span("research", trace_id=trace_id, candidate_id=cand.id,
                             category_id=cat.id, profile_id=cat.profile_id) as measured:
                result = enrich.enrich_one(rt, self.rl, cand, cat)
                if result == "error":
                    measured.outcome = "error"
                elif result == "gone":
                    measured.outcome = "cancelled"
        except rt.yt.YtBlocked as e:
            self.rl.blocked(e, cat)
        return True

    @staticmethod
    def _next_found(needs: list[data.Need], *, cached_only: bool = False) -> Optional[Candidate]:
        with session() as s:
            for need in needs:
                query = select(Candidate).where(Candidate.category_id == need.cat.id,
                                                       Candidate.status == "found",
                                                       Candidate.dismissed == False)  # noqa: E712
                if cached_only:
                    query = query.join(Video, Video.id == Candidate.video_id).where(Video.enriched_at != None)  # noqa: E711
                rows = s.exec(query.order_by(col(Candidate.created_at), Candidate.id)).all()
                if rows:
                    # Cached research first, then positive channel feedback.
                    # Every pick still receives the complete quality judgment.
                    def priority(c):
                        v = s.get(Video, c.video_id)
                        trust = data.trust_for(s, need.cat.profile_id, v.channel_id) if v and v.channel_id else 0
                        return (bool(v and v.enriched_at), max(0, trust or 0))
                    return max(rows, key=priority)  # stable: FIFO for ties
        return None

    # ------------------------------------------------------------------ forever
    def _idle(self) -> None:
        """Nothing to do: sleep (always > 0). Wake early on run_now, or on new feedback once the LLM is usable."""
        rt = self.rt
        secs = IDLE_S
        detail = "All queues are full — checking again soon"
        if not rt.llm_ok():
            secs = max(IDLE_MIN_S, min(IDLE_S, (rt.llm_backoff_until - now()).total_seconds()))
            detail = "Waiting for the AI model to become available"
        rt.sleep(secs, "idle", detail, wake_on_run_now=True, min_s=IDLE_MIN_S,
                 until_check=lambda ctl: rt.llm_ok() and (learn.pending_count() > 0 or bool(ctl.get("memory_run_now"))))

    def _paused(self) -> None:
        rt = self.rt
        if not self._paused_announced:
            self._paused_announced = True
            rt.event("control", "⏸️ Paused — no YouTube or AI calls until resumed", "info")
        rt.state("paused", "Paused by user")
        while not rt.stop_requested and rt.control().get("paused"):
            rt._sleep(1.0)

    def _disabled(self) -> None:
        rt = self.rt
        rt.state("idle", "AI curator disabled in settings")
        while not rt.stop_requested and not rt.settings()["agent"].get("enabled", True):
            if rt.control().get("paused"):
                return
            rt._sleep(2.0)

    def _step(self) -> None:
        try:
            if not self.iterate():
                self._idle()
        except Paused:
            self._paused()
        except Disabled:
            self._disabled()

    def _on_error(self, e: Exception) -> None:
        rt = self.rt
        log.error("worker iteration failed: %s", e, exc_info=e)
        try:
            rt.event("error", f"💥 Worker error: {e}"[:500], "error",
                     payload={"traceback": "".join(traceback.format_exception(e))[-2000:]})
            rt.sleep(ERROR_SLEEP_S, "error", f"{type(e).__name__}: {e}"[:300], wake_on_run_now=True)
        except Interrupt:
            pass  # pause/disable/stop are handled by the next _step / loop condition
        except Exception:  # noqa: BLE001 — e.g. database locked; never let the worker die
            log.exception("error handler failed")
            rt._sleep(5.0)

    def run(self) -> None:
        rt = self.rt
        from ..config import DATA_DIR
        timing.configure(DATA_DIR)
        memory.recover()
        try:
            rt.event("control", "🤖 Curator started", "info")
            rt.state("idle", "Starting")
        except Exception:  # noqa: BLE001
            log.exception("failed to write start state")
        try:
            while not rt.stop_requested:
                try:
                    self._step()
                except Stopped:
                    break
                except Exception as e:  # noqa: BLE001
                    self._on_error(e)
        finally:
            try:
                rt.state("offline", "Worker stopped")
                rt.event("control", "🔌 Curator stopped", "info")
            except Exception:  # noqa: BLE001
                log.exception("failed to write offline state")
