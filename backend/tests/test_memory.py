import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select

from bt.agent import data, memory, prompts
from bt.db import engine, get_kv, get_settings, init_db, save_setting, session, update_agent_control
from bt.llm_metrics import overview, record
from bt.main import app
from bt.models import Category, Lesson, LlmRequest, MemoryCore, Profile, Video


@pytest.fixture(autouse=True)
def fresh():
    SQLModel.metadata.drop_all(engine)
    init_db()
    save_setting("memory", {"enabled": False})
    yield


def seed():
    with session() as s:
        pid = s.exec(select(Profile)).first().id
        cat = Category(profile_id=pid, name="Science")
        s.add(cat); s.commit()
        for text in ["Avoid cruelty and distress in animal videos; never select clips where animals are distressed for laughs.",
                     "Avoid unsafe stunts that kids might copy; reject unsafe stunts even if presented as entertainment."]:
            s.add(Lesson(profile_id=pid, text=text))
        s.add(Lesson(profile_id=pid, category_id=cat.id, channel_id="UCscience", text="Prefer explanations on this channel."))
        s.commit()
        return pid, cat


def runtime(audit=True, edit=None, omit=False):
    calls = []
    def chat(messages, **kwargs):
        calls.append(kwargs["schema_name"])
        if kwargs["schema_name"] in ("memory_audit", "memory_continuity_audit"):
            return {"ok": audit, "issues": [] if audit else ["A safety condition was lost"]}
        rules = json.loads(messages[-1]["content"])
        ids = [i for r in rules for i in r["source_ids"]]
        if edit:
            edit()
        return {"rules": [{"text": "Avoid animal distress and unsafe imitable stunts.", "source_ids": ids[:-1] if omit else ids}]}
    rt = SimpleNamespace(llm=SimpleNamespace(chat_json=chat), check=lambda: None,
                         state=lambda *a, **k: None, event=lambda *a, **k: None,
                         settings=get_settings, llm_ok=lambda: True)
    return rt, calls


def test_core_scope_recent_corrections_and_stable_prompt_prefix():
    pid, cat = seed()
    rt, calls = runtime()
    result = memory.compact_profile(rt, pid, 12000)
    assert result["status"] == "compacted" and "memory_audit" in calls
    with session() as s:
        source = memory.active(s, pid)
        assert len(source) == 3 and all(le.active for le in source)
        core = memory.latest(s, pid)
        assert f"Playlist {cat.id}" in core.text and "lessons apply only when evaluating channel UCscience" in core.text
        assert "other channels remain eligible" in core.text
        assert "; ONLY channel" not in core.text
        assert len(core.sources) == 3
        correction = Lesson(profile_id=pid, text="Prefer longer explanations")
        s.add(correction); s.commit()
        text, corrections = memory.select_memory(s, pid, data.lessons_for(s, cat, "UCscience"))
        assert text == core.text and [le.id for le in corrections] == [correction.id]
        judge = prompts.judge_messages(cat, Video(id="memorystest", title="X"), corrections, None, 1000, core=text)
        plan = prompts.planner_messages(cat, corrections, [], [], [], [], core=text)
        assert judge[0]["content"].split("TASK INSTRUCTIONS")[0] == plan[0]["content"].split("TASK INSTRUCTIONS")[0]
        system = judge[0]["content"]
        assert system.index("CORE IDENTITY") < system.index("RECENT CORRECTIONS") < system.index("TASK INSTRUCTIONS")
        source[0].active = False
        s.add(source[0]); s.commit()
        text, corrections = memory.select_memory(s, pid, memory.active(s, pid))
        assert text == "" and len(corrections) == 3


def test_failed_audit_or_missing_sources_never_installs_core():
    pid, _ = seed()
    rt, _ = runtime(audit=False)
    with pytest.raises(ValueError, match="audit failed"):
        memory.compact_profile(rt, pid, 12000)
    rt, _ = runtime(omit=True)
    with pytest.raises(ValueError, match="omitted or duplicated"):
        memory.compact_profile(rt, pid, 12000)
    with session() as s:
        assert memory.latest(s, pid) is None
        assert len(memory.active(s, pid)) == 3


def test_changes_during_compaction_are_not_overwritten():
    pid, _ = seed()
    def edit():
        with session() as s:
            lesson = memory.active(s, pid)[0]
            lesson.text = "New explicit rule while the model was working"
            s.add(lesson); s.commit()
    rt, _ = runtime(edit=edit)
    with pytest.raises(ValueError, match="changed during"):
        memory.compact_profile(rt, pid, 12000)
    with session() as s:
        assert memory.latest(s, pid) is None
        assert memory.active(s, pid)[0].text.startswith("New explicit")


def test_manual_run_with_schedule_disabled_and_api_validation():
    seed()
    rt, _ = runtime()
    with TestClient(app) as c:
        assert c.post("/api/agent/memory/run").json()["queued"]
        assert c.get("/api/agent/memory").json()["run"]["state"] == "queued"
        assert memory.process(rt)
        status = c.get("/api/agent/memory").json()
        assert status["core_valid"] and status["run"]["state"] == "completed"
        assert not memory.process(rt)
        assert c.put("/api/settings/memory", json={"time": "25:99"}).status_code == 400
        assert c.put("/api/settings/memory", json={"timezone": "not/a/timezone"}).status_code == 400
        assert c.put("/api/settings/memory", json={"max_core_tokens": 1}).status_code == 400
        assert c.put("/api/settings/memory", json={"max_core_tokens": 100000}).status_code == 200
        assert c.put("/api/settings/memory", json={"max_core_tokens": 100001}).status_code == 400
        assert c.post("/api/agent/memory/run").json()["queued"]
        assert memory.process(rt)
        assert c.get("/api/agent/memory").json()["versions"] == 2
        with session() as s:
            assert memory.latest(s, status["run"]["profiles"][0]["profile_id"]).budget_tokens == 100000


def test_schedule_is_timezone_aware_and_once_per_night(monkeypatch):
    cfg = {"timezone": "Europe/Copenhagen", "time": "03:00"}
    for instant in [datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc), datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc)]:
        assert memory.scheduled_day(cfg, instant) == instant.date().isoformat()
    assert memory.scheduled_day(cfg, datetime(2026, 10, 28, 12, tzinfo=timezone.utc)) is None
    seed()
    save_setting("memory", {"enabled": True})
    monkeypatch.setattr(memory, "scheduled_day", lambda _cfg: "2026-10-28")
    rt, calls = runtime()
    assert memory.process(rt)
    before = len(calls)
    assert not memory.process(rt) and len(calls) == before


def test_context_metrics_use_reported_tokens_and_label_estimates():
    body = {"model": "test", "messages": [{"role": "system", "content": "CORE IDENTITY / KNOWLEDGE / RULES\ncore\n\nRECENT CORRECTIONS\nnew\n\nTASK INSTRUCTIONS\njudge"},
                                          {"role": "user", "content": "private video transcript"}]}
    response = httpx.Response(200, json={"usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 40}}})
    record(body, "verdict", response)
    record(body, "plan", httpx.Response(200, json={}))
    with session() as s:
        summary = overview(s)
        assert summary["requests"] == 2 and summary["estimated_requests"] == 1 and summary["cached_tokens"] == 40
        measured = s.exec(select(LlmRequest).where(LlmRequest.task == "verdict")).one()
        assert measured.input_tokens == 100 and measured.core_chars == 4 and measured.correction_chars == 3
        assert "private video transcript" not in json.dumps(measured.model_dump(), default=str)


def test_revision_audit_rejects_unsafe_change_and_retains_old_core():
    pid, _ = seed()
    rt, _ = runtime()
    memory.compact_profile(rt, pid, 12000)
    with session() as s:
        before = memory.latest(s, pid)
        old_id, old_text = before.id, before.text
        s.add(Lesson(profile_id=pid, text="This particular video was an exception"))
        s.commit()
    rt, calls = runtime()
    original = rt.llm.chat_json
    def reject(messages, **kwargs):
        if kwargs["schema_name"] == "memory_continuity_audit":
            calls.append(kwargs["schema_name"])
            assert "previous_core" in json.loads(messages[-1]["content"])
            return {"ok": False, "issues": ["Existing rule weakened"]}
        return original(messages, **kwargs)
    rt.llm.chat_json = reject
    with pytest.raises(ValueError, match="continuity review failed"):
        memory.compact_profile(rt, pid, 12000)
    with session() as s:
        assert memory.latest(s, pid).id == old_id
        core, corrections = memory.select_memory(s, pid, memory.active(s, pid))
        assert core == old_text and len(corrections) == 1
    assert "memory_continuity_audit" in calls


def test_context_percentiles_cache_denominator_hourly_bins_and_profile_memory(monkeypatch):
    import bt.llm_metrics as metrics
    from datetime import timedelta
    instant = datetime(2026, 10, 28, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(metrics, "now", lambda: instant)
    with session() as s:
        pid = s.exec(select(Profile)).first().id
        rule = Lesson(profile_id=pid, text="Keep explicit rules")
        s.add(rule); s.flush()
        s.add(MemoryCore(profile_id=pid, text="Keep explicit rules", sources={str(rule.id): memory.fingerprint(rule)}))
        for task, tokens, estimate, cached, hours, ok in [
            ("verdict", 100, False, 50, 1, True),
            ("verdict", 4000, True, 100, 2, True),
            ("lesson_evidence", 65000, False, 100000, 3, True),
            ("verdict", 999999, False, 0, 1, False),
            ("verdict", 999999, False, 0, 25, True)]:
            s.add(LlmRequest(task=task, model="model", input_tokens=tokens, estimated=estimate,
                             cached_tokens=cached, ok=ok, created_at=instant - timedelta(hours=hours)))
        s.commit()
        stats = metrics.overview(s)
        assert stats["requests"] == 3 and stats["failed"] == 1
        assert stats["median_input_tokens"] == 4000 and stats["p95_input_tokens"] == 65000
        assert stats["total_input_tokens"] == 69100 and stats["max_input_tokens"] == 65000
        # Estimated input is not a valid denominator for a server-reported cache hit rate.
        assert stats["cache_hit_percent"] == round(65050 / 65100 * 100, 1)
        assert sum(h["requests"] for h in stats["hourly"]) == 3
        assert sum(b["requests"] for b in stats["distribution"]) == 3
        assert {b["label"]: b["requests"] for b in stats["distribution"]}["4k–8k"] == 1
        assert stats["memory_profiles"][0]["core_valid"]
        assert stats["rule_strengths"]["explicit"] == 1
        assert stats["revisions_24h"] == 0


def test_core_metric_stops_before_priority_and_task_even_without_corrections():
    body = {"model": "test", "messages": [{"role": "system", "content":
        "CORE IDENTITY / KNOWLEDGE / RULES\ncore\n\nMEMORY PRIORITY\npriority\n\nTASK INSTRUCTIONS\njudge"}]}
    record(body, "verdict", httpx.Response(200, json={"usage": {"prompt_tokens": 123}}))
    with session() as s:
        request = s.exec(select(LlmRequest)).one()
        assert request.core_chars == 4 and request.correction_chars == 0


def test_legacy_scope_header_repaired_without_changing_explicit_rules():
    legacy = "Playlist 9: Engineering; ONLY channel UCscience\n- Only accept this channel for this lesson.\n\nAll playlists; ONLY channel UCother\n- Prefer long videos."
    fixed = memory.repair_scope_headers(legacy)
    assert "; ONLY channel" not in fixed
    assert "other channels remain eligible" in fixed
    assert "- Only accept this channel for this lesson." in fixed
    assert memory.repair_scope_headers(fixed) == fixed
    messages = memory.apply([{"role": "system", "content": "Judge"}], legacy)
    assert "; ONLY channel" not in messages[0]["content"]


def test_legacy_core_is_repaired_on_read_even_when_sources_unchanged():
    pid, cat = seed()
    with session() as s:
        lessons = memory.active(s, pid)
        s.add(MemoryCore(profile_id=pid, text=f"Playlist {cat.id}: Science; ONLY channel UCscience\n- Prefer explanations.",
                         sources=memory.source_snapshot(s, lessons)))
        s.commit()
        core, corrections = memory.select_memory(s, pid, data.lessons_for(s, cat, "UCdifferent"))
        assert "other channels remain eligible" in core
        assert "; ONLY channel" not in core
        assert not corrections
