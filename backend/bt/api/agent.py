"""Agent status/control + live SSE stream of AgentEvents."""
import asyncio
import json
from datetime import timedelta
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from ..db import (get_agent_control, get_agent_state, get_kv, get_session, get_settings, session, update_agent_control,
                  yt_request_counts)
from ..models import AgentEvent, Candidate, Category, Profile, now
from ..access import require_admin
from .core import _offline_ids, profile_id

router = APIRouter()


@router.get("/agent/memory")
def memory_status(pid: int = Depends(profile_id), s: Session = Depends(get_session)):
    from ..agent.memory import active, latest, select_memory
    from ..llm_metrics import overview
    from ..models import MemoryCore
    lessons = active(s, pid)
    revision = latest(s, pid)
    core, corrections = select_memory(s, pid, lessons)
    state = get_kv("memory_run", {"state": "idle"}, s)
    if get_agent_control(s).get("memory_run_now"):
        state = {**state, "state": "queued"}
    return {"run": state, "core": revision.text if revision else "", "core_valid": bool(core),
            "core_budget_tokens": revision.budget_tokens if revision else None,
            "core_updated_at": revision.created_at if revision else None,
            "versions": len(s.exec(select(MemoryCore.id).where(MemoryCore.profile_id == pid)).all()),
            "active_rules": len(lessons), "corrections": len(corrections), "context": overview(s)}


@router.post("/agent/memory/run", dependencies=[Depends(require_admin)])
def memory_run():
    if get_kv("memory_run").get("state") == "running":
        return {"queued": False}
    update_agent_control(memory_run_now=True)
    return {"queued": True}


def _queues(s: Session, pid: Optional[int]) -> list[dict]:
    q = select(Category)
    if pid:
        q = q.where(Category.profile_id == pid)
    out = []
    for c in s.exec(q.order_by(Category.position, Category.id)).all():
        cands = s.exec(select(Candidate.status, Candidate.dismissed).where(Candidate.category_id == c.id)).all()
        out.append({
            "category_id": c.id, "name": c.name, "emoji": c.emoji, "color": c.color, "enabled": c.enabled,
            "target": c.target_queue, "last_worked_at": c.last_worked_at,
            "accepted": sum(1 for st, d in cands if st == "accepted" and not d),
            "pending": sum(1 for st, _ in cands if st in ("found", "enriched")),
            "rejected": sum(1 for st, _ in cands if st == "rejected"),
        })
    return out


def status_payload(s: Session, pid: Optional[int] = None) -> dict:
    st = get_settings(s)["throttle"]
    return {
        "state": get_agent_state(s),
        "downloader": get_kv("downloader_state", {"state": "offline"}, s),
        "control": get_agent_control(s),
        "budget": {**yt_request_counts(s), "max_hour": st["max_per_hour"], "max_day": st["max_per_day"]},
        "queues": _queues(s, pid),
    }


@router.get("/agent/status")
def agent_status(pid: int = Depends(profile_id), s: Session = Depends(get_session)):
    return status_payload(s, pid)


@router.get("/agent/events")
def agent_events(after_id: int = 0, limit: int = 200, category_id: Optional[int] = None,
                 s: Session = Depends(get_session)):
    q = select(AgentEvent).where(AgentEvent.id > after_id, AgentEvent.kind.notin_(['source_scout', 'hub_curator']))
    if category_id:
        q = q.where(AgentEvent.category_id == category_id)
    if after_id:
        return s.exec(q.order_by(AgentEvent.id).limit(limit)).all()
    return list(reversed(s.exec(q.order_by(col(AgentEvent.id).desc()).limit(limit)).all()))


@router.get("/agent/stream")
async def agent_stream(request: Request, after_id: int = 0):
    """SSE: `event: state` (agent_state+control+budget, on change) and `event: agent` (each AgentEvent)."""
    def poll(last_id: int, with_state: bool):
        with session() as s:
            if last_id < 0:
                last = s.exec(select(AgentEvent).where(AgentEvent.kind.notin_(['source_scout', 'hub_curator']))
                              .order_by(col(AgentEvent.id).desc())).first()
                return max(0, (last.id if last else 0) - 30), [], None
            evs = s.exec(select(AgentEvent).where(AgentEvent.id > last_id, AgentEvent.kind.notin_(['source_scout', 'hub_curator'])).order_by(AgentEvent.id).limit(100)).all()
            st = None
            if with_state:
                st = status_payload(s)
                st.pop("queues")
            return (evs[-1].id if evs else last_id), evs, st

    async def gen():
        # DB work runs in a thread so a busy SQLite never stalls the event loop (and video proxying).
        last_id = after_id
        if not last_id:
            last_id, _, _ = await run_in_threadpool(poll, -1, False)
        last_state = None
        tick = 0
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            last_id, evs, st = await run_in_threadpool(poll, last_id, tick % 2 == 0)
            for ev in evs:
                yield f"id: {ev.id}\nevent: agent\ndata: {ev.model_dump_json()}\n\n"
            if st is not None:
                blob = json.dumps(st, default=str)
                if blob != last_state:
                    last_state = blob
                    yield f"event: state\ndata: {blob}\n\n"
            tick += 1
            if tick % 30 == 0:
                yield ": ping\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent/pause", dependencies=[Depends(require_admin)])
def pause():
    return update_agent_control(paused=True)


@router.post("/agent/resume", dependencies=[Depends(require_admin)])
def resume():
    return update_agent_control(paused=False)


@router.post("/agent/reset-backoff", dependencies=[Depends(require_admin)])
def reset_backoff():
    """Manually clear a YouTube bot-check backoff (risky: may get blocked again)."""
    return update_agent_control(backoff_reset=True)


class RunNow(BaseModel):
    category_id: Optional[int] = None


@router.post("/agent/run", dependencies=[Depends(require_admin)])
def run_now(body: RunNow):
    """Wake the worker now (skip remaining sleep), optionally focusing one category."""
    return update_agent_control(run_now={"category_id": body.category_id, "at": now().isoformat()})


@router.post("/agent/playing")
def playing(seconds: int = 90):
    """Player heartbeat: worker holds off YouTube requests while someone is watching (if enabled)."""
    return update_agent_control(playing_until=(now() + timedelta(seconds=seconds)).isoformat())


@router.get("/agent/candidates/{cand_id}")
def candidate_detail(cand_id: int, s: Session = Depends(get_session)):
    from ..models import Video
    from .core import candidate_out
    c = s.get(Candidate, cand_id)
    if not c:
        return {"detail": "not found"}
    v = s.get(Video, c.video_id)
    out = candidate_out(c, v, offline=c.video_id in _offline_ids([c.video_id], s))
    category = s.get(Category, c.category_id)
    profile = s.get(Profile, category.profile_id) if category else None
    out.update(profile_id=category.profile_id if category else None,
               category_name=category.name if category else None,
               profile_name=profile.name if profile else None)
    if v:
        out["transcript_excerpt"] = (v.transcript or "")[:1500]
        out["comments"] = (v.comments or [])[:10]
    out["events"] = s.exec(select(AgentEvent).where(AgentEvent.video_id == c.video_id,
                                                    AgentEvent.category_id == c.category_id)
                           .order_by(AgentEvent.id)).all()
    return out


class LlmTest(BaseModel):
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None


def _llm_connection(body: LlmTest, cfg: dict) -> tuple[str, dict]:
    base = (body.base_url if body.base_url is not None else cfg["base_url"]).strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(400, "Enter a valid server URL without credentials or query parameters")
    saved_base = cfg["base_url"].strip().rstrip("/")
    key = body.api_key
    if key == "********":
        if base != saved_base:
            raise HTTPException(400, "Enter the API key for this server; the saved key belongs to another server")
        key = cfg.get("api_key", "")
    elif key is None:
        key = cfg.get("api_key", "") if base == saved_base else ""
    return base, {"Authorization": f"Bearer {key}"} if key else {}


def _models_url(base: str) -> str:
    # OpenCode serves catalogues separately from its translated OpenAI API.
    special = {
        "https://opencode.ai/inference/openai/v1": "https://opencode.ai/inference/v1/models",
        "https://opencode.ai/inference/go/openai/v1": "https://opencode.ai/inference/go/v1/models",
    }
    return special.get(base, f"{base}/models")


async def _list_llm_models(client: httpx.AsyncClient, base: str, headers: dict) -> list[str]:
    anthropic = urlsplit(base).hostname == "api.anthropic.com"
    headers = {**headers, "anthropic-version": "2023-06-01"} if anthropic else headers
    response = await client.get(_models_url(base), headers=headers, params={"limit": 1000} if anthropic else None)
    response.raise_for_status()
    data = response.json()
    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("The server did not return a model list")
    return sorted({entry["id"] for entry in entries
                   if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]})


@router.post("/agent/llm-models", dependencies=[Depends(require_admin)])
async def llm_models(body: LlmTest):
    cfg = (await run_in_threadpool(get_settings))["llm"]
    base, headers = _llm_connection(body, cfg)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            return {"ok": True, "models": await _list_llm_models(client, base, headers)}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "models": [], "error": str(e)}


@router.post("/agent/llm-test", dependencies=[Depends(require_admin)])
async def llm_test(body: LlmTest):
    cfg = (await run_in_threadpool(get_settings))["llm"]
    base, headers = _llm_connection(body, cfg)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            # Some compatible servers do not expose a model listing; testing chat
            # should still work when the user enters a model manually.
            try:
                models = await _list_llm_models(c, base, headers)
            except (httpx.HTTPError, ValueError):
                models = []
            from ..llm import prepare_chat_body
            payload = {"model": body.model or cfg["model"], "max_tokens": 512,
                       "messages": [{"role": "user", "content": "Reply with just: pong"}]}
            if cfg.get("thinking") is False:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            prepare_chat_body(base, payload)
            r = await c.post(f"{base}/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            reply = r.json()["choices"][0]["message"].get("content") or ""
            if not reply.strip() and "chat_template_kwargs" not in payload and urlsplit(base).hostname not in {"api.openai.com", "api.anthropic.com"}:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
                r = await c.post(f"{base}/chat/completions", headers=headers, json=payload)
                r.raise_for_status()
                reply = r.json()["choices"][0]["message"].get("content") or ""
        return {"ok": True, "models": models, "reply": reply}
    except Exception as e:
        return {"ok": False, "error": str(e)}
