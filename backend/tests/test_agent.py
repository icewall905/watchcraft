"""Agent worker tests (no network except the `live` test, which needs BT_LIVE=1)."""
import os
import tempfile

os.environ.setdefault("BT_DATA_DIR", tempfile.mkdtemp(prefix="bt-test-"))

from datetime import timedelta  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlmodel import SQLModel, select  # noqa: E402

from bt import llm  # noqa: E402
from bt.agent import data, judge, learn, prompts  # noqa: E402
from bt.agent.loop import Worker  # noqa: E402
from bt.agent.ratelimit import quiet_remaining  # noqa: E402
from bt.agent.runtime import Paused, Runtime, parse_ts  # noqa: E402
from bt.db import (engine, get_agent_state, init_db, log_yt_request, save_setting, session,  # noqa: E402
                   update_agent_control)
from bt.models import (AgentEvent, Candidate, Category, Feedback, Lesson, Profile, ProfileChannel, Video,  # noqa: E402
                       WatchHistory, YtRequest, now)


# ------------------------------------------------------------------ fakes

class YtError(Exception):
    pass


class YtBlocked(YtError):
    pass


class YtUnavailable(YtError):
    pass


def entry(vid, title, dur=600, ch="UCgood", **kw):
    return {"id": vid, "title": title, "channel_id": ch, "channel_name": ch + " name", "duration_s": dur,
            "view_count": 1000, "thumbnail": "", "published_at": None, "is_short": False, "is_live": False, **kw}


class FakeYt:
    YtError, YtBlocked, YtUnavailable = YtError, YtBlocked, YtUnavailable

    def __init__(self, results=None):
        self.calls = []
        self.results = results if results is not None else [
            entry("vidGOOD0001", "Building a giant marble run"),
            entry("vidCRUEL001", "Scaring my cat for views"),
            entry("vidSHORT001", "Quick clip", dur=30, is_short=True),
            entry("vidEXCL0001", "Minecraft lets play episode 4"),
        ]
        self.block_on = set()

    def _log(self, kind):
        self.calls.append(kind)
        if kind in self.block_on:
            log_yt_request(kind, "worker", False, "blocked")
            raise YtBlocked("Sign in to confirm you're not a bot")
        log_yt_request(kind, "worker", True, "fake")

    def search(self, query, n=15, origin="worker"):
        self._log("search")
        return [dict(e) for e in self.results]

    def channel_videos(self, ref, n=30, origin="worker"):
        self._log("channel")
        return {"id": "UCchan", "name": "Chan"}, [dict(e) for e in self.results]

    def video_info(self, video_id, origin="worker"):
        self._log("metadata")
        e = next((dict(x) for x in self.results if x["id"] == video_id), entry(video_id, "Some video"))
        e.update(description=f"Description of {e['title']}", tags=["fun"], chapters=[], formats=[])
        return e

    def upsert_video(self, s, d):
        v = s.get(Video, d["id"]) or Video(id=d["id"])
        for k in ("title", "channel_id", "channel_name", "duration_s", "view_count", "is_short", "is_live"):
            if d.get(k) is not None:
                setattr(v, k, d[k])
        if "description" in d:
            v.description = d["description"]
        s.add(v)
        return v

    def transcript(self, info, max_chars, langs=None):
        self._log("transcript")
        return f"Transcript: {info['title']} " * 3

    def comments(self, video_id, max_comments=40, origin="worker"):
        self._log("comments")
        return [{"author": "a", "text": "my kids loved this", "likes": 12}]


class FakeFeeds:
    def __init__(self, yt):
        self.yt = yt

    def channel_feed(self, channel_id, origin="worker"):
        self.yt._log("rss")
        return []


class FakeLlm:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.fail_with_images = False

    def chat_json(self, messages, json_schema=None, max_tokens=1024, images=None, schema_name="result", **kw):
        self.calls.append({"schema": schema_name, "images": images, "messages": messages})
        if self.fail or (images and self.fail_with_images):
            raise llm.LlmError("boom")
        if schema_name == "plan":
            return {"queries": ["marble run build", "kids science experiment"], "channels": []}
        if schema_name == "verdict":
            user = messages[-1]["content"]
            if "cat for views" in user:
                return {"score": 18, "summary": "Frightens a cat.", "reasons": ["mean"],
                        "red_flags": ["animal distress played for laughs"], "kid_safe": False, "tags": ["cat"]}
            return {"score": 86, "summary": "A fun hands-on build.", "reasons": ["effort", "kids loved it"],
                    "red_flags": [], "kid_safe": True, "tags": ["building"]}
        if schema_name == "lessons":
            return {"lessons": [{"text": "Avoid videos where animals are frightened for laughs",
                                 "category_id": None, "channel_id": "UCbad", "replaces_lesson_id": None}]}
        raise AssertionError(schema_name)


class FakeTime:
    """Fake monotonic clock; sleep advances it and runs an optional hook."""

    def __init__(self):
        self.t = 0.0
        self.slept = 0.0
        self.hook = None

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.slept += s
        if self.hook:
            self.hook()


# ------------------------------------------------------------------ fixtures

@pytest.fixture(autouse=True)
def fresh_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    # Pipeline tests exercise an explicitly enabled curator after onboarding.
    save_setting("agent", {"enabled": True})
    save_setting("throttle", {"min_delay_s": 0, "max_delay_s": 0, "max_per_hour": 1000, "max_per_day": 10000,
                              "backoff_min_s": 3600, "backoff_max_s": 21600, "pause_while_playing": True,
                              "quiet_hours": "", "search_results": 15, "max_comments": 40,
                              "transcript_max_chars": 5000})
    save_setting("llm", {"vision": True})
    save_setting("memory", {"enabled": False})
    yield
    SQLModel.metadata.drop_all(engine)
    init_db()


def make_worker(yt=None, fake_llm=None):
    yt = yt or FakeYt()
    ft = FakeTime()
    rt = Runtime(yt=yt, feeds=FakeFeeds(yt), llm=fake_llm or FakeLlm(), thumb=lambda vid: "data:image/jpeg;base64,AAA",
                 sleep=ft.sleep, clock=ft.clock)
    w = Worker(rt)
    w.ft = ft
    return w


def make_category(**kw):
    with session() as s:
        pid = s.exec(select(Profile)).first().id
        c = Category(profile_id=pid, name=kw.pop("name", "Kids science"), brief="Fun hands-on science for kids",
                     seed_queries=["science for kids"], exclude_keywords=["minecraft"], target_queue=5,
                     threshold=65, kid_safe=True, **kw)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c


def cands(cat_id):
    with session() as s:
        return {c.video_id: c for c in s.exec(select(Candidate).where(Candidate.category_id == cat_id)).all()}


def events(kind=None):
    with session() as s:
        q = select(AgentEvent)
        if kind:
            q = q.where(AgentEvent.kind == kind)
        return s.exec(q.order_by(AgentEvent.id)).all()


# ------------------------------------------------------------------ pipeline

def test_pipeline_found_enriched_judged():
    cat = make_category()
    w = make_worker()
    for _ in range(12):
        w.iterate()
    cs = cands(cat.id)
    assert set(cs) == {"vidGOOD0001", "vidCRUEL001"}  # short + excluded keyword prefiltered
    assert cs["vidGOOD0001"].status == "accepted" and cs["vidGOOD0001"].score == 86
    assert cs["vidCRUEL001"].status == "rejected"
    assert "animal distress" in cs["vidCRUEL001"].red_flags[0]
    with session() as s:
        v = s.get(Video, "vidGOOD0001")
        assert v.enriched_at and "Transcript" in v.transcript and v.comments
        assert s.get(Category, cat.id).last_worked_at is not None
        assert s.exec(select(YtRequest)).all()
    yt = w.rt.yt
    assert yt.calls.count("metadata") == 2 and yt.calls.count("transcript") == 2 and yt.calls.count("comments") == 2
    kinds = {e.kind for e in events()}
    assert {"plan", "search", "transcript", "comments", "judge", "accept", "reject"} <= kinds
    acc = events("accept")[0]
    assert acc.message.startswith("✅ Accepted (86)") and acc.payload["score"] == 86 and acc.video_id == "vidGOOD0001"
    assert {e.payload["candidate_id"] for e in events("judge")} == {c.id for c in cs.values()}
    # every candidate-related event links to its candidate (UI opens /api/agent/candidates/{id})
    vid_events = [e for e in events() if e.video_id and e.category_id]
    assert vid_events and all(e.payload.get("candidate_id") == cs[e.video_id].id for e in vid_events)
    assert {e.kind for e in vid_events} >= {"metadata", "transcript", "comments", "judge", "accept", "reject"}
    found_ev = [e for e in events("search") if e.payload.get("candidate_ids")]
    assert set(found_ev[0].payload["candidate_ids"]) == {c.id for c in cs.values()}
    srch = [e for e in events("search") if "query" in e.payload]
    assert srch[0].payload["query"] == "marble run build"
    # judge got the thumbnail as image
    vcalls = [c for c in w.rt.llm.calls if c["schema"] == "verdict"]
    assert vcalls and vcalls[0]["images"] == ["data:image/jpeg;base64,AAA"]
    assert "my kids loved this" in vcalls[0]["messages"][-1]["content"]
    # planner cached in category-scoped setting
    from bt.db import get_kv
    assert get_kv(f"plan:{cat.id}")["queries"] == ["marble run build", "kids science experiment"]


def test_reuses_enrichment_across_categories():
    a = make_category(name="A")
    w = make_worker()
    for _ in range(8):
        w.iterate()
    n_meta = w.rt.yt.calls.count("metadata")
    b = make_category(name="B")
    with session() as s:
        a_row = s.get(Category, a.id)
        a_row.enabled = False
        s.add(a_row)
        s.commit()
    for _ in range(8):
        w.iterate()
    assert cands(b.id)["vidGOOD0001"].status == "accepted"
    assert w.rt.yt.calls.count("metadata") == n_meta  # no re-fetch
    assert any("Already researched" in e.message for e in events("metadata"))


def test_prefilter_rules():
    cat = make_category(max_age_days=30)
    with session() as s:
        s.add(ProfileChannel(profile_id=cat.profile_id, channel_id="UCbad", trust=-0.9))
        s.add(ProfileChannel(profile_id=cat.profile_id, channel_id="UCblk", blocked=True))
        s.commit()
        ctx = data.profile_ctx(s, cat.profile_id)
    pf = lambda e, **k: data.prefilter(e, cat, ctx, True, **k)  # noqa: E731
    assert pf(entry("v1", "ok")) is None
    assert pf(entry("v1", "ok", ch="UCbad")) == "blocked/distrusted channel"
    assert pf(entry("v1", "ok", ch="UCblk")) == "blocked/distrusted channel"
    assert pf(entry("v1", "ok", dur=10)).startswith("too short")
    assert pf(entry("v1", "ok", dur=99999)).startswith("too long")
    assert pf(entry("v1", "ok", is_live=True)) == "live stream"
    assert pf(entry("v1", "ok", is_short=True)) == "short"
    assert pf(entry("v1", "MINECRAFT fun")).startswith("excluded keyword")
    assert pf(entry("v1", "ok", published_at=(now() - timedelta(days=90)).isoformat())).startswith("older")
    assert pf(entry("v1", "ok"), existing={"v1"}) == "already a candidate"


# ------------------------------------------------------------------ control

def test_pause_blocks_all_calls():
    make_category()
    w = make_worker()
    update_agent_control(paused=True)
    with pytest.raises(Paused):
        w.iterate()
    assert w.rt.yt.calls == [] and w.rt.llm.calls == []


def test_pause_during_throttle_wait_makes_no_call():
    make_category()
    save_setting("throttle", {"min_delay_s": 30, "max_delay_s": 30})
    log_yt_request("search", "worker", True, "previous")
    w = make_worker()
    w.ft.hook = lambda: update_agent_control(paused=True)
    with pytest.raises(Paused):
        w.iterate()
    assert w.rt.yt.calls == []
    assert w.ft.slept <= 2  # control polled at least every 2 s


def test_run_loop_paused_then_stop_sets_offline():
    w = make_worker()
    update_agent_control(paused=True)
    n = {"i": 0}

    def hook():
        n["i"] += 1
        if n["i"] > 3:
            w.rt.stop_requested = True
    w.ft.hook = hook
    w.rt._sleep = lambda s: (w.ft.sleep(s))
    w.run()
    assert get_agent_state()["state"] == "offline"
    assert any("Paused" in e.message for e in events("control"))
    assert w.rt.yt.calls == [] and w.rt.llm.calls == []


def test_playing_holds_youtube_but_allows_llm():
    make_category()
    update_agent_control(playing_until=(now() + timedelta(seconds=600)).isoformat())
    w = make_worker()
    assert w.iterate() is True
    assert w.rt.yt.calls == []
    assert get_agent_state()["state"] == "waiting"


def test_run_now_wakes_idle_sleep():
    w = make_worker()  # no categories → idle
    assert w.iterate() is False
    w.ft.hook = lambda: update_agent_control(run_now={"category_id": None, "at": now().isoformat()})
    w._idle()
    assert w.ft.slept <= 2


def test_backoff_on_blocked_persists_and_resets():
    make_category()
    yt = FakeYt()
    yt.block_on = {"search"}
    w = make_worker(yt)
    w.iterate()
    st = get_agent_state()
    assert st["state"] == "backoff" and st["backoff_n"] == 1
    until = parse_ts(st["until"])
    assert 3500 < (until - now()).total_seconds() <= 3600
    assert any("backing off 1h" in e.message for e in events("backoff"))
    # restart keeps backing off; no YT calls while backoff active
    yt.calls.clear()
    w2 = make_worker(yt)
    assert w2.rt.backoff_n == 1 and w2.rt.backoff_until is not None
    w2.iterate()
    assert yt.calls == []
    assert get_agent_state()["state"] == "backoff"
    # next block doubles
    w2.rt.backoff_until = None
    w2.iterate()
    assert w2.rt.backoff_n == 2
    assert 7100 < (w2.rt.backoff_until - now()).total_seconds() <= 7200
    # manual reset via control
    update_agent_control(backoff_reset=True)
    yt.block_on = set()
    w2.iterate()
    assert w2.rt.backoff_n == 0 and w2.rt.backoff_until is None
    assert "search" in yt.calls


def test_hourly_cap_blocks():
    make_category()
    save_setting("throttle", {"max_per_hour": 2})
    log_yt_request("search")
    log_yt_request("search")
    w = make_worker()
    w.iterate()
    assert w.rt.yt.calls == []
    assert "budget" in get_agent_state()["detail"]


def test_quiet_hours():
    from datetime import datetime
    assert quiet_remaining("01:00-07:00", datetime(2026, 1, 1, 3, 0)) == 4 * 3600
    assert quiet_remaining("01:00-07:00", datetime(2026, 1, 1, 8, 0)) == 0
    assert quiet_remaining("23:00-07:00", datetime(2026, 1, 1, 23, 30)) == 7.5 * 3600
    assert quiet_remaining("23:00-07:00", datetime(2026, 1, 1, 6, 0)) == 3600
    assert quiet_remaining("", datetime(2026, 1, 1, 6, 0)) == 0


# ------------------------------------------------------------------ llm failures

def test_llm_failure_leaves_enriched_and_backs_off():
    cat = make_category()
    fl = FakeLlm()
    w = make_worker(fake_llm=fl)
    for _ in range(3):
        w.iterate()
    fl.fail = True
    for _ in range(3):
        w.iterate()
    assert w.rt.llm_backoff_until is not None
    assert any(c.status == "enriched" for c in cands(cat.id).values())
    assert any("LLM unavailable" in e.message and e.level == "warn" for e in events("error"))


def test_vision_failure_retries_without_image():
    cat = make_category()
    fl = FakeLlm()
    fl.fail_with_images = True
    w = make_worker(fake_llm=fl)
    for _ in range(10):
        w.iterate()
    assert cands(cat.id)["vidGOOD0001"].status == "accepted"
    v = [c for c in fl.calls if c["schema"] == "verdict"]
    assert v[0]["images"] and v[1]["images"] is None


def test_kid_safe_category_rejects_unsafe():
    cat = make_category()
    assert judge.decide(cat, judge.normalize({"score": 95, "kid_safe": False, "summary": "x"}))[0] is False
    assert judge.decide(cat, judge.normalize({"score": "88", "kid_safe": "true", "summary": "x"}))[0] is True
    assert judge.normalize({"score": 150})["score"] == 100


# ------------------------------------------------------------------ learn

def test_feedback_creates_lessons_and_updates_trust():
    cat = make_category()
    with session() as s:
        s.add(Video(id="vidBAD00001", title="Cat scare", channel_id="UCbad", channel_name="Bad"))
        s.add(Video(id="vidLIKE0001", title="Rocket", channel_id="UCgood", channel_name="Good"))
        s.add(Feedback(profile_id=cat.profile_id, video_id="vidBAD00001", category_id=cat.id, rating=-1,
                       chips=["mean"], text="mean to the cat"))
        s.add(Feedback(profile_id=cat.profile_id, video_id="vidLIKE0001", category_id=cat.id, rating=2))
        s.commit()
    w = make_worker()
    w.iterate()
    with session() as s:
        lessons = s.exec(select(Lesson)).all()
        assert len(lessons) == 1 and "animals" in lessons[0].text and lessons[0].channel_id == "UCbad"
        assert all(f.processed for f in s.exec(select(Feedback)).all())
        tr = {p.channel_id: p.trust for p in s.exec(select(ProfileChannel)).all()}
        assert tr == {"UCbad": -0.3, "UCgood": 0.2}
    assert any(e.message.startswith("🧠 Learned") for e in events("learn"))
    assert w.rt.yt.calls.count("search") <= 1  # learning ran first (no YT needed for it)
    first_llm = w.rt.llm.calls[0]["schema"]
    assert first_llm == "lessons"
    # same lesson again → deduped (reinforced), not duplicated
    with session() as s:
        s.add(Feedback(profile_id=cat.profile_id, video_id="vidBAD00001", category_id=cat.id, rating=-1, text="again"))
        s.commit()
    learn.process(w.rt)
    with session() as s:
        ls = s.exec(select(Lesson)).all()
        assert len(ls) == 1 and ls[0].weight > 1
        assert s.exec(select(ProfileChannel).where(ProfileChannel.channel_id == "UCbad")).first().trust == -0.6


def test_lessons_reach_judge_prompt():
    cat = make_category()
    with session() as s:
        s.add(Lesson(profile_id=cat.profile_id, text="Never pick prank videos"))
        s.commit()
        v = Video(id="x" * 11, title="T", channel_id="UC", transcript="hello", comments=[{"text": "nice", "likes": 3}])
        msgs = prompts.judge_messages(cat, v, data.lessons_for(s, cat, "UC"), 0.4, 1000)
    u = msgs[-1]["content"]
    assert "Never pick prank videos" in msgs[0]["content"]
    assert "trust +0.4" in u and "hello" in u and "Kid-safe category: YES" in u


def test_prompt_stable_context_precedes_dynamic_queue_and_candidate():
    cat = make_category()
    a = Video(id="a" * 11, title="Candidate A", channel_id="UC", transcript="a")
    b = Video(id="b" * 11, title="Candidate B", channel_id="UC", transcript="b")
    ja = prompts.judge_messages(cat, a, [], None, 1000, queue=["Queue A"])[-1]["content"]
    jb = prompts.judge_messages(cat, b, [], None, 1000, queue=["Queue B"])[-1]["content"]
    assert ja.split("CURRENT QUEUE", 1)[0] == jb.split("CURRENT QUEUE", 1)[0]
    pa = prompts.planner_messages(cat, [], [], [], [], [], queue=["Queue A"])[-1]["content"]
    pb = prompts.planner_messages(cat, [], [], [], [], [], queue=["Queue B"])[-1]["content"]
    assert pa.split("CURRENT QUEUE", 1)[0] == pb.split("CURRENT QUEUE", 1)[0]
    assert pa.index("Seed queries from the family") < pa.index("CURRENT QUEUE")


def test_approved_rejection_keeps_verdict_context_and_does_not_boost_trust():
    cat = make_category()
    with session() as s:
        s.add(Video(id="reject00001", title="Rejected", channel_id="UCbad", channel_name="Bad"))
        s.add(Candidate(category_id=cat.id, video_id="reject00001", status="rejected", score=22,
                        summary="A shallow prank", reasons=["staged"], red_flags=["cruel prank"], tags=["prank"]))
        fb = Feedback(profile_id=cat.profile_id, video_id="reject00001", category_id=cat.id, rating=1,
                      chips=["Approved rejection"], text="")
        s.add(fb)
        s.commit()
        items, channels = learn._items(s, [fb])
        assert channels == {"UCbad"}
        assert items[0]["ai_score_when_picked"] == 22
        assert items[0]["ai_reasons_when_picked"] == ["staged"]
        assert items[0]["ai_red_flags_when_picked"] == ["cruel prank"]
        learner_msgs = prompts.learn_messages(cat.name, [cat], [], items)
        assert "Approved rejection" in learner_msgs[0]["content"]
        assert "wants the" in learner_msgs[0]["content"]
        assert "endorses the AI rejection" not in "\n".join(m["content"] for m in learner_msgs)
        assert learn._adjust_trust(s, cat.profile_id, [fb]) == []


def test_approval_correction_is_processed_and_lesson_reaches_future_judgments(monkeypatch):
    cat = make_category()
    w = make_worker()
    with session() as s:
        s.add(Video(id="advanced001", title="Advanced marble builds", channel_id="UCbuild"))
        s.add(Candidate(category_id=cat.id, video_id="advanced001", status="accepted", score=45,
                        summary="Too advanced", red_flags=["technical explanation"]))
        fb = Feedback(profile_id=cat.profile_id, category_id=cat.id, video_id="advanced001", rating=1,
                      chips=["Approved rejection"], text="Family added this despite the AI rejection: Too advanced")
        s.add(fb)
        s.commit()
        fid = fb.id

    def corrected_lesson(messages, **kw):
        assert kw["schema_name"] == "lessons"
        assert "Approved rejection" in messages[-1]["content"] and "Too advanced" in messages[-1]["content"]
        return {"lessons": [{"text": "Welcome advanced marble builds with clear explanations",
                             "category_id": cat.id, "channel_id": None, "replaces_lesson_id": None}]}

    monkeypatch.setattr(w.rt.llm, "chat_json", corrected_lesson)
    assert learn.process(w.rt) == 1
    with session() as s:
        assert s.get(Feedback, fid).processed
        lessons = data.lessons_for(s, cat, "UCnew")
        assert len(lessons) == 1 and lessons[0].category_id == cat.id
        msgs = prompts.judge_messages(cat, Video(id="future00001", title="Another build"), lessons, None, 1000)
        assert "Welcome advanced marble builds" in msgs[0]["content"]
        assert data.trust_for(s, cat.profile_id, "UCbuild") is None
    assert learn.process(w.rt) == 0  # processed exactly once


def test_serialized_llm_prompt_keeps_schema_prefix_before_variable_text_and_image(monkeypatch):
    cat = make_category()
    sent = []

    def capture(cfg, body, abort=None):
        sent.append(body["messages"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]},
                              request=httpx.Request("POST", "http://test"))

    monkeypatch.setattr(llm, "_post", capture)
    for suffix in ("A", "B"):
        msgs = prompts.judge_messages(cat, Video(id=suffix * 11, title=f"Title {suffix}"), [], None, 1000)
        llm.chat_json(msgs, json_schema=prompts.JUDGE_SCHEMA, images=[f"data:image/png;base64,{suffix}"])
    assert sent[0][0] == sent[1][0]
    assert sent[0][0]["role"] == "system" and "JSON schema" in sent[0][0]["content"]
    assert sent[0][1]["content"][0]["type"] == "text"
    assert sent[0][1]["content"][-1]["type"] == "image_url"
    assert sent[0][1]["content"] != sent[1][1]["content"]


def test_queue_context_reaches_judge_and_planner():
    cat = make_category()
    with session() as s:
        for vid, title in [("v" * 11, "Space Vid A"), ("w" * 11, "Watched Vid B"), ("x" * 11, "Dismissed Vid C")]:
            s.add(Video(id=vid, title=title, channel_id="UC"))
        for vid, extra in [("v" * 11, {}), ("w" * 11, {}), ("x" * 11, {"dismissed": True})]:
            s.add(Candidate(category_id=cat.id, video_id=vid, status="accepted", judged_at=now(), **extra))
        s.add(WatchHistory(profile_id=cat.profile_id, video_id="w" * 11, finished=True))
        s.commit()
        q = data.queue_titles(s, cat.id, cat.profile_id)
    assert q == ["Space Vid A"]  # dismissed and finished-watched excluded
    v = Video(id="y" * 11, title="T", channel_id="UC", transcript="hello")
    u = prompts.judge_messages(cat, v, [], None, 1000, queue=q)[-1]["content"]
    assert "Space Vid A" in u and "Watched Vid B" not in u
    p = prompts.planner_messages(cat, [], [], [], [], [], queue=q)[-1]["content"]
    assert "Space Vid A" in p and "CURRENT QUEUE" in p and "VARIETY" in prompts.PLANNER_SYSTEM


def test_rich_queue_context_is_bounded_and_isolated():
    cat = make_category()
    other = make_category(name="Other category")
    with session() as s:
        for i in range(42):
            vid = f"q{i:010d}"  # eleven-character deterministic test IDs
            s.add(Video(id=vid, title=f"Queue {i}", channel_id=f"UC{i:08d}",
                        channel_name=f"Creator {i}", duration_s=61 + i,
                        tags=["topic", "experiment", f"subtopic-{i}"]))
            s.add(Candidate(category_id=cat.id, video_id=vid, status="accepted",
                            judged_at=now() + timedelta(seconds=i), dismissed=(i == 1),
                            tags=["topic", "experiment", f"subtopic-{i}"]))
        s.add(Video(id="other000001", title="Other category video", channel_id="OTHER"))
        s.add(Candidate(category_id=other.id, video_id="other000001", status="accepted", judged_at=now()))
        s.add(WatchHistory(profile_id=cat.profile_id, video_id="q0000000000", finished=True))
        s.commit()
        queue = data.queue_context(s, cat.id, cat.profile_id)

    assert len(queue) == 40
    assert all(" — Creator " in item and "topics: topic, experiment" in item for item in queue)
    assert any("Queue 2 — Creator 2 — 1:03" in item for item in queue)
    assert not any(item.startswith("Queue 0 —") or item.startswith("Queue 1 —") or
                   item.startswith("Other category") for item in queue)


def test_rejections_with_reasons_reach_judge_and_planner():
    cat = make_category()
    with session() as s:
        s.add(Video(id="a" * 11, title="Ollama setup for beginners", channel_id="UC"))
        s.add(Video(id="b" * 11, title="Prank vid", channel_id="UC2"))
        s.add(Candidate(category_id=cat.id, video_id="a" * 11, status="rejected",
                        judged_at=now() + timedelta(seconds=10),
                        red_flags=["Family: Boring; too basic, want advanced stuff"]))
        s.add(Candidate(category_id=cat.id, video_id="b" * 11, status="rejected", judged_at=now(),
                        red_flags=["cruel prank"]))
        s.add(Feedback(profile_id=cat.profile_id, video_id="a" * 11, category_id=cat.id, rating=-1,
                       chips=["Boring"], text="too basic, want advanced stuff"))
        s.commit()
        r = data.rejections_for(s, cat.id, cat.profile_id)
    # family feedback is the reason when present, the AI note otherwise
    assert r[0] == "Ollama setup for beginners (family: Boring; too basic, want advanced stuff)"
    assert r[1] == "Prank vid (AI: cruel prank)"
    v = Video(id="c" * 11, title="T", channel_id="UC", transcript="hello")
    u = prompts.judge_messages(cat, v, [], None, 1000, rejections=r)[-1]["content"]
    assert "RECENT REJECTIONS" in u and "family: Boring; too basic, want advanced stuff" in u
    assert "RECENT REJECTIONS" in prompts.JUDGE_SYSTEM
    p = prompts.planner_messages(cat, [], [], r, [], [], queue=[])[-1]["content"]
    assert "Ollama setup for beginners" in p and "family:" in p


# ------------------------------------------------------------------ JSON parsing / llm client

@pytest.mark.parametrize("raw", [
    '{"score": 70, "summary": "ok"}',
    '<think>let me think {"score": 1}</think>\n{"score": 70, "summary": "ok"}',
    '<think>\nreasoning...\n</think>\n```json\n{"score": 70, "summary": "ok"}\n```',
    'Sure! Here is the verdict:\n```\n{"score": 70, "summary": "ok"}\n```\nHope that helps.',
    'Verdict: {"score": 70, "summary": "ok",} trailing words',
    'reasoning without open tag</think>{"score": 70, "summary": "ok"}',
    '{"score": 70, "summary": "ok"} and another {"x": 1}',
])
def test_parse_json_robust(raw):
    out = llm.parse_json(raw)
    assert out["score"] == 70 and out["summary"] == "ok"


def test_parse_json_braces_in_strings_and_failure():
    assert llm.parse_json('x {"summary": "a {weird} \\"q\\" }", "score": 5} y')["score"] == 5
    with pytest.raises(llm.LlmParseError):
        llm.parse_json("<think>{\"a\":1}</think> no json here")


def test_llm_client_falls_back_when_schema_rejected(monkeypatch):
    llm._schema_unsupported.clear()
    sent = []

    def fake_post(cfg, body, abort=None):
        sent.append(dict(body))
        req = httpx.Request("POST", "http://x/v1/chat/completions")
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"}, request=req)
        content = '<think>hmm</think>```json\n{"queries": ["a"], "channels": []}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
                              request=req)
    monkeypatch.setattr(llm, "_post", fake_post)
    out = llm.chat_json([{"role": "user", "content": "plan"}], json_schema=prompts.PLAN_SCHEMA, max_tokens=50)
    assert out == {"queries": ["a"], "channels": []}
    assert "response_format" in sent[0] and "response_format" not in sent[1]
    assert "JSON schema" in sent[1]["messages"][0]["content"]
    # remembered: next call goes straight to plain mode
    llm.chat_json([{"role": "user", "content": "plan"}], json_schema=prompts.PLAN_SCHEMA, max_tokens=50)
    assert "response_format" not in sent[2]
    llm._schema_unsupported.clear()


def test_llm_client_retries_then_raises(monkeypatch):
    calls = []

    def fake_post(cfg, body, abort=None):
        calls.append(1)
        return httpx.Response(503, text="busy", request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(llm.LlmError):
        llm.chat_json([{"role": "user", "content": "x"}], max_tokens=5)
    assert len(calls) == 3  # 1 + 2 retries


def test_images_attached_to_last_user_message():
    msgs = llm._with_images([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], ["data:x"])
    assert msgs[1]["content"][0] == {"type": "text", "text": "u"}
    assert msgs[1]["content"][1]["image_url"]["url"] == "data:x"


# ------------------------------------------------------------------ live

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("BT_LIVE") != "1", reason="set BT_LIVE=1 to hit the real LLM")
def test_live_judge_prompt():
    cat = make_category()
    transcript = (
        "Okay kids, today we're building the biggest marble run this house has ever seen. "
        "First the funnel at the top of the stairs. Why does the marble speed up here? Because it's going "
        "downhill, gravity pulls it. Now Emma, what happens if we make the slope steeper? Let's test it. "
        "Whoa, it flew right off the track! That's too much speed for the curve, so we add a wall. "
        "Next the loop: if the marble is too slow it falls out at the top, so we start it higher up. "
        "Three tries... and it works! Now the long run through the hallway, we used pool noodles cut in half. "
        "The cat is very interested. Final section: the marble drops into the cup and rings the bell. "
        "Thanks for watching, try building one with cardboard tubes at home and send us pictures.")
    v = Video(id="aaaaaaaaaaa", title="We Built a Giant Marble Run Through Our House", channel_id="UCx9f8K2mQ",
              channel_name="Maker Family", duration_s=840, view_count=2_400_000, like_count=98_000,
              description="We spent 3 weekends building a 40 m marble run with the kids. Plans in the link.",
              transcript=transcript,
              comments=[{"author": "a", "text": "My 6yo watched this 5 times and now builds his own", "likes": 900},
                        {"author": "b", "text": "Love how they explain the physics without being boring",
                         "likes": 300},
                        {"author": "c", "text": "The cat chasing the marble at 7:10 lol", "likes": 120}])
    msgs = prompts.judge_messages(cat, v, [], None, 4000)
    # Vision path: send any real thumbnail just to exercise image input (verdict not asserted strictly).
    img = judge.fetch_thumb("dQw4w9WgXcQ") if os.environ.get("BT_LIVE_VISION") == "1" else None
    out = llm.chat_json(msgs, json_schema=prompts.JUDGE_SCHEMA, max_tokens=3000, images=[img] if img else None,
                        schema_name="verdict")
    verdict = judge.normalize(out)
    print("LIVE VERDICT:", verdict)
    assert 0 <= verdict["score"] <= 100 and verdict["summary"]
    if not img:
        assert verdict["score"] >= 60 and verdict["kid_safe"] is True


# ------------------------------------------------------------------ review regressions

def test_idle_always_sleeps_during_llm_backoff():
    """Feedback pending + LLM down must not busy-loop (review finding #1)."""
    cat = make_category(enabled=False)
    fl = FakeLlm()
    fl.fail = True
    w = make_worker(fake_llm=fl)
    with session() as s:
        s.add(Video(id="v1", title="x", channel_id="UCx"))
        s.add(Feedback(profile_id=cat.profile_id, video_id="v1", rating=-1, text="bad"))
        s.commit()
    n = {"it": 0}
    orig = w.iterate

    def it():
        n["it"] += 1
        if n["it"] > 20:
            w.rt.stop_requested = True
        return orig()
    w.iterate = it
    w.run()
    assert n["it"] == 21
    assert w.ft.slept >= 20 * 5  # every idle pass slept at least IDLE_MIN_S
    assert len(fl.calls) <= 2  # LLM retried only after its 5-minute backoff


def test_idle_feedback_wake_respects_min_sleep():
    w = make_worker()
    cat = make_category(enabled=False)
    with session() as s:
        s.add(Feedback(profile_id=cat.profile_id, video_id="v1", rating=0, text="note"))
        s.commit()
    w._idle()
    assert 5 <= w.ft.slept <= 7


def test_pause_interrupts_llm_call_and_keeps_candidate():
    cat = make_category()

    class PausingLlm(FakeLlm):
        def chat_json(self, messages, abort=None, **kw):
            if kw.get("schema_name") == "verdict":
                update_agent_control(paused=True)
                abort()  # what llm._post's polling does mid-request
            return super().chat_json(messages, **kw)
    fl = PausingLlm()
    w = make_worker(fake_llm=fl)
    with pytest.raises(Paused):
        for _ in range(5):
            w.iterate()
    assert any(c.status == "enriched" for c in cands(cat.id).values())
    assert w.rt.llm_backoff_until is None  # a pause is not an LLM failure


class _Abort(Exception):
    pass


def _slow_llm_server(seconds=8):
    """Local HTTP server that 'generates' for `seconds`, noticing client disconnects like llama.cpp does."""
    import http.server
    import socket
    import threading
    import time as _t
    log = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            t0 = _t.monotonic()
            log.append(("start", t0))
            self.connection.settimeout(0.1)
            while _t.monotonic() - t0 < seconds:
                try:
                    if self.connection.recv(1, socket.MSG_PEEK) == b"":
                        log.append(("client_closed", _t.monotonic()))
                        return
                except socket.timeout:
                    pass
            body = b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            log.append(("done", _t.monotonic()))

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, log


def test_llm_abort_cancels_request_and_holds_lock_until_helper_exits():
    import threading
    import time as _t
    srv, log = _slow_llm_server(8)
    cfg = {"base_url": f"http://127.0.0.1:{srv.server_port}", "timeout_s": 30}
    t0 = _t.monotonic()

    def abort():
        if log and _t.monotonic() - log[0][1] > 0.5:
            raise _Abort()
    try:
        with pytest.raises(_Abort):
            llm._post(cfg, {"x": 1}, abort)
        t_abort = _t.monotonic()
        assert t_abort - t0 < 3
        helpers = [th for th in threading.enumerate() if th.name == "llm-request"]
        # lock is still owned by the (cancelling) helper, or the helper already finished
        assert llm._lock.locked() or not any(th.is_alive() for th in helpers)
        for th in helpers:
            th.join(3)
            assert not th.is_alive()
        assert not llm._lock.locked()
        deadline = _t.monotonic() + 2
        while not any(k == "client_closed" for k, _ in log) and _t.monotonic() < deadline:
            _t.sleep(0.05)
        closed = [ts for k, ts in log if k == "client_closed"]
        assert closed and closed[0] - t_abort < 1.5  # server saw the disconnect promptly
        assert not any(k == "done" for k, _ in log)
        # next request waits for the lock (never overlaps) and works normally
        srv2, log2 = _slow_llm_server(0)
        r = llm._post({"base_url": f"http://127.0.0.1:{srv2.server_port}", "timeout_s": 5}, {"x": 1}, lambda: None)
        assert r.status_code == 200
        srv2.shutdown()
    finally:
        srv.shutdown()


def test_llm_lock_waits_for_previous_request():
    import threading
    import time as _t
    llm._lock.acquire()
    released = {}

    def later():
        _t.sleep(0.6)
        released["t"] = _t.monotonic()
        llm._lock.release()
    threading.Thread(target=later).start()
    srv, log = _slow_llm_server(0)
    try:
        llm._post({"base_url": f"http://127.0.0.1:{srv.server_port}", "timeout_s": 5}, {"x": 1}, lambda: None)
        assert log[0][1] >= released["t"]  # request only started after the lock was free
    finally:
        srv.shutdown()


def test_llm_abort_during_retry_sleep(monkeypatch):
    posts = []

    def fake_post(cfg, body, abort=None):
        posts.append(1)
        return httpx.Response(503, text="busy", request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(llm, "_post", fake_post)
    state = {"n": 0}

    def abort():
        state["n"] += 1
        if len(posts) >= 1 and state["n"] > 2:
            raise _Abort()
    with pytest.raises(_Abort):
        llm.chat_json([{"role": "user", "content": "x"}], max_tokens=5, abort=abort)
    assert len(posts) == 1


def test_llm_non_schema_400_does_not_disable_schema(monkeypatch):
    llm._schema_unsupported.clear()
    sent = []

    def fake_post(cfg, body, abort=None):
        sent.append(dict(body))
        return httpx.Response(400, json={"error": "the request exceeds the available context size"},
                              request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(llm.LlmError):
        llm.chat_json([{"role": "user", "content": "x"}], json_schema=prompts.PLAN_SCHEMA, max_tokens=5)
    assert not llm._schema_unsupported
    assert all("response_format" in b for b in sent)


def test_llm_truncated_thinking_retries_without_thinking(monkeypatch):
    sent = []

    def fake_post(cfg, body, abort=None):
        sent.append(dict(body))
        req = httpx.Request("POST", "http://x")
        if len(sent) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "", "reasoning_content": "hmm" * 99},
                                                          "finish_reason": "length"}]}, request=req)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}]},
                              request=req)
    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm.chat_json([{"role": "user", "content": "x"}], max_tokens=100) == {"a": 1}
    assert "chat_template_kwargs" not in sent[0]
    assert sent[1]["chat_template_kwargs"] == {"enable_thinking": False} and sent[1]["max_tokens"] == 150


def test_run_survives_errors_in_handlers():
    w = make_worker()
    n = {"i": 0}

    def bad_step():
        n["i"] += 1
        if n["i"] >= 3:
            w.rt.stop_requested = True
        raise RuntimeError("database is locked")
    w._step = bad_step
    orig_event = w.rt.event
    w.rt.event = lambda kind, *a, **k: (_ for _ in ()).throw(RuntimeError("locked")) if kind == "error" \
        else orig_event(kind, *a, **k)
    w.run()
    assert n["i"] == 3
    assert get_agent_state()["state"] == "offline"


def test_orphaned_enriched_candidates_marked_error():
    cat = make_category()
    with session() as s:
        s.add(Candidate(category_id=cat.id, video_id="missingvid1", status="enriched"))
        s.commit()
    assert judge.pending() == []
    assert cands(cat.id)["missingvid1"].status == "error"


def test_no_thumbnail_during_backoff():
    cat = make_category()
    fl = FakeLlm()
    w = make_worker(fake_llm=fl)
    with session() as s:
        s.add(Video(id="vidGOOD0001", title="Marble run", channel_id="UCgood", enriched_at=now()))
        c = Candidate(category_id=cat.id, video_id="vidGOOD0001", status="enriched")
        s.add(c)
        s.commit()
        s.refresh(c)
    w.rt.backoff_until = now() + timedelta(hours=1)
    assert judge.judge_one(w.rt, c) == "accepted"
    assert fl.calls[-1]["images"] is None


def test_structured_output_http_truncation_increases_budget(monkeypatch):
    sent = []
    configs = []
    original = dict(llm._cfg())

    def fake_post(cfg, body, abort=None):
        configs.append(dict(cfg))
        sent.append(dict(body))
        if len(sent) == 1:
            return httpx.Response(502, json={'error': {
                'type': 'structured_output_failed',
                'message': 'structured output was incomplete (finish_reason=length); increase the output budget'
            }})
        return httpx.Response(200, json={'choices': [
            {'message': {'content': '{"brief":"Science", "seed_queries":["experiments"]}'},
             'finish_reason': 'stop'}]})

    monkeypatch.setattr(llm, '_post', fake_post)
    result = llm.chat_json([{'role': 'user', 'content': 'examples'}],
                           json_schema={'type': 'object'}, max_tokens=32768,
                           timeout_s=600, reasoning_effort='medium')
    assert result['brief'] == 'Science'
    assert [b['max_tokens'] for b in sent] == [32768, 49152]
    assert sent[0]['chat_template_kwargs'] == {'enable_thinking': True}
    assert sent[1]['chat_template_kwargs'] == {'enable_thinking': True}
    assert all(b['reasoning_effort'] == 'medium' for b in sent)
    assert all(c['timeout_s'] == 600 for c in configs)
    assert llm._cfg() == original  # Import-specific timeout does not change worker settings.


def test_learning_cannot_replace_existing_rule_from_one_correction():
    cat = make_category()
    with session() as s:
        rule = Lesson(profile_id=cat.profile_id, text="Avoid videos with frightening pranks")
        s.add(rule)
        s.add(Video(id="exception01", title="A harmless surprise"))
        fb = Feedback(profile_id=cat.profile_id, video_id="exception01", category_id=cat.id,
                      rating=1, text="This one was harmless")
        s.add(fb); s.commit(); s.refresh(rule); s.refresh(fb)
        rule_id = rule.id
    w = make_worker()
    w.rt.llm.chat_json = lambda *args, **kwargs: {"lessons": [{
        "text": "Allow harmless surprises in this playlist", "category_id": cat.id,
        "channel_id": None, "replaces_lesson_id": rule_id}]}
    learn._process_profile(w.rt, cat.profile_id, [fb])
    with session() as s:
        rules = s.exec(select(Lesson).where(Lesson.profile_id == cat.profile_id)).all()
        assert len(rules) == 2
        assert s.get(Lesson, rule_id).text == "Avoid videos with frightening pranks"
        assert s.get(Lesson, rule_id).category_id is None
        assert any(le.category_id == cat.id and "harmless" in le.text for le in rules)


def test_ai_judge_overlaps_research(monkeypatch):
    import threading
    from bt.agent import enrich

    cat = make_category()
    w = make_worker()
    with session() as s:
        for vid, status in (("ready", "enriched"), ("next", "found")):
            s.add(Video(id=vid, title=vid))
            s.add(Candidate(category_id=cat.id, video_id=vid, status=status))
        s.commit()
    judging = threading.Event()
    researching = threading.Event()

    def judge_one(rt, cand):
        judging.set()
        assert researching.wait(3), "research was blocked behind AI"
        return "accepted"

    def research(rt, rl, cand, category):
        researching.set()
        assert judging.wait(3), "AI was blocked behind research"
        return "enriched"

    monkeypatch.setattr(judge, "judge_one", judge_one)
    monkeypatch.setattr(enrich, "enrich_one", research)
    w.iterate()
    assert judging.is_set() and researching.is_set()


@pytest.mark.parametrize("empty_transcript", [False, True])
def test_research_resumes_after_comments_block_without_refetch(empty_transcript):
    from bt.agent import enrich
    from bt.db import get_kv

    cat = make_category()
    w = make_worker()
    with session() as s:
        s.add(Video(id="resume", title="resume"))
        cand = Candidate(category_id=cat.id, video_id="resume")
        s.add(cand)
        s.commit()
    if empty_transcript:
        def transcript(info, max_chars):
            w.rt.yt._log("transcript")
            return ""
        w.rt.yt.transcript = transcript
    w.rt.yt.block_on.add("comments")
    with pytest.raises(YtBlocked):
        enrich.enrich_one(w.rt, w.rl, cand, cat)
    assert "transcript" in get_kv("research:resume")
    w.rt.yt.block_on.clear()
    restarted = make_worker(yt=w.rt.yt)
    assert enrich.enrich_one(restarted.rt, restarted.rl, cand, cat) == "enriched"
    assert w.rt.yt.calls == ["metadata", "transcript", "comments", "comments"]
    assert get_kv("research:resume") == {}


def test_research_prefers_cached_then_trusted_candidates():
    cat = make_category()
    with session() as s:
        s.add(ProfileChannel(profile_id=cat.profile_id, channel_id="trusted", trust=0.8))
        for vid, channel, enriched in (("old", "neutral", None), ("trusted", "trusted", None),
                                        ("cached", "neutral", now())):
            s.add(Video(id=vid, channel_id=channel, enriched_at=enriched))
            s.add(Candidate(category_id=cat.id, video_id=vid))
        s.commit()
        needs = data.category_needs(s)
    assert Worker._next_found(needs).video_id == "cached"
    with session() as s:
        c = s.exec(select(Candidate).where(Candidate.video_id == "cached")).one()
        c.status = "enriched"
        s.add(c)
        s.commit()
    assert Worker._next_found(needs).video_id == "trusted"


def test_expired_caption_checkpoint_refreshes_metadata():
    from bt.agent import enrich
    from bt.db import get_kv

    cat = make_category()
    w = make_worker()
    with session() as s:
        s.add(Video(id="expired", title="expired", fetched_at=now()))
        cand = Candidate(category_id=cat.id, video_id="expired")
        s.add(cand)
        s.commit()
    save_setting("research:expired", {"metadata_done": True, "metadata_ts": 0,
                                      "caption_info": {"id": "expired"}})
    assert enrich.enrich_one(w.rt, w.rl, cand, cat) == "enriched"
    assert w.rt.yt.calls == ["metadata", "transcript", "comments"]
    assert get_kv("research:expired") == {}


def test_planner_receives_scoped_search_yield_not_just_latest_rejections():
    cat = make_category()
    with session() as s:
        other = Category(profile_id=cat.profile_id, name="Other")
        s.add(other); s.commit()
        for vid, cid, status, source, title in [
            ("yieldbad001", cat.id, "rejected", "search: Amazing", "Pop song"),
            ("yieldbad002", cat.id, "rejected", "search: Amazing", "Another song"),
            ("yieldgood01", cat.id, "accepted", "search: automaton mechanism build", "Original automaton"),
            ("yieldfound1", cat.id, "enriched", "search: Amazing", "Not judged"),
            ("yieldother1", other.id, "accepted", "search: Amazing", "Other playlist")]:
            s.add(Video(id=vid, title=title))
            s.add(Candidate(category_id=cid, video_id=vid, status=status, source=source, judged_at=now()))
        s.commit()
        outcomes = data.discovery_yield(s, cat.id)
    assert any("search: Amazing: 0 accepted, 2 rejected" in line for line in outcomes)
    assert any("search: automaton mechanism build: 1 accepted, 0 rejected" in line for line in outcomes)
    prompt = prompts.planner_messages(cat, [], [], [], [], [], search_yield=outcomes)[-1]["content"]
    assert "RECENT DISCOVERY OUTCOMES" in prompt and "Pop song" in prompt
    assert "Not judged" not in prompt and "Other playlist" not in prompt
