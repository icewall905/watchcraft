"""OpenAI-compatible chat client (sync, one request at a time).

Settings are read from `get_settings()["llm"]` on every call so UI changes apply live.
Optional extra llm settings honoured if present: `thinking` (bool, default True; False sends
`chat_template_kwargs.enable_thinking=false`), `max_retries` (default 2).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Callable

import httpx

log = logging.getLogger("bt.llm")

_lock = threading.Lock()  # concurrency 1 (shared GPU)
_schema_unsupported: set[str] = set()  # base_urls that rejected response_format=json_schema


class LlmError(Exception):
    pass


class LlmParseError(LlmError):
    pass


_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    # unterminated <think> (truncated) or a stray closing tag
    if re.search(r"</think(?:ing)?>", text, re.I):
        text = re.split(r"</think(?:ing)?>", text, flags=re.I)[-1]
    text = re.sub(r"<think(?:ing)?>.*\Z", "", text, flags=re.S | re.I)
    return text.strip()


def _balanced(text: str, start: int) -> str | None:
    """Return the balanced {...} or [...] substring starting at `start` (string-aware)."""
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _loads_lenient(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # trailing commas, smart quotes
        s2 = re.sub(r",\s*([}\]])", r"\1", s).replace("“", '"').replace("”", '"')
        return json.loads(s2)


def parse_json(text: str) -> dict:
    """Robustly extract the first JSON object from model output."""
    t = strip_think(text)
    candidates = [m.group(1).strip() for m in _FENCE_RE.finditer(t)] + [t]
    for c in candidates:
        try:
            v = _loads_lenient(c)
            if isinstance(v, dict):
                return v
        except (json.JSONDecodeError, ValueError):
            pass
        for m in re.finditer(r"\{", c):
            blob = _balanced(c, m.start())
            if not blob:
                continue
            try:
                v = _loads_lenient(blob)
                if isinstance(v, dict):
                    return v
            except (json.JSONDecodeError, ValueError):
                continue
    raise LlmParseError(f"no JSON object in model output: {t[:200]!r}")


ROLES = ("judge", "worker")


def _cfg(role: str = "judge") -> dict:
    """Settings for one role.

    The judge does the thinking: verdicts, planning, review, reports. The worker does the grunt
    work: screening, summaries, imports. A worker that is not configured falls back to the judge's
    model, endpoint and key, so a single-model setup behaves exactly as before.
    """
    from .db import get_settings
    cfg = dict(get_settings()["llm"])
    if role == "worker":
        cfg["model"] = (cfg.get("worker_model") or "").strip() or cfg["model"]
        cfg["base_url"] = (cfg.get("worker_base_url") or "").strip() or cfg["base_url"]
        cfg["api_key"] = (cfg.get("worker_api_key") or "").strip() or cfg.get("api_key", "")
    return cfg


def _with_images(messages: list[dict], images: list[str] | None) -> list[dict]:
    """Attach image URLs (data: or http) to the last user message (OpenAI vision format)."""
    if not images:
        return messages
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m.get("role") == "user":
            content = m["content"]
            parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
            m["content"] = list(parts) + [{"type": "image_url", "image_url": {"url": u}} for u in images]
            break
    return msgs


_SCHEMA_ERR_RE = re.compile(r"response_format|json_schema|json schema|grammar|schema", re.I)


class LlmTruncated(LlmError):
    """Completion hit max_tokens before producing usable content (e.g. a thinking model ran out)."""


def _acquire(abort: Callable[[], None] | None) -> None:
    """Take the GPU-concurrency lock; abortable while waiting."""
    while not _lock.acquire(timeout=0.5):
        if abort:
            abort()


def _post(cfg: dict, body: dict, abort: Callable[[], None] | None = None) -> httpx.Response:
    """POST /chat/completions while holding the concurrency lock.

    With `abort`, the request runs as an asyncio task on a helper thread's event loop and `abort()` is polled
    every 0.25 s. If it raises, the task is cancelled (httpx closes the socket, so the server sees the disconnect
    and stops generating) and the exception propagates at once. The lock is owned by the helper and released only
    when the helper has really finished, so an abandoned request never overlaps the next one."""
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    timeout = float(cfg.get("timeout_s") or 300)
    _acquire(abort)
    if abort is None:
        try:
            with httpx.Client(timeout=timeout) as c:
                return c.post(url, json=body, headers=headers)
        finally:
            _lock.release()

    box: dict = {}
    ready = threading.Event()

    async def request():
        async with httpx.AsyncClient(timeout=timeout) as c:
            return await c.post(url, json=body, headers=headers)

    def run():
        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(request())
            box["loop"], box["task"] = loop, task
            ready.set()
            box["r"] = loop.run_until_complete(task)
        except BaseException as exc:  # noqa: BLE001 (incl. CancelledError)
            box["e"] = exc
        finally:
            ready.set()
            try:
                loop.close()
            finally:
                _lock.release()

    th = threading.Thread(target=run, daemon=True, name="llm-request")
    try:
        th.start()
    except BaseException:
        _lock.release()
        raise
    ready.wait()
    try:
        while th.is_alive():
            abort()
            th.join(0.25)
    except BaseException:
        loop, task = box.get("loop"), box.get("task")
        if loop is not None and task is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # loop already finished
        raise
    if "e" in box:
        raise box["e"]
    return box["r"]


def _sleep(seconds: float, abort: Callable[[], None] | None) -> None:
    end = time.monotonic() + seconds
    while True:
        if abort:
            abort()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.5, left))


def prepare_chat_body(base: str, body: dict) -> dict:
    """Adapt optional OpenAI-compatible fields for the direct provider APIs."""
    from urllib.parse import urlsplit
    host = urlsplit(base).hostname
    if host in {"api.openai.com", "api.anthropic.com"}:
        body.pop("chat_template_kwargs", None)
    if host == "api.openai.com":
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        if re.match(r"^(?:gpt-[56]|o[134])(?:\b|[-.])", body.get("model", "")):
            body.pop("temperature", None)
    elif host == "api.anthropic.com":
        body.pop("temperature", None)
    return body


def _request(messages, json_schema, max_tokens, images, temperature, schema_name, abort=None,
             no_think: bool = False, timeout_s: float | None = None,
             reasoning_effort: str | None = None, role: str = "judge") -> str:
    if abort:
        abort()
    cfg = _cfg(role)
    if timeout_s is not None:
        cfg["timeout_s"] = timeout_s
    body: dict = {"model": cfg["model"], "messages": _with_images(messages, images), "max_tokens": max_tokens,
                  "temperature": cfg.get("temperature", 0.4) if temperature is None else temperature}
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    if no_think or (reasoning_effort is None and cfg.get("thinking") is False):
        body["chat_template_kwargs"] = {"enable_thinking": False}
    elif reasoning_effort is not None:
        body["chat_template_kwargs"] = {"enable_thinking": True}
    base = cfg["base_url"]
    use_schema = json_schema is not None and base not in _schema_unsupported
    if use_schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": schema_name, "strict": True, "schema": json_schema}}
    prepare_chat_body(base, body)
    def send():
        from .llm_metrics import record
        response = None
        try:
            response = _post(cfg, body, abort)
            return response
        finally:
            record(body, schema_name, response)
    r = send()
    if use_schema and r.status_code in (400, 415, 422, 501) and _SCHEMA_ERR_RE.search(r.text or ""):
        log.info("LLM rejected response_format (%s); falling back to plain prompt", r.status_code)
        _schema_unsupported.add(base)
        body.pop("response_format", None)
        if abort:
            abort()
        r = send()
    if r.status_code >= 400 and json_schema is not None and re.search(
            r"finish_reason\s*[=:]\s*length|increase the output budget", r.text or "", re.I):
        raise LlmTruncated("server reported truncated structured output")
    if r.status_code >= 400:
        raise LlmError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LlmError(f"bad response: {str(data)[:300]}") from e
    if not strip_think(content):
        if choice.get("finish_reason") == "length":
            raise LlmTruncated("ran out of max_tokens before answering (thinking too long)")
        raise LlmError(f"empty completion (finish_reason={choice.get('finish_reason')})")
    if choice.get("finish_reason") == "length" and json_schema is not None:
        try:
            parse_json(content)
        except LlmParseError as e:
            raise LlmTruncated("JSON answer truncated at max_tokens") from e
    return content


def _attempts(retries: int | None) -> int:
    return 1 + (retries if retries is not None else int(_cfg().get("max_retries", 2)))


def _loop(fn, attempts: int, max_tokens: int, abort):
    """Shared retry loop. Truncation increases the budget and requests a thinking fallback."""
    last: Exception | None = None
    no_think = False
    for i in range(attempts):
        try:
            return fn(max_tokens, no_think)
        except LlmTruncated as e:
            last = e
            log.warning("LLM truncated (attempt %d/%d): %s — increasing output budget", i + 1, attempts, e)
            no_think, max_tokens = True, int(max_tokens * 1.5)
        except (httpx.HTTPError, LlmError, ValueError) as e:
            last = e
            log.warning("LLM call failed (attempt %d/%d): %s", i + 1, attempts, e)
            if i + 1 < attempts:
                _sleep(min(2 ** i, 10), abort)
    if isinstance(last, LlmTruncated):
        raise last
    raise LlmError(str(last))


def chat(messages: list[dict], json_schema: dict | None = None, max_tokens: int = 1024,
         images: list[str] | None = None, temperature: float | None = None, schema_name: str = "result",
         retries: int | None = None, abort: Callable[[], None] | None = None, role: str = "judge") -> str:
    """Return the assistant text (think blocks stripped). Retries on transport/HTTP errors.

    `abort()` is called before every attempt, during retry sleeps and while waiting for the response;
    any exception it raises propagates immediately (used for pause/stop)."""
    return _loop(lambda mt, nt: strip_think(_request(messages, json_schema, mt, images, temperature, schema_name,
                                                     abort, nt, role=role)),
                 _attempts(retries), max_tokens, abort)


def chat_json(messages: list[dict], json_schema: dict | None = None, max_tokens: int = 1024,
              images: list[str] | None = None, temperature: float | None = None, schema_name: str = "result",
              retries: int | None = None, abort: Callable[[], None] | None = None,
              timeout_s: float | None = None, no_think: bool = False,
              reasoning_effort: str | None = None, role: str = "judge") -> dict:
    """Like chat() but returns a parsed dict; parse failures count as a failed attempt."""
    msgs = messages
    if json_schema is not None:
        # Always restate the schema in the prompt too: works with and without response_format support.
        hint = ("Respond with ONLY a JSON object (no prose, no code fences) matching this JSON schema:\n"
                + json.dumps(json_schema, separators=(",", ":")))
        msgs = [dict(m) for m in messages]
        if msgs and msgs[0].get("role") == "system":
            msgs[0]["content"] = f"{msgs[0]['content']}\n\n{hint}"
        else:
            msgs.insert(0, {"role": "system", "content": hint})
    return _loop(lambda mt, nt: parse_json(_request(msgs, json_schema, mt, images, temperature, schema_name,
                                                    abort, no_think or (nt and reasoning_effort is None), timeout_s,
                                                    reasoning_effort, role=role)),
                 _attempts(retries), max_tokens, abort)
