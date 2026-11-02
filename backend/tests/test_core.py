import os
import tempfile

os.environ.setdefault("BT_DATA_DIR", tempfile.mkdtemp(prefix="bt-test-"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import select  # noqa: E402

from bt.db import emit, session  # noqa: E402
from bt.main import app  # noqa: E402
from bt.models import AgentEvent, Candidate, Category, Download, Feedback, Video, WatchHistory, now  # noqa: E402
from bt.agent import data  # noqa: E402


def client():
    c = TestClient(app)
    c.__enter__()  # run lifespan (init_db)
    return c


def test_profiles_categories_feed_flow():
    c = client()
    profiles = c.get("/api/profiles").json()
    assert profiles and profiles[0]["name"]
    kid = c.post("/api/profiles", json={"name": "Kids", "emoji": "🧒"}).json()
    h = {"X-Profile-Id": str(kid["id"])}

    cat = c.post("/api/categories", json={"name": "Science", "brief": "fun science"}, headers=h).json()
    assert cat["profile_id"] == kid["id"]
    # Other profile cannot see it
    assert all(x["id"] != cat["id"] for x in c.get("/api/categories").json())
    assert c.get(f"/api/categories/{cat['id']}").status_code == 404

    with session() as s:
        s.add(Video(id="abcdefghijk", title="Egg drop", channel_id="UC1", duration_s=600))
        s.add(Candidate(category_id=cat["id"], video_id="abcdefghijk", status="accepted", score=90, summary="fun"))
        s.commit()

    home = c.get("/api/feed/home", headers=h).json()
    row = next(r for r in home if r["kind"] == "category")
    assert row["items"][0]["video"]["title"] == "Egg drop"

    # watching to the end hides it from the feed
    c.post("/api/history", json={"video_id": "abcdefghijk", "position_s": 590, "duration_s": 600}, headers=h)
    assert c.get(f"/api/feed/category/{cat['id']}", headers=h).json() == []

    fb = c.post("/api/feedback", json={"video_id": "abcdefghijk", "category_id": cat["id"], "rating": -1,
                                       "text": "mean to the cat"}, headers=h).json()
    assert fb["profile_id"] == kid["id"]
    assert c.post("/api/lessons", json={"text": "No cruelty"}, headers=h).status_code == 200
    assert len(c.get("/api/lessons", headers=h).json()) == 1

    ch = c.put("/api/channels/UC1", json={"name": "Chan", "subscribed": True}, headers=h).json()
    assert ch["subscribed"]
    assert c.delete(f"/api/profiles/{kid['id']}").json()["ok"]


def test_mark_seen():
    c = client()
    profiles = c.get("/api/profiles").json()
    h = {"X-Profile-Id": str(profiles[0]["id"])}
    cat = c.post("/api/categories", json={"name": "S", "brief": "b"}, headers=h).json()
    with session() as s:
        s.add(Video(id="seenit00000", title="Seen", channel_id="UC1", duration_s=600))
        s.add(Video(id="other000000", title="Other", channel_id="UC1", duration_s=600))
        s.add(Candidate(category_id=cat["id"], video_id="seenit00000", status="accepted", score=80, summary="x"))
        s.add(Candidate(category_id=cat["id"], video_id="other000000", status="accepted", score=70, summary="y"))
        s.commit()

    # invalid id → 404
    assert c.post("/api/history/seen", json={"video_id": "../nope"}, headers=h).status_code == 404
    r = c.post("/api/history/seen", json={"video_id": "seenit00000"}, headers=h).json()
    assert r["ok"] and r["dismissed"] == 1
    # gone from the feed, other profile's videos untouched
    feed = c.get(f"/api/feed/category/{cat['id']}", headers=h).json()
    assert [x["video"]["id"] for x in feed] == ["other000000"]
    # candidate dismissed → downloader / queue counts exclude it
    with session() as s:
        rows = {cand.video_id: cand for cand in s.exec(select(Candidate)).all()}
        assert rows["seenit00000"].dismissed and not rows["other000000"].dismissed
        cat_obj = s.get(Category, cat["id"])
        ctx = data.profile_ctx(s, profiles[0]["id"])
        assert data.prefilter({"id": "seenit00000", "duration_s": 600, "title": "Seen"}, cat_obj, ctx, False) == "already watched"
        assert data.prefilter({"id": "other000000", "duration_s": 600, "title": "Other"}, cat_obj, ctx, False) is None


def test_seen_survives_late_progress_and_explicit_replay():
    c = client()
    profile = c.post("/api/profiles", json={"name": "Seen and replay"}).json()
    h = {"X-Profile-Id": str(profile["id"])}
    vid = "lateprog001"
    with session() as s:
        s.add(Video(id=vid, title="Late progress", duration_s=600))
        s.commit()
    progress = {"video_id": vid, "position_s": 100, "duration_s": 600}
    assert c.post("/api/history", json=progress, headers=h).status_code == 200
    assert c.post("/api/history/seen", json={"video_id": vid}, headers=h).status_code == 200
    assert c.post("/api/history", json=progress, headers=h).json()["finished"] is True
    assert not any(row["kind"] == "continue" for row in c.get("/api/feed/home", headers=h).json())
    observed = c.get(f"/api/history/{vid}", headers=h).json()
    restarted = c.post(f"/api/history/{vid}/restart", json={"updated_at": observed["updated_at"]}, headers=h).json()
    assert restarted["restarted"] is True
    assert c.post("/api/history", json=progress, headers=h).json()["finished"] is False
    assert any(row["kind"] == "continue" for row in c.get("/api/feed/home", headers=h).json())
    # An older replay request must not undo a more recent Seen-it action.
    c.post("/api/history/seen", json={"video_id": vid}, headers=h)
    assert not c.post(f"/api/history/{vid}/restart", json={"updated_at": observed["updated_at"]}, headers=h).json()["restarted"]
    assert c.get(f"/api/history/{vid}", headers=h).json()["finished"] is True


def test_dislike_removes_continue_and_all_profile_queues():
    c = client()
    profile = c.post("/api/profiles", json={"name": "Dislike owner"}).json()
    other = c.post("/api/profiles", json={"name": "Dislike other"}).json()
    h = {"X-Profile-Id": str(profile["id"])}
    other_h = {"X-Profile-Id": str(other["id"])}
    categories = [c.post("/api/categories", json={"name": name}, headers=headers).json()
                  for name, headers in (("First", h), ("Second", h), ("Other", other_h))]
    vid = "boring00001"
    with session() as s:
        s.add(Video(id=vid, title="Boring", channel_id="boring-channel", duration_s=600))
        for cat in categories:
            s.add(Candidate(category_id=cat["id"], video_id=vid, status="accepted"))
        s.commit()
    c.put("/api/channels/boring-channel", json={"subscribed": True}, headers=h)
    for headers in (h, other_h):
        c.post("/api/history", json={"video_id": vid, "position_s": 100, "duration_s": 600}, headers=headers)
    assert c.post("/api/feedback", json={"video_id": vid, "rating": -1, "chips": ["Boring"]}, headers=h).status_code == 200
    for row in c.get("/api/feed/home", headers=h).json():
        assert vid not in [item["video"]["id"] for item in row["items"]]
    assert c.get(f"/api/feed/category/{categories[2]['id']}", headers=other_h).json()[0]["video"]["id"] == vid
    assert any(row["kind"] == "continue" for row in c.get("/api/feed/home", headers=other_h).json())
    c.post("/api/history", json={"video_id": vid, "position_s": 120, "duration_s": 600}, headers=h)
    assert not any(row["kind"] == "continue" for row in c.get("/api/feed/home", headers=h).json())


def test_playlist_pacing_is_shared_paginated_and_aware_of_current_video():
    c = client()
    profile = c.post("/api/profiles", json={"name": "Playlist pacing"}).json()
    h = {"X-Profile-Id": str(profile["id"])}
    cat = c.post("/api/categories", json={"name": "Mixed interests"}, headers=h).json()
    with session() as s:
        for i, (topic, status, dismissed) in enumerate([
            ("space", "accepted", False), ("space", "accepted", False),
            ("music", "accepted", False), ("animals", "accepted", False),
            ("music", "rejected", False), ("music", "accepted", True),
            ("music", "accepted", False),
        ]):
            vid = f"pacing{i:05d}"
            s.add(Video(id=vid, title=topic, channel_id=f"channel{i}"))
            s.add(Candidate(category_id=cat["id"], video_id=vid, status=status,
                            dismissed=dismissed, score=90 - i, tags=[topic]))
        s.add(WatchHistory(profile_id=profile["id"], video_id="pacing00006", finished=True))
        s.commit()
    url = f"/api/feed/category/{cat['id']}"
    full = c.get(url, headers=h).json()
    video_ids = lambda items: [x["video"]["id"] for x in items]
    assert video_ids(full) == ["pacing00000", "pacing00002", "pacing00003", "pacing00001"]
    pages = c.get(url + "?limit=2", headers=h).json() + c.get(url + "?offset=2&limit=2", headers=h).json()
    assert video_ids(pages) == video_ids(full)
    home = c.get("/api/feed/home?per_row=2", headers=h).json()
    assert video_ids(next(r["items"] for r in home if r.get("category_id") == cat["id"])) == video_ids(full[:2])
    next_up = c.get(url + "?after=pacing00000", headers=h).json()
    assert video_ids(next_up)[0] == "pacing00002" and "pacing00000" not in video_ids(next_up)
    # Still use the current video for pacing after it is marked watched.
    c.post("/api/history/seen", json={"video_id": "pacing00000"}, headers=h)
    assert video_ids(c.get(url + "?after=pacing00000", headers=h).json())[0] == "pacing00002"
    assert c.get(url).status_code == 404
    assert len(c.get(url + "?status=rejected", headers=h).json()) == 1


def test_feedback_dislike_rejects_candidate():
    c = client()
    profiles = c.get("/api/profiles").json()
    h = {"X-Profile-Id": str(profiles[0]["id"])}
    cat = c.post("/api/categories", json={"name": "LLM stuff", "brief": "local LLM howtos"}, headers=h).json()
    with session() as s:
        s.add(Video(id="ollama00000", title="Ollama setup for total beginners", channel_id="UC1", duration_s=600))
        s.add(Candidate(category_id=cat["id"], video_id="ollama00000", status="accepted", score=80,
                        summary="gentle intro"))
        s.commit()

    r = c.post("/api/feedback", json={"video_id": "ollama00000", "category_id": cat["id"], "rating": -1,
                                      "chips": ["Boring", "Too basic"],
                                      "text": "want more advanced videos"}, headers=h).json()
    assert r["rating"] == -1
    with session() as s:
        cand = s.exec(select(Candidate).where(Candidate.video_id == "ollama00000")).first()
        assert cand.status == "rejected" and cand.dismissed and cand.judged_at is not None
        assert cand.red_flags[-1] == "Family: Boring, Too basic, want more advanced videos"
    # the verdict log shows the family's rejection, clickable like any verdict
    evs = c.get("/api/agent/events").json()
    assert any(e["kind"] == "reject" and "You rejected" in e["message"]
               and e["payload"].get("candidate_id") == cand.id for e in evs)
    # candidate detail exposes the family reason to the drawer
    d = c.get(f"/api/agent/candidates/{cand.id}").json()
    assert d["status"] == "rejected" and any("Family:" in f for f in d["red_flags"])


def test_add_anyway_uses_intended_profile_and_records_correction_once(monkeypatch):
    c = client()
    owner = c.post("/api/profiles", json={"name": "Verdict owner"}).json()
    h = {"X-Profile-Id": str(owner["id"])}
    cat = c.post("/api/categories", json={"name": "Advanced builds"}, headers=h).json()
    with session() as s:
        s.add(Video(id="override001", title="An ambitious build", channel_id="UCoverride"))
        cand = Candidate(category_id=cat["id"], video_id="override001", status="rejected", dismissed=True,
                         score=0, summary="Too complicated", reasons=["Advanced techniques"],
                         red_flags=["Too technical"], error="old error", judged_at=now())
        s.add(cand)
        s.add(WatchHistory(profile_id=owner["id"], video_id="override001", finished=True, position_s=500))
        s.commit()
        cand_id = cand.id
    # No metadata calls are needed to restore a stored verdict, even if not fetched.
    from bt import ytdl
    monkeypatch.setattr(ytdl, "video_info", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network")))
    detail = c.get(f"/api/agent/candidates/{cand_id}").json()
    assert detail["profile_id"] == owner["id"] and detail["category_name"] == "Advanced builds"
    assert c.post(f"/api/candidates/{cand_id}/accept").status_code == 404
    result = c.post(f"/api/candidates/{cand_id}/accept", headers=h)
    assert result.status_code == 200 and result.json()["learn_from_override"]
    assert c.post(f"/api/candidates/{cand_id}/accept", headers=h).json()["learn_from_override"] is False
    with session() as s:
        candidate = s.get(Candidate, cand_id)
        assert candidate.status == "accepted" and not candidate.dismissed and not candidate.error
        assert candidate.score == 0 and candidate.red_flags == ["Too technical"]
        fbs = s.exec(select(Feedback).where(Feedback.video_id == "override001")).all()
        assert len(fbs) == 1
        fb = fbs[0]
        assert fb.profile_id == owner["id"] and fb.category_id == cat["id"] and not fb.processed
        assert fb.rating == 1 and fb.chips == ["Approved rejection"]
        assert "Too complicated" in fb.text and "Too technical" in fb.text and "Advanced techniques" in fb.text
        evs = s.exec(select(AgentEvent).where(AgentEvent.video_id == "override001")).all()
        assert len(evs) == 1 and evs[0].payload["candidate_id"] == cand_id
    feed = c.get(f"/api/feed/category/{cat['id']}", headers=h).json()
    assert [x["video"]["id"] for x in feed] == ["override001"]


def test_add_anyway_pending_and_deleted_category_do_not_learn():
    c = client()
    cat = c.post("/api/categories", json={"name": "Pending override"}).json()
    with session() as s:
        s.add(Video(id="pending0001", title="Waiting for a verdict"))
        candidate = Candidate(category_id=cat["id"], video_id="pending0001", status="enriched")
        s.add(candidate)
        s.commit()
        cand_id = candidate.id
    result = c.post(f"/api/candidates/{cand_id}/accept")
    assert result.status_code == 200 and result.json()["learn_from_override"] is False
    with session() as s:
        assert s.exec(select(Feedback).where(Feedback.video_id == "pending0001")).all() == []
    c.delete(f"/api/categories/{cat['id']}")
    assert c.post(f"/api/candidates/{cand_id}/accept").status_code == 404


def test_stats_page():
    c = client()
    profiles = c.get("/api/profiles").json()
    h = {"X-Profile-Id": str(profiles[0]["id"])}
    cat = c.post("/api/categories", json={"name": "Stats cat", "brief": "b"}, headers=h).json()
    with session() as s:
        s.add(Video(id="statsvid001", title="S1", channel_id="UC1", duration_s=600))
        s.add(Video(id="statsvid002", title="S2", channel_id="UC1", duration_s=600))
        s.add(Video(id="statsvid003", title="S3", channel_id="UC1", duration_s=600))
        s.add(Candidate(category_id=cat["id"], video_id="statsvid001", status="accepted", score=80, judged_at=now()))
        s.add(Candidate(category_id=cat["id"], video_id="statsvid002", status="rejected", score=20,
                        summary="shallow", judged_at=now()))
        s.add(Candidate(category_id=cat["id"], video_id="statsvid003", status="accepted", score=70, judged_at=now()))
        s.add(WatchHistory(profile_id=profiles[0]["id"], video_id="statsvid001", category_id=cat["id"],
                           finished=True, duration_s=600))
        s.commit()
    # the family dislikes the accepted one → its rejection is "family", not the AI's
    c.post("/api/feedback", json={"video_id": "statsvid001", "category_id": cat["id"], "rating": -1,
                                  "chips": ["Boring"]}, headers=h)

    r = c.get("/api/stats/page?days=7", headers=h).json()
    row = next(x for x in r["categories"] if x["id"] == cat["id"])
    assert row["counts"]["rejected_family"] == 1  # statsvid001, flipped by the feedback above
    assert row["counts"]["rejected_ai"] == 1      # statsvid002, by the judge
    assert row["counts"]["watched"] == 1 and row["watch_seconds"] == 600
    assert row["avg_score"] == 70.0  # over accepted candidates (statsvid001 is now rejected)
    assert row["counts"]["accepted"] == 1 and row["counts"]["in_queue"] == 1  # statsvid003

    ov = r["overview"]
    assert ov["videos"] >= 2 and ov["candidates"]["rejected_family"] >= 1 and ov["watched"] >= 1
    assert ov["feedback"]["dislike"] >= 1 and "waiting" in ov["downloads"] and "max_day" in ov["budget"]

    today = r["daily"][-1]
    assert today["watched"] >= 1 and today["feedback"] >= 1
    assert today["rejected_family"] >= 1 and today["rejected_ai"] >= 1
    assert today["watched_by_category"].get(str(cat["id"])) == 1
    assert len(r["daily"]) == 7 and r["daily"][0]["date"] < r["daily"][-1]["date"]


def test_block_channel_dismisses():
    c = client()
    profiles = c.get("/api/profiles").json()
    h = {"X-Profile-Id": str(profiles[0]["id"])}
    cat = c.post("/api/categories", json={"name": "S", "brief": "b"}, headers=h).json()
    with session() as s:
        s.add(Video(id="badchan0001", title="Bad 1", channel_id="UCbad", duration_s=600))
        s.add(Video(id="badchan0002", title="Bad 2", channel_id="UCbad", duration_s=600))
        s.add(Video(id="goodvid0000", title="Good", channel_id="UCgood", duration_s=600))
        s.add(Candidate(category_id=cat["id"], video_id="badchan0001", status="accepted", score=80, summary="x"))
        s.add(Candidate(category_id=cat["id"], video_id="badchan0002", status="found"))
        s.add(Candidate(category_id=cat["id"], video_id="goodvid0000", status="accepted", score=70, summary="y"))
        s.commit()

    r = c.post("/api/channels/UCbad/block", headers=h).json()
    assert r["blocked"] is True and r["dismissed"] == 2
    feed = c.get(f"/api/feed/category/{cat['id']}", headers=h).json()
    assert [x["video"]["id"] for x in feed] == ["goodvid0000"]
    # prefilter rejects the channel for future discoveries
    from bt.agent import data as d
    with session() as s:
        cat_obj = s.get(Category, cat["id"])
        ctx = d.profile_ctx(s, profiles[0]["id"])
        assert d.prefilter({"id": "newvid000001", "duration_s": 600, "title": "New",
                            "channel_id": "UCbad"}, cat_obj, ctx, False) == "blocked/distrusted channel"
        assert d.prefilter({"id": "newvid000002", "duration_s": 600, "title": "New2",
                            "channel_id": "UCgood"}, cat_obj, ctx, False) is None


def test_proxy_provider_catalog():
    c = client()
    provs = c.get("/api/proxy/providers").json()["providers"]
    by_id = {p["id"]: p for p in provs}
    assert set(by_id) == {"pia", "local"}  # only providers whose hostnames actually resolve
    assert by_id["pia"]["scheme"] == "socks5" and by_id["pia"]["port"] == 1080
    assert by_id["pia"]["host"] == "proxy-nl.privateinternetaccess.com"
    assert "http" in by_id["local"]["schemes"]


def test_proxy_test_rejects_bad_urls():
    c = client()
    assert c.post("/api/proxy/test", json={"url": "proxy-nl.example.com:1080"}).status_code == 400
    assert c.post("/api/proxy/test", json={"url": "ftp://x:1"}).status_code == 400


def test_proxy_test_unreachable_proxy_reports_error():
    c = client()
    res = c.post("/api/proxy/test", json={"url": "socks5://u:p@127.0.0.1:9"}).json()
    assert res["ok"] is False and res["error"]
    assert "reach the proxy" in res["hint"]


def test_cookies_test_without_cookies():
    from bt.config import COOKIES_PATH
    COOKIES_PATH.unlink(missing_ok=True)
    c = client()
    assert c.post("/api/cookies/test").json() == {"present": False, "logged_in": False}


def test_ytimport_requires_cookies_and_valid_body(tmp_path):
    c = client()
    assert c.post("/api/ytimport/subscriptions").status_code == 400
    assert c.post("/api/ytimport/feed", json={"category_id": 1, "source": "recommended"}).status_code == 400
    # a dummy cookies file passes the presence check; the feed body still validates
    from bt.config import COOKIES_PATH
    COOKIES_PATH.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tk\tv")
    try:
        profiles = c.get("/api/profiles").json()
        h = {"X-Profile-Id": str(profiles[0]["id"])}
        cat = c.post("/api/categories", json={"name": "Import", "brief": "test"}, headers=h).json()
        assert c.post("/api/ytimport/feed", json={"category_id": cat["id"], "source": "nope"},
                      headers=h).status_code == 400
        assert c.post("/api/ytimport/feed", json={"category_id": cat["id"], "source": "playlist",
                                                 "playlist_url": "https://example.com/x"},
                      headers=h).status_code == 400
        assert c.post("/api/ytimport/feed", json={"category_id": 999999, "source": "liked"},
                      headers=h).status_code == 404
        # a category of another profile is invisible
        other = c.post("/api/profiles", json={"name": "Imp2", "emoji": "🙂"}).json()
        assert c.post("/api/ytimport/feed", json={"category_id": cat["id"], "source": "liked"},
                      headers={"X-Profile-Id": str(other["id"])}).status_code == 404
        # Imported starting picks bypass filtering and AI judging.
        from bt.api import ytimport
        entries = [{"id": "goodvid0001", "title": "Good", "channel_id": "UCgood", "duration": 600},
                   {"id": "shortvid001", "title": "Short", "channel_id": "UCgood", "duration": 30},
                   {"id": "livevid0001", "title": "Live", "channel_id": "UCgood", "duration": 600,
                    "is_live": True}]
        monkeypatched = entries

        def fake_flat(url, kind, limit):
            return monkeypatched

        orig_flat = ytimport._flat_entries
        ytimport._flat_entries = fake_flat
        try:
            res = c.post("/api/ytimport/feed", json={"category_id": cat["id"], "source": "liked", "limit": 10},
                         headers=h).json()
        finally:
            ytimport._flat_entries = orig_flat
        assert res["found"] == 3 and res["added"] == 3 and res["skipped"] == 0
        assert res["skip_reasons"] == {}
        from sqlmodel import select as _sel
        with session() as s:
            rows = s.exec(_sel(Candidate).where(Candidate.category_id == cat["id"])).all()
            assert {(r.video_id, r.status, r.source) for r in rows} == {
                (e["id"], "accepted", "import:liked") for e in entries
            }
    finally:
        COOKIES_PATH.unlink(missing_ok=True)


def test_settings_and_agent():
    c = client()
    st = c.get("/api/settings").json()
    assert st["throttle"]["max_per_hour"] == 120
    c.put("/api/settings/llm", json={"api_key": "secret"})
    assert c.get("/api/settings").json()["llm"]["api_key"] == "********"
    c.put("/api/settings/llm", json={"api_key": "********", "model": "x"})
    from bt.db import get_settings
    assert get_settings()["llm"]["api_key"] == "secret"
    # proxy with credentials is masked the same way; without credentials it stays visible
    c.put("/api/settings/throttle", json={"proxy": "socks5://u:p@proxy-nl.privateinternetaccess.com:1080"})
    assert c.get("/api/settings").json()["throttle"]["proxy"] == "********"
    c.put("/api/settings/throttle", json={"proxy": "********", "max_per_hour": 99})
    assert get_settings()["throttle"]["proxy"] == "socks5://u:p@proxy-nl.privateinternetaccess.com:1080"
    assert c.get("/api/settings").json()["throttle"]["max_per_hour"] == 99
    c.put("/api/settings/throttle", json={"proxy": "http://gluetun:8888"})
    assert c.get("/api/settings").json()["throttle"]["proxy"] == "http://gluetun:8888"
    c.put("/api/settings/throttle", json={"proxy": "", "max_per_hour": 120})

    assert c.get("/api/settings").json()["appearance"]["theme"] == "projector"
    assert c.put("/api/settings/appearance", json={"theme": "forest"}).json()["theme"] == "forest"
    assert c.get("/api/settings").json()["appearance"]["theme"] == "forest"
    assert c.put("/api/settings/appearance", json={"theme": "does-not-exist"}).status_code == 400
    assert c.put("/api/settings/appearance", json={"theme": []}).status_code == 400
    assert c.put("/api/settings/appearance", json={"theme": "projector"}).status_code == 200

    assert c.post("/api/agent/pause").json()["paused"] is True
    assert c.get("/api/agent/status").json()["control"]["paused"] is True
    assert c.post("/api/agent/resume").json()["paused"] is False
    emit("search", "hello")
    assert c.get("/api/agent/events").json()[-1]["message"] == "hello"


def test_stats_all_profiles_keep_watch_history_separate():
    c = client()
    owners = [c.post('/api/profiles', json={'name': name}).json()
              for name in ('Stats global A', 'Stats global B')]
    cats = [c.post('/api/categories', json={'name': 'Shared interest'},
                   headers={'X-Profile-Id': str(p['id'])}).json() for p in owners]
    with session() as s:
        s.add(Video(id='globalstat1', title='Shared video', duration_s=600))
        s.add(Video(id='globalstat2', title='Second profile rejection', duration_s=300))
        for cat in cats:
            s.add(Candidate(category_id=cat['id'], video_id='globalstat1', status='accepted',
                            score=80, judged_at=now()))
        s.add(Candidate(category_id=cats[1]['id'], video_id='globalstat2', status='rejected', judged_at=now()))
        s.add(WatchHistory(profile_id=owners[0]['id'], video_id='globalstat1', category_id=cats[0]['id'],
                           finished=True, duration_s=600))
        s.add(Feedback(profile_id=owners[1]['id'], category_id=cats[1]['id'], video_id='globalstat2', rating=-1))
        s.commit()
    results = [c.get('/api/stats/page?days=7', headers={'X-Profile-Id': str(p['id'])}).json() for p in owners]
    assert results[0] == results[1]
    rows = {r['id']: r for r in results[0]['categories']}
    first, second = (rows[cat['id']] for cat in cats)
    assert first['profile_name'] == owners[0]['name'] and first['profile_id'] == owners[0]['id']
    assert second['profile_name'] == owners[1]['name']
    assert first['counts']['in_queue'] == 0 and first['counts']['watched'] == 1
    assert second['counts']['in_queue'] == 1 and second['counts']['watched'] == 0
    assert second['counts']['rejected_family'] == 1
    assert results[0]['overview']['candidates']['in_queue'] == sum(r['counts']['in_queue'] for r in rows.values())
    assert results[0]['daily'][-1]['watched_by_category'][str(cats[0]['id'])] == 1
    assert results[0] == c.get('/api/stats/page?days=7').json()


def test_continue_watching_seen_and_boring_persist_across_reload_and_late_progress():
    c = client()
    profile = c.post('/api/profiles', json={'name': 'Continue persistence'}).json()
    other = c.post('/api/profiles', json={'name': 'Other continue'}).json()
    headers = {'X-Profile-Id': str(profile['id'])}
    other_headers = {'X-Profile-Id': str(other['id'])}
    cat = c.post('/api/categories', json={'name': 'Continue playlist'}, headers=headers).json()
    ids = ['contseen001', 'contbore001', 'contkeep001']
    with session() as s:
        for vid in ids:
            s.add(Video(id=vid, title=vid, duration_s=600))
            s.add(Candidate(category_id=cat['id'], video_id=vid, status='accepted'))
        s.commit()
    for vid in ids:
        assert c.post('/api/history', json={'video_id': vid, 'position_s': 120, 'duration_s': 600,
                      'category_id': cat['id']}, headers=headers).status_code == 200
    c.post('/api/history', json={'video_id': ids[1], 'position_s': 80, 'duration_s': 600}, headers=other_headers)
    def continuing(h):
        rows = c.get('/api/feed/home', headers=h).json()
        return [item['video']['id'] for row in rows if row['kind'] == 'continue' for item in row['items']]
    assert set(continuing(headers)) == set(ids)
    assert c.post('/api/history/seen', json={'video_id': ids[0]}, headers=headers).status_code == 200
    assert c.post('/api/feedback', json={'video_id': ids[1], 'category_id': cat['id'],
                  'rating': -1, 'chips': ['Boring']}, headers=headers).status_code == 200
    # A late progress beacon from the previous player must not reverse Seen it.
    c.post('/api/history', json={'video_id': ids[0], 'position_s': 130, 'duration_s': 600}, headers=headers)
    assert c.get('/api/history/' + ids[0], headers=headers).json()['finished'] is True
    assert continuing(headers) == [ids[2]]
    assert continuing(headers) == [ids[2]]  # a fresh request behaves like a reload
    assert continuing(other_headers) == [ids[1]]
    # Negative feedback is preserved as feedback, not falsely recorded as watched.
    assert c.get('/api/history/' + ids[1], headers=headers).json()['finished'] is False
    # A later explicit positive correction can put the unfinished video back.
    c.post('/api/feedback', json={'video_id': ids[1], 'rating': 1, 'text': 'Actually enjoyed it'}, headers=headers)
    assert ids[1] in continuing(headers)


def test_a_card_reports_a_queued_download_not_just_a_finished_one():
    """The card menu must tell "not saved" from "queued"/"downloading", not only "done"."""
    c = client()
    profile = c.post("/api/profiles", json={"name": "Card owner"}).json()
    h = {"X-Profile-Id": str(profile["id"])}
    cat = c.post("/api/categories", json={"name": "Science", "brief": "fun"}, headers=h).json()
    with session() as s:
        s.add(Video(id="cardvid0001", title="Queued thing", channel_id="UC1", duration_s=600))
        s.add(Candidate(category_id=cat["id"], video_id="cardvid0001", status="accepted", score=90))
        s.add(Download(video_id="cardvid0001", status="queued"))
        s.commit()
    card = c.get(f"/api/feed/category/{cat['id']}", headers=h).json()[0]["video"]
    assert card["offline"] is False and card["download_status"] == "queued"
    with session() as s:
        s.get(Download, "cardvid0001").status = "done"
        s.commit()
    card = c.get(f"/api/feed/category/{cat['id']}", headers=h).json()[0]["video"]
    assert card["offline"] is True and card["download_status"] == "done"


def test_shorts_feed_returns_only_accepted_unwatched_shorts(tmp_path, monkeypatch):
    """The swipe feed is the playlist queue filtered to shorts, and a dislike removes one."""
    from bt.downloader import service
    monkeypatch.setattr(service, "MEDIA_DIR", tmp_path)
    c = client()
    profile = c.post("/api/profiles", json={"name": "Swiper"}).json()
    h = {"X-Profile-Id": str(profile["id"])}
    cat = c.post("/api/categories", json={"name": "Clips", "kind": "shorts", "brief": "short clips"}, headers=h).json()
    with session() as s:
        s.add(Video(id="short000001", title="A short", channel_id="UC1", duration_s=30, is_short=True))
        s.add(Video(id="long0000001", title="A long one", channel_id="UC1", duration_s=900, is_short=False))
        s.add(Video(id="short000002", title="Watched short", channel_id="UC1", duration_s=20, is_short=True))
        s.add(Video(id="short000003", title="Dismissed short", channel_id="UC1", duration_s=25, is_short=True))
        s.add(Candidate(category_id=cat["id"], video_id="short000001", status="accepted", score=90))
        s.add(Candidate(category_id=cat["id"], video_id="long0000001", status="accepted", score=95))
        s.add(Candidate(category_id=cat["id"], video_id="short000002", status="accepted", score=99))
        s.add(Candidate(category_id=cat["id"], video_id="short000003", status="accepted", score=99, dismissed=True))
        s.add(WatchHistory(profile_id=profile["id"], video_id="short000002", finished=True))
        s.commit()
    (tmp_path / "short000001.mp4").write_bytes(b"local movie")
    with session() as s:
        s.add(Download(video_id="short000001", status="done", path="short000001.mp4"))
        s.commit()
    got = c.get("/api/shorts", params={"category_id": cat["id"]}, headers=h).json()
    # Not the long video, not the watched one, not the dismissed one.
    assert [i["video"]["id"] for i in got["items"]] == ["short000001"]
    assert got["items"][0]["video"]["is_short"] is True
    # A dislike goes through the normal feedback path, so the curator learns from it.
    assert c.post("/api/feedback", json={"video_id": "short000001", "category_id": cat["id"], "rating": -1},
                  headers=h).status_code == 200
    assert c.get("/api/shorts", params={"category_id": cat["id"]}, headers=h).json()["items"] == []
    # Another profile sees none of it.
    other = c.post("/api/profiles", json={"name": "Other"}).json()
    assert c.get("/api/shorts", params={"category_id": cat["id"]}, headers={"X-Profile-Id": str(other["id"])}).status_code == 404


def test_shorts_page_deduplicates_before_limit_and_excludes_loaded_videos(tmp_path, monkeypatch):
    from bt.downloader import service
    monkeypatch.setattr(service, "MEDIA_DIR", tmp_path)
    c = client()
    profile = c.post('/api/profiles', json={'name': 'Paged swiper'}).json()
    h = {'X-Profile-Id': str(profile['id'])}
    cats = [c.post('/api/categories', json={'name': f'Clips {n}', 'kind': 'shorts', 'brief': 'clips'}, headers=h).json()['id']
            for n in range(5)]
    ids = [f'page{i:07d}' for i in range(25)]
    with session() as s:
        for i, vid in enumerate(ids):
            s.add(Video(id=vid, title=vid, duration_s=30, is_short=True))
            (tmp_path / f"{vid}.mp4").write_bytes(b"local")
            s.add(Download(video_id=vid, status="done", path=f"{vid}.mp4"))
            for cid in cats:
                s.add(Candidate(category_id=cid, video_id=vid, status='accepted', score=100-i))
        s.commit()
    first = c.get('/api/shorts', params={'limit': 10, 'category_id': cats[0]}, headers=h).json()
    assert [x['video']['id'] for x in first['items']] == ids[:10]
    assert first['total'] == 25 and first['next_offset'] == 10
    with session() as s:
        for vid in ids[:7]:
            s.add(WatchHistory(profile_id=profile['id'], video_id=vid, finished=True))
        s.commit()
    second = c.get('/api/shorts', params={'limit': 10, 'category_id': cats[0], 'exclude_ids': ','.join(ids[:10])}, headers=h).json()
    assert [x['video']['id'] for x in second['items']] == ids[10:20]
    assert second['next_offset'] == 10
    last = c.get('/api/shorts', params={'limit': 10, 'category_id': cats[0], 'exclude_ids': ','.join(ids[:20])}, headers=h).json()
    assert [x['video']['id'] for x in last['items']] == ids[20:]
    assert last['next_offset'] is None
    assert c.get('/api/shorts', params={'category_id': cats[0], 'exclude_ids': 'bad'}, headers=h).status_code == 400
    assert c.get('/api/shorts', params={'category_id': cats[0]}, headers={'X-Profile-Id': '1'}).status_code == 404


def test_autofill_brief_writes_a_brief_from_the_name(monkeypatch):
    from bt import llm
    seen = []

    def fake(messages, **kw):
        seen.append((messages, kw))
        return {"brief": "  Simple, fun science for kids 5-9: experiments, space, animals.  "}

    monkeypatch.setattr(llm, "chat_json", fake)
    c = client()
    r = c.post("/api/categories/autofill-brief", json={"name": "Science for kids"})
    assert r.status_code == 200
    assert r.json()["brief"] == "Simple, fun science for kids 5-9: experiments, space, animals."
    assert "Science for kids" in seen[0][0][-1]["content"]
    assert seen[0][1]["role"] == "worker"
    assert seen[0][1]["schema_name"] == "category_brief"
    # The brief must not be pushed towards a child audience unless the name asks for one.
    system = seen[0][0][0]["content"]
    assert "Do not assume a young audience" in system
    assert "young children watching together" not in system


def test_autofill_brief_requires_a_name_and_rejects_empty_or_failed_output(monkeypatch):
    from bt import llm
    c = client()
    assert c.post("/api/categories/autofill-brief", json={"name": "   "}).status_code == 400
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"brief": "   "})
    assert c.post("/api/categories/autofill-brief", json={"name": "Cats"}).status_code == 502

    def boom(*a, **kw):
        raise llm.LlmError("no model")

    monkeypatch.setattr(llm, "chat_json", boom)
    assert c.post("/api/categories/autofill-brief", json={"name": "Cats"}).status_code == 502
