"""Shared learner and memory stay scoped between video and article feedback."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect
from sqlmodel import SQLModel, select

from bt.agent import knowledge, learn, memory
from bt.api import core as core_api
from bt.db import _migrate_shared_learning_columns, engine, init_db, session
from bt.models import Feedback, Lesson, LessonEvidenceState, LessonRevision, MemoryCore, Profile, ProfileChannel
from bt.hub.models import ContentItem, ItemState


@pytest.fixture(autouse=True)
def fresh():
    SQLModel.metadata.drop_all(engine)
    init_db()
    yield


def test_old_schema_migration_is_additive_and_defaults_rows_to_video():
    from sqlalchemy import create_engine
    old = create_engine("sqlite://")
    with old.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE feedback (id INTEGER PRIMARY KEY, profile_id INTEGER, video_id VARCHAR, category_id INTEGER, rating INTEGER, chips JSON, text TEXT, processed BOOLEAN, created_at DATETIME)")
        conn.exec_driver_sql("CREATE TABLE lesson (id INTEGER PRIMARY KEY, profile_id INTEGER, category_id INTEGER, channel_id VARCHAR, text TEXT, weight FLOAT, active BOOLEAN, source_feedback_ids JSON, created_at DATETIME, updated_at DATETIME)")
        conn.exec_driver_sql("CREATE TABLE memorycore (id INTEGER PRIMARY KEY, profile_id INTEGER, text TEXT, sources JSON, budget_tokens INTEGER, created_at DATETIME)")
        conn.exec_driver_sql("INSERT INTO feedback (id, profile_id, video_id, category_id, rating, chips, text, processed) VALUES (1, 2, 'video1234567', NULL, 1, '[]', 'liked', 0)")
        conn.exec_driver_sql("INSERT INTO lesson (id, profile_id, text, active) VALUES (4, 2, 'Prefer science', 1)")
        conn.exec_driver_sql("INSERT INTO memorycore (id, profile_id, text) VALUES (7, 2, 'video core')")
        _migrate_shared_learning_columns(conn)
        feedback = conn.exec_driver_sql("SELECT content_type, item_id, video_id FROM feedback WHERE id=1").one()
        lesson = conn.exec_driver_sql("SELECT content_type FROM lesson WHERE id=4").one()
        core = conn.exec_driver_sql("SELECT content_type FROM memorycore WHERE id=7").one()
        assert tuple(feedback) == ("video", None, "video1234567")
        assert lesson[0] == core[0] == "video"
        assert {"content_type", "item_id"}.issubset({c["name"] for c in inspect(conn).get_columns("feedback")})
    old.dispose()


def test_article_feedback_learns_article_rule_without_creating_video_trust():
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        item = ContentItem(url="https://example.test/a", type="news", title="A thoughtful climate report",
                           publisher="Example", summary="Explains the evidence and policy trade-offs.")
        s.add(item); s.flush()
        fb = Feedback(profile_id=pid, content_type="article", item_id=item.id, video_id="", rating=-1,
                      chips=["too shallow"], text="I prefer reporting with evidence and context")
        s.add(fb); s.commit()
        iid, fid = item.id, fb.id

    seen = []
    def chat(messages, **kwargs):
        seen.append((messages, kwargs))
        return {"lessons": [{"text": "Prefer reported articles with evidence and useful context.",
                             "category_id": None, "channel_id": None, "replaces_lesson_id": None,
                             "source_feedback_ids": [fid]}], "reviews": []}
    rt = SimpleNamespace(llm=SimpleNamespace(chat_json=chat), check=lambda: None,
                         state=lambda *a, **k: None, event=lambda *a, **k: None,
                         llm_ok=lambda: True, llm_failed=lambda *a, **k: pytest.fail(str(a)))
    assert learn._process_profile(rt, pid, [fb], content_type="article") == 1
    with session() as s:
        article_lessons = memory.active(s, pid, "article")
        assert len(article_lessons) == 1
        assert article_lessons[0].category_id is None and article_lessons[0].channel_id is None
        assert memory.active(s, pid, "video") == []
        saved = s.get(Feedback, fid)
        assert saved.processed and saved.content_type == "article" and saved.item_id == iid
        assert not s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == pid)).first()
    system = seen[0][0][0]["content"]
    assert "article curation" in system and "category_id and channel_id to null" in system


def test_video_core_and_lesson_fingerprints_keep_the_legacy_hash():
    lesson = Lesson(id=3, profile_id=1, text="Keep explanations", category_id=9, channel_id="UC123", active=True)
    legacy = hashlib.sha256(json.dumps([lesson.text, lesson.category_id, lesson.channel_id, lesson.active],
                                      ensure_ascii=False).encode()).hexdigest()
    assert memory.fingerprint(lesson) == legacy
    article = Lesson(id=4, profile_id=1, content_type="article", text="Keep context", active=True)
    assert memory.fingerprint(article) != hashlib.sha256(json.dumps([article.text, article.category_id,
        article.channel_id, article.active], ensure_ascii=False).encode()).hexdigest()


def test_article_memory_versions_and_lessons_are_type_scoped():
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        video_rule = Lesson(profile_id=pid, content_type="video", text="Prefer explainers")
        article_rule = Lesson(profile_id=pid, content_type="article", text="Prefer sourced reporting")
        s.add(video_rule); s.add(article_rule); s.flush()
        s.add(MemoryCore(profile_id=pid, content_type="video", text="video core", sources={str(video_rule.id): memory.fingerprint(video_rule)}))
        s.add(MemoryCore(profile_id=pid, content_type="article", text="article core", sources={str(article_rule.id): memory.fingerprint(article_rule)}))
        s.commit()
        assert [x.text for x in memory.active(s, pid)] == ["Prefer explainers"]
        assert [x.text for x in memory.active(s, pid, "article")] == ["Prefer sourced reporting"]
        assert memory.latest(s, pid).text == "video core"
        assert memory.latest(s, pid, "article").text == "article core"
        assert memory.select_memory(s, pid, [article_rule], content_type="article")[0] == "article core"
        assert memory.select_memory(s, pid, [video_rule], content_type="video")[0] == "video core"


def test_article_neutral_undo_and_repeat_votes_do_not_multiply_evidence():
    from bt.agent.knowledge import counts
    assert counts({"1": {"stance": "support", "item_id": 17},
                   "2": {"stance": "contradict", "item_id": 17}}) == (0, 1)
    assert counts({"1": {"stance": "support", "item_id": 17},
                   "2": {"stance": "irrelevant", "item_id": 17}}) == (0, 0)
    assert counts({"1": {"stance": "support", "video_id": "v1"},
                   "2": {"stance": "irrelevant", "video_id": "v1"}}) == (1, 0)


def test_article_feedback_undo_does_not_create_lesson_even_if_model_proposes_one():
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        item = ContentItem(url="https://example.test/undo", type="article", title="A report")
        s.add(item); s.flush()
        fb = Feedback(profile_id=pid, content_type="article", item_id=item.id, video_id="", rating=0)
        s.add(fb); s.commit()
    def chat(messages, **kwargs):
        return {"lessons": [{"text": "Prefer this publisher.", "category_id": None, "channel_id": None,
                             "replaces_lesson_id": None, "source_feedback_ids": [fb.id]}], "reviews": []}
    rt = SimpleNamespace(llm=SimpleNamespace(chat_json=chat), check=lambda: None,
                         state=lambda *a, **k: None, event=lambda *a, **k: None,
                         llm_ok=lambda: True, llm_failed=lambda *a, **k: pytest.fail(str(a)))
    assert learn._process_profile(rt, pid, [fb], content_type="article") == 1
    with session() as s:
        assert memory.active(s, pid, "article") == []


def test_old_article_vote_is_ignored_when_a_newer_vote_is_not_reviewed_yet():
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        item = ContentItem(url="https://example.test/latest", type="news", title="A report")
        s.add(item); s.flush()
        old = Feedback(profile_id=pid, content_type="article", item_id=item.id, rating=-1)
        s.add(old); s.flush()
        lesson = Lesson(profile_id=pid, content_type="article", text="Avoid this subject",
                        source_feedback_ids=[old.id])
        s.add(lesson); s.flush()
        state = knowledge.register(s, lesson)
        state.assessments = {str(old.id): {"stance": "support", "item_id": item.id,
                                            "signature": knowledge._feedback_signature(old)}}
        s.add(state)
        s.add(Feedback(profile_id=pid, content_type="article", item_id=item.id, rating=0))
        s.commit()
        assert knowledge.describe(s, lesson)["support"] == 0


def test_article_generic_dislike_uses_deterministic_narrow_correction():
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        ids = []
        for index, rating, text in ((0, -1, "Boring"), (1, 2, "Good"), (2, 2, "Excellent")):
            item = ContentItem(url=f"https://example.test/{index}", type="article", title=f"Article {index}")
            s.add(item); s.flush()
            fb = Feedback(profile_id=pid, content_type="article", item_id=item.id, rating=rating, text=text)
            s.add(fb); s.flush(); ids.append((item.id, fb.id))
        lesson = Lesson(profile_id=pid, content_type="article", text="Avoid all reports on this subject",
                        source_feedback_ids=[ids[0][1]])
        s.add(lesson); s.commit()
        lid = lesson.id
    stances = {fid: ("support" if index == 0 else "contradict") for index, (_iid, fid) in enumerate(ids)}
    calls = []
    def chat(messages, **kwargs):
        name = kwargs["schema_name"]; calls.append(name)
        if name == "lesson_evidence":
            body = json.loads(messages[-1]["content"])
            return {"explicit_instruction": False, "assessments": [
                {"feedback_id": row["feedback_id"], "stance": stances[row["feedback_id"]], "reason": "Directly relevant"}
                for row in body["feedback"]]}
        if name == "lesson_revision_audit":
            return {"ok": True, "reason": "Two distinct positive articles challenge an unsupported broad inference."}
        raise AssertionError(name)
    rt = SimpleNamespace(llm=SimpleNamespace(chat_json=chat), check=lambda: None,
                         state=lambda *a, **k: None, event=lambda *a, **k: None)
    assert knowledge.review(rt, pid, {"lesson_id": lid, "text": "candidate", "feedback_ids": [ids[1][1], ids[2][1]]},
                            {ids[1][1], ids[2][1]}, content_type="article")
    with session() as s:
        revision = s.exec(select(LessonRevision)).one()
        assert "article" in revision.after and "unspecified" in revision.after
        assert "lesson_revision" not in calls


def test_deleting_latest_article_vote_recomputes_state_and_queues_refresh(monkeypatch):
    queued = []
    monkeypatch.setattr(core_api, "_article_preferences_changed", queued.append)
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        item = ContentItem(url="https://example.test/delete-vote", type="article", title="Report")
        s.add(item); s.flush()
        prior = Feedback(profile_id=pid, content_type="article", item_id=item.id, rating=1)
        latest = Feedback(profile_id=pid, content_type="article", item_id=item.id, rating=-1)
        s.add(prior); s.add(latest)
        s.add(ItemState(profile_id=pid, item_id=item.id, rating=-1, dismissed=True))
        s.commit()
        iid, fid = item.id, latest.id
    with session() as s:
        core_api.delete_feedback(fid, pid=pid, s=s)
    with session() as s:
        state = s.get(ItemState, (pid, iid))
        assert state.rating == 1 and not state.dismissed
        assert s.get(Feedback, prior.id) is not None
    assert queued == [pid]


def test_article_lesson_crud_queues_rejudging_after_commit(monkeypatch):
    queued = []
    monkeypatch.setattr(core_api, "_article_preferences_changed", queued.append)
    with session() as s:
        pid = s.exec(select(Profile.id).order_by(Profile.id)).first()
        created = core_api.create_lesson(core_api.LessonIn(content_type="article", text="Prefer source links"), pid=pid, s=s)
        lid = created.id
    with session() as s:
        core_api.patch_lesson(lid, {"text": "Prefer linked primary sources"}, pid=pid, s=s)
    with session() as s:
        core_api.delete_lesson(lid, pid=pid, s=s)
    assert queued == [pid, pid, pid]
