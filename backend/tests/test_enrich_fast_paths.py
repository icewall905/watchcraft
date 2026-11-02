"""Network-free research paths retain admission and decision safeguards."""
import pytest
from test_agent import fresh_db, make_category, make_worker  # noqa: F401

from bt import ytdl
from bt.agent import enrich, judge
from bt.db import get_kv, session
from bt.models import Candidate, Video, now


def candidate(cat, vid):
    with session() as s:
        s.add(Video(id=vid, title="Science build", duration_s=600))
        c = Candidate(category_id=cat.id, video_id=vid)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c


def test_cache_only_never_extracts_on_miss(monkeypatch):
    ytdl.cache_clear()
    monkeypatch.setattr(ytdl, "_db_read_info", lambda vid: None)
    monkeypatch.setattr(ytdl, "_extract", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert ytdl.cached_video_info("fastpath001") is None


def test_cached_metadata_and_absent_track_skip_network_turns():
    cat = make_category()
    w = make_worker()
    cand = candidate(cat, "fastpath001")
    w.rt.yt.cached_video_info = lambda vid: {"id": vid, "title": "Science build", "duration_s": 600}
    w.rt.yt.pick_track = lambda info: None
    turns = []
    w.rl.wait_turn = lambda kind, *a, **kw: turns.append(kind)
    assert enrich.enrich_one(w.rt, w.rl, cand, cat) == "enriched"
    assert turns == ["comments"]
    assert w.rt.yt.calls == ["comments"]
    with session() as s:
        assert s.get(Video, cand.video_id).transcript == ""


def test_cache_miss_gets_admission_and_no_track_checkpoint_survives_block():
    cat = make_category()
    w = make_worker()
    cand = candidate(cat, "fastpath002")
    w.rt.yt.cached_video_info = lambda vid: None
    w.rt.yt.pick_track = lambda info: None
    turns = []
    w.rl.wait_turn = lambda kind, *a, **kw: turns.append(kind)
    w.rt.yt.block_on.add("comments")
    with pytest.raises(w.rt.yt.YtBlocked):
        enrich.enrich_one(w.rt, w.rl, cand, cat)
    assert turns == ["metadata", "comments"]
    assert get_kv("research:fastpath002")["transcript_state"] == "no_track"


def test_cached_research_does_not_overwrite_manual_decision_or_dismissal():
    cat = make_category()
    w = make_worker()
    for vid, status, dismissed in [("fastpath003", "accepted", False), ("fastpath004", "found", True)]:
        cand = candidate(cat, vid)
        with session() as s:
            v = s.get(Video, vid)
            v.enriched_at = now()
            c = s.get(Candidate, cand.id)
            c.status, c.dismissed = status, dismissed
            s.add(v)
            s.add(c)
            s.commit()
        assert enrich.enrich_one(w.rt, w.rl, cand, cat) == "gone"
        with session() as s:
            c = s.get(Candidate, cand.id)
            assert (c.status, c.dismissed) == (status, dismissed)


def test_researched_candidate_reused_without_turns_and_disabled_category_skipped():
    cat = make_category()
    w = make_worker()
    cand = candidate(cat, "fastpath005")
    with session() as s:
        v = s.get(Video, cand.video_id)
        v.enriched_at = now()
        s.add(v)
        s.commit()
    w.rl.wait_turn = lambda *a, **kw: pytest.fail("unexpected network turn")
    assert enrich.enrich_one(w.rt, w.rl, cand, cat) == "enriched"
    assert not w.rt.yt.calls
    other = candidate(cat, "fastpath006")
    with session() as s:
        category = s.get(type(cat), cat.id)
        category.enabled = False
        s.add(category)
        s.commit()
    assert enrich.enrich_one(w.rt, w.rl, other, cat) == "gone"


def test_cached_negative_metadata_does_not_advance_request_spacing():
    cat = make_category()
    w = make_worker()
    cand = candidate(cat, 'fastpath007')
    w.rt.yt.cached_video_info = lambda vid: (_ for _ in ()).throw(w.rt.yt.YtUnavailable('cached unavailable'))
    w.rl.wait_turn = lambda *a, **k: pytest.fail('a cached failure must not consume an outbound turn')
    assert enrich.enrich_one(w.rt, w.rl, cand, cat) == 'error'
    assert w.rl.last_at is None
    assert w.rt.yt.calls == []


def test_network_enrichment_never_overwrites_manual_decision():
    cat = make_category()
    w = make_worker()
    cand = candidate(cat, 'fastpath008')
    def comments(video_id, max_comments=40, origin='worker'):
        with session() as s:
            c = s.get(Candidate, cand.id)
            c.status = 'accepted'
            s.add(c)
            s.commit()
        return [{'text': 'useful'}]
    w.rt.yt.comments = comments
    assert enrich.enrich_one(w.rt, w.rl, cand, cat) == 'gone'
    with session() as s:
        assert s.get(Candidate, cand.id).status == 'accepted'
        assert s.get(Video, cand.video_id).comments == [{'text': 'useful'}]


def _acquired_comments(monkeypatch, setting, vid):
    cat = make_category()
    w = make_worker()
    cand = candidate(cat, vid)
    monkeypatch.setattr(w.rt, "throttle", lambda: {"max_comments": setting, "transcript_max_chars": 5000})
    seen = []

    def comments(video_id, max_comments=40, origin="worker"):
        seen.append(max_comments)
        return [{"text": "useful"}]

    w.rt.yt.comments = comments
    assert enrich.enrich_one(w.rt, w.rl, cand, cat) == "enriched"
    return seen


def test_comment_acquisition_is_capped_at_the_judge_evidence_limit(monkeypatch):
    # Production asks for 60 while the judge reads 30; the extra pages are waste.
    assert _acquired_comments(monkeypatch, 60, "fastpath009") == [judge.JUDGE_COMMENTS_MAX]


def test_comment_acquisition_keeps_a_lower_setting(monkeypatch):
    assert _acquired_comments(monkeypatch, 12, "fastpath010") == [12]
