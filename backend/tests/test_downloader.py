"""Offline cache downloader: queue, eviction, reconcile, run_one, gating, API (no network unless BT_LIVE=1)."""
import os
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import delete, select

from bt import ytdl
from bt.config import MEDIA_DIR
from bt.db import get_kv, init_db, save_setting, session, set_agent_state, update_agent_control
from bt.downloader import service as svc
from bt.downloader import transcode
from bt.models import Candidate, Category, Download, Profile, ReencodeJob, ReencodeResult, Setting, Video, WatchHistory, YtRequest, now
from bt.music.models import MusicFavorite, MusicMix, MusicMixTrack, MusicPlay

IDS = [f"vid{i:08d}" for i in range(10)]  # 11 chars


@pytest.fixture(autouse=True)
def clean():
    init_db()
    with session() as s:
        for m in (ReencodeResult, ReencodeJob, Download, Candidate, WatchHistory, Category, YtRequest,
                  MusicFavorite, MusicMix, MusicMixTrack, MusicPlay):
            s.exec(delete(m))
        s.exec(delete(Setting).where(Setting.key.startswith('music_for_you:')))
        s.commit()
    save_setting("cache", {"enabled": True, "max_gb": 1, "max_height": 720, "rate_limit": 0, "min_gap_s": 0,
                           "auto": "accepted"})
    save_setting("agent", {"enabled": True})
    update_agent_control(paused=False)
    set_agent_state(state="idle", backoff_until=None)
    import shutil
    shutil.rmtree(MEDIA_DIR, ignore_errors=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    update_agent_control(paused=False)


def _cat(s, name="c"):
    pid = s.exec(select(Profile)).first().id
    c = Category(profile_id=pid, name=name)
    s.add(c)
    s.commit()
    s.refresh(c)
    return c


def _cand(s, cat, vid, score=80, status="accepted", dismissed=False, dur=10):
    if not s.get(Video, vid):
        s.add(Video(id=vid, title=f"T {vid}", duration_s=dur))
    s.add(Candidate(category_id=cat.id, video_id=vid, status=status, score=score, dismissed=dismissed))
    s.commit()


def _done(s, vid, size, pinned=False, played_ago=None):
    p = MEDIA_DIR / svc.rel_path(vid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 10)
    d = Download(video_id=vid, status="done", path=svc.rel_path(vid), size_bytes=size, pinned=pinned,
                 finished_at=now() - timedelta(days=10),
                 last_played_at=(now() - timedelta(hours=played_ago)) if played_ago is not None else None)
    s.add(d)
    s.commit()
    return d


def test_existing_reencode_queue_and_preserve_playability(monkeypatch):
    from bt.api.downloads import queue_reencode
    with session() as s:
        _done(s, IDS[0], 10)
        other = _done(s, IDS[1], 10)
        other.vcodec = "h264 (NVENC)"
        s.add(other)
        s.commit()
        assert queue_reencode(s) == {"queued": 1}
        assert queue_reencode(s) == {"queued": 0}
        assert s.get(Download, IDS[0]).status == "done"
    def encode(src, dst, **kwargs):
        assert src.read_bytes() == b"x" * 10
        dst.write_bytes(b"small")
        return "ok", ""
    monkeypatch.setattr(transcode, "reencode", encode)
    svc.Downloader().reencode_existing(IDS[0])
    with session() as s:
        row = s.get(Download, IDS[0])
        assert row.status == "done" and row.size_bytes == 5 and row.vcodec == "h264 (NVENC)"
        assert s.get(ReencodeJob, IDS[0]) is None
        result = s.exec(select(ReencodeResult)).one()
        assert (result.outcome, result.original_bytes, result.output_bytes) == ("saved", 10, 5)
    assert (MEDIA_DIR / svc.rel_path(IDS[0])).read_bytes() == b"small"


def test_existing_reencode_keeps_original_when_larger_or_failed(monkeypatch):
    with session() as s:
        _done(s, IDS[0], 10)
        s.add(ReencodeJob(video_id=IDS[0]))
        s.commit()
    def encode(src, dst, **kwargs):
        dst.write_bytes(b"x" * 20)
        return "ok", ""
    monkeypatch.setattr(transcode, "reencode", encode)
    svc.Downloader().reencode_existing(IDS[0])
    with session() as s:
        assert s.get(ReencodeJob, IDS[0]).status == "error"
        assert s.get(Download, IDS[0]).size_bytes == 10
        result = s.exec(select(ReencodeResult)).one()
        assert (result.outcome, result.original_bytes, result.output_bytes) == ("not_smaller", 10, 20)
    assert (MEDIA_DIR / svc.rel_path(IDS[0])).read_bytes() == b"x" * 10


def test_existing_reencode_delete_during_encode_does_not_restore_file(monkeypatch):
    from bt.api.downloads import delete_download
    with session() as s:
        _done(s, IDS[0], 10)
        s.add(ReencodeJob(video_id=IDS[0]))
        s.commit()
    def encode(src, dst, **kwargs):
        dst.write_bytes(b"small")
        with session() as s:
            delete_download(IDS[0], s)
        return "ok", ""
    monkeypatch.setattr(transcode, "reencode", encode)
    svc.Downloader().reencode_existing(IDS[0])
    with session() as s:
        assert s.get(Download, IDS[0]).status == "evicted"
        assert s.get(ReencodeJob, IDS[0]) is None
    assert not (MEDIA_DIR / svc.rel_path(IDS[0])).exists()


def test_reencode_stats_distinguish_current_jobs_from_recorded_attempts():
    from bt.api.core import stats_page
    with session() as s:
        row = _done(s, IDS[0], 5)
        row.vcodec = "h264 (NVENC)"
        s.add(row)
        s.add(ReencodeJob(video_id=IDS[1]))
        s.add(ReencodeJob(video_id=IDS[2], status="error", error="Output was not smaller; original kept"))
        s.add(ReencodeResult(video_id=IDS[0], outcome="saved", original_bytes=10, output_bytes=5))
        s.add(ReencodeResult(video_id=IDS[2], outcome="not_smaller", original_bytes=10, output_bytes=12))
        s.commit()
        stats = stats_page(7, s)["reencode"]
        assert (stats["queued"], stats["encoded_copies"], stats["kept_not_smaller"]) == (1, 1, 1)
        assert (stats["recorded_saved"], stats["recorded_not_smaller"], stats["saved_bytes"]) == (1, 1, 5)


def test_sync_queue_and_next_round_robin():
    with session() as s:
        a, b = _cat(s, "a"), _cat(s, "b")
        _cand(s, a, IDS[0], 90)
        _cand(s, a, IDS[1], 95)
        _cand(s, b, IDS[2], 50)
        _cand(s, a, IDS[3], 99, dismissed=True)
        _cand(s, b, IDS[4], 99, status="rejected")
        s.add(Download(video_id=IDS[5], status="evicted"))
        _cand(s, b, IDS[5], 99)
        assert svc.sync_queue(s, {"auto": "accepted"}) == 3
        assert svc.sync_queue(s, {"auto": "accepted"}) == 0
        queued = {d.video_id for d in s.exec(select(Download).where(Download.status == "queued")).all()}
        assert queued == {IDS[0], IDS[1], IDS[2]}
        d, cat = svc.next_download(s, None)
        assert (d.video_id, cat) == (IDS[1], a.id)  # best score in first category
        d, cat = svc.next_download(s, cat)
        assert (d.video_id, cat) == (IDS[2], b.id)  # round-robin
        s.add(Download(video_id=IDS[6], status="queued", pinned=True))
        s.commit()
        assert svc.next_download(s, cat)[0].video_id == IDS[6]  # manual pin first


def test_retry_backoff_and_max_attempts():
    t = now()
    d = Download(video_id=IDS[0], status="error", attempts=1, finished_at=t - timedelta(seconds=30))
    assert not svc._retry_ready(d, t)
    d.finished_at = t - timedelta(seconds=svc.RETRY_BASE_S + 1)
    assert svc._retry_ready(d, t)
    d.attempts = svc.MAX_ATTEMPTS
    assert not svc._retry_ready(d, t)


def test_purge_unwanted_rejected_and_watched():
    """Rejected by Family/Judge (no accepted candidate left) and finished-watched copies are dropped;
    a pinned save or a copy that may be streaming right now is kept."""
    with session() as s:
        a, b = _cat(s, "a"), _cat(s, "b")
        _cand(s, a, IDS[0], 90, status="rejected")  # judge rejected the only candidate
        _done(s, IDS[0], 100)
        _cand(s, a, IDS[1], 90)  # accepted in one category...
        _cand(s, b, IDS[1], 80, status="rejected")  # ...rejected in another: still wanted, keep
        _done(s, IDS[1], 100)
        _cand(s, a, IDS[2])  # watched to the end: drop
        _done(s, IDS[2], 100)
        s.add(WatchHistory(profile_id=a.profile_id, video_id=IDS[2], finished=True))
        _cand(s, a, IDS[3])  # watched, but explicitly pinned: keep
        _done(s, IDS[3], 100, pinned=True)
        s.add(WatchHistory(profile_id=a.profile_id, video_id=IDS[3], finished=True))
        _cand(s, a, IDS[4])  # watched, but maybe streaming right now: keep for the next sweep
        _done(s, IDS[4], 100, played_ago=0.001)
        s.add(WatchHistory(profile_id=a.profile_id, video_id=IDS[4], finished=True))
        s.add(Download(video_id=IDS[5], status="queued"))  # queued, then rejected + watched: drop
        s.add(Candidate(category_id=a.id, video_id=IDS[5], status="rejected"))
        s.add(WatchHistory(profile_id=a.profile_id, video_id=IDS[5], finished=True))
        s.commit()
        assert svc.purge_unwanted(s) == 200  # two done copies (the queued row's file never existed)
        assert svc.purge_unwanted(s) == 0  # idempotent
    with session() as s:
        st = {d.video_id: d.status for d in s.exec(select(Download)).all()}
        assert st[IDS[0]] == st[IDS[2]] == st[IDS[5]] == "evicted"
        assert st[IDS[1]] == st[IDS[3]] == st[IDS[4]] == "done"
    for vid in (IDS[0], IDS[2], IDS[5]):
        assert not (MEDIA_DIR / svc.rel_path(vid)).exists()
    for vid in (IDS[1], IDS[3], IDS[4]):
        assert (MEDIA_DIR / svc.rel_path(vid)).exists()
    # once the playing stops, the sweep removes the watched copy
    with session() as s:
        d = s.get(Download, IDS[4])
        d.last_played_at = now() - timedelta(seconds=svc.ACTIVE_PLAY_S + 1)
        s.add(d)
        s.commit()
        assert svc.purge_unwanted(s) == 100


def test_sync_queue_skips_watched():
    with session() as s:
        c = _cat(s)
        _cand(s, c, IDS[0])  # watched by some profile: never downloaded
        _cand(s, c, IDS[1])  # only started watching: queued
        s.add(WatchHistory(profile_id=c.profile_id, video_id=IDS[0], finished=True))
        s.add(WatchHistory(profile_id=c.profile_id, video_id=IDS[1], finished=False))
        s.commit()
        assert svc.sync_queue(s, {"auto": "accepted"}) == 1
        assert {d.video_id for d in s.exec(select(Download).where(Download.status == "queued")).all()} == {IDS[1]}


def test_family_reject_and_watch_finish_cancel_downloads():
    """API hooks: a dislike flips the candidate and removes the queued copy immediately (aborting a
    running download), finishing a video removes its saved copy; pinned saves survive a dislike."""
    from fastapi.testclient import TestClient

    from bt.main import app
    with TestClient(app) as c:
        cat = c.post("/api/categories", json={"name": "drop rule", "brief": "b"}).json()
        cat_id = cat["id"]
        with session() as s:
            _cand(s, s.get(Category, cat_id), IDS[0], 90)  # queued, then disliked
            _cand(s, s.get(Category, cat_id), IDS[1], 90)  # saved, watched to the end
            _cand(s, s.get(Category, cat_id), IDS[2], 90)  # saved and pinned: dislike keeps it
            _cand(s, s.get(Category, cat_id), IDS[3], 90)  # saved, "Seen it" without playing
            s.add(Download(video_id=IDS[0], status="queued"))
            _done(s, IDS[1], 100)
            _done(s, IDS[2], 100, pinned=True)
            _done(s, IDS[3], 100)
            s.commit()
        c.post("/api/feedback", json={"video_id": IDS[0], "category_id": cat_id, "rating": -1,
                                      "chips": ["Too basic"]})
        with session() as s:
            assert s.get(Download, IDS[0]).status == "evicted"
            assert not (MEDIA_DIR / svc.rel_path(IDS[0])).exists()
            cand = s.exec(select(Candidate).where(Candidate.video_id == IDS[0])).one()
            assert cand.status == "rejected" and cand.dismissed
            assert any(f.startswith("Family:") for f in (cand.red_flags or []))
        assert c.post("/api/history", json={"video_id": IDS[1], "position_s": 9.3, "duration_s": 10}).json()["finished"]
        with session() as s:
            assert s.get(Download, IDS[1]).status == "evicted"  # 92 % auto-finish freed the copy
            assert not (MEDIA_DIR / svc.rel_path(IDS[1])).exists()
        assert not c.post("/api/history", json={"video_id": IDS[3], "position_s": 4, "duration_s": 10}).json()["finished"]
        with session() as s:
            assert s.get(Download, IDS[3]).status == "done"
        c.post("/api/feedback", json={"video_id": IDS[2], "category_id": cat_id, "rating": -1})
        with session() as s:
            assert s.get(Download, IDS[2]).status == "done"  # pinned save survives the dislike
        assert c.post("/api/history/seen", json={"video_id": IDS[3]}).json()["ok"]
        with session() as s:
            assert s.get(Download, IDS[3]).status == "evicted"  # "Seen it" removed the copy
            assert not (MEDIA_DIR / svc.rel_path(IDS[3])).exists()


def test_eviction_order_and_make_room():
    with session() as s:
        c = _cat(s)
        pid = c.profile_id
        _done(s, IDS[0], 400_000_000, played_ago=1)  # wanted, recently played
        _cand(s, c, IDS[0])
        _done(s, IDS[1], 400_000_000, played_ago=50)  # wanted, played long ago
        _cand(s, c, IDS[1])
        _done(s, IDS[2], 300_000_000)  # dismissed -> unwanted
        _cand(s, c, IDS[2], dismissed=True)
        _done(s, IDS[3], 100_000_000)  # finished by its only viewer (even though still accepted)
        _cand(s, c, IDS[3])
        s.add(WatchHistory(profile_id=pid, video_id=IDS[3], finished=True))
        _done(s, IDS[4], 900_000_000, pinned=True)
        s.commit()
        order = [d.video_id for d in svc.eviction_order(s, allow_wanted=True)]
        assert order == [IDS[3], IDS[2], IDS[1], IDS[0]]
        assert [d.video_id for d in svc.eviction_order(s, allow_wanted=False)] == [IDS[3], IDS[2]]
        # cap 1 GB, used 2.1 GB: unwanted ones go, wanted+pinned can't -> False for auto downloads
        assert svc.make_room(s, {"max_gb": 1}, 0, allow_wanted=False) is False
        st = {d.video_id: d.status for d in s.exec(select(Download)).all()}
        assert st[IDS[3]] == st[IDS[2]] == "evicted" and st[IDS[1]] == "done"
        assert not (MEDIA_DIR / svc.rel_path(IDS[3])).exists()
        assert svc.make_room(s, {"max_gb": 1.5}, 0, allow_wanted=True) is True  # pinned request may evict wanted
        assert s.get(Download, IDS[1]).status == "evicted" and s.get(Download, IDS[4]).status == "done"


def test_reconcile():
    with session() as s:
        s.add(Download(video_id=IDS[0], status="done", path=svc.rel_path(IDS[0]), size_bytes=5))  # file missing
        s.add(Download(video_id=IDS[1], status="downloading"))
        s.add(Download(video_id=IDS[2], status="evicted"))
        s.commit()
    for vid in (IDS[2], IDS[3]):  # IDS[2]: evicted but file survived, IDS[3]: orphan -> adopt
        p = MEDIA_DIR / svc.rel_path(vid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"abc")
    (MEDIA_DIR / "zz").mkdir()
    (MEDIA_DIR / "zz" / "junk.mp4").write_bytes(b"x")
    (MEDIA_DIR / ".tmp" / IDS[7]).mkdir(parents=True)  # staging without an active row
    (MEDIA_DIR / ".tmp" / IDS[1]).mkdir(parents=True)  # staging of a resumable download: keep
    with session() as s:
        st = svc.reconcile(s)
        assert st == {"missing": 1, "adopted": 1, "deleted": 2, "requeued": 1, "staging_removed": 1}
        assert s.get(Download, IDS[0]).status == "evicted"
        assert s.get(Download, IDS[1]).status == "queued"
        d = s.get(Download, IDS[3])
        assert d.status == "done" and d.size_bytes == 3
    assert not (MEDIA_DIR / svc.rel_path(IDS[2])).exists()
    assert (MEDIA_DIR / ".tmp" / IDS[1]).exists() and not (MEDIA_DIR / ".tmp" / IDS[7]).exists()


def _fake_download(content=b"\0" * 1000, hook_calls=1, exc=None):
    def fake(vid, workdir, max_height, rate_limit, hook=None, origin="worker"):
        for i in range(hook_calls):
            hook({"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000, "speed": 1e6, "eta": 1,
                  "info_dict": {"vcodec": "avc1", "acodec": "none"}})
        if exc:
            raise exc
        p = Path(workdir) / f"{vid}.mp4"
        p.write_bytes(content)
        return {"path": str(p), "height": 720, "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2", "title": "T"}
    return fake


def _queue(vid, **kw):
    with session() as s:
        if not s.get(Video, vid):
            s.add(Video(id=vid, title="Title", duration_s=10))
        s.add(Download(video_id=vid, status="queued", **kw))
        s.commit()
        return s.get(Download, vid)


def test_run_one_success_atomic(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download())
    d = _queue(IDS[0], pinned=True)
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.run_one(d, {"max_gb": 1, "max_height": 720}) == "done"
    with session() as s:
        row = s.get(Download, IDS[0])
        assert (row.status, row.height, row.size_bytes, row.progress) == ("done", 720, 1000, 1.0)
    assert (MEDIA_DIR / svc.rel_path(IDS[0])).stat().st_size == 1000
    assert not svc.staging_dir(IDS[0]).exists()
    from bt.db import get_kv
    assert get_kv("downloader_state")["state"] == "downloading"


def test_run_one_errors(monkeypatch):
    dl = svc.Downloader(sleep=lambda s: None)
    cfg = {"max_gb": 1}
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(exc=ytdl.YtUnavailable("This video is unavailable")))
    assert dl.run_one(_queue(IDS[0]), cfg) == "error"
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(exc=ytdl.YtError("ffmpeg exploded")))
    assert dl.run_one(_queue(IDS[1]), cfg) == "error"
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(exc=ytdl.YtBlocked("Sign in to confirm")))
    assert dl.run_one(_queue(IDS[2]), cfg) == "blocked"
    with session() as s:
        a, b, c = (s.get(Download, i) for i in IDS[:3])
        assert (a.status, a.attempts) == ("error", svc.MAX_ATTEMPTS)
        assert (b.status, b.attempts) == ("error", 1)
        assert (c.status, c.attempts) == ("queued", 0)
    assert dl.blocker()[0] == "backoff"


# ---- HW transcoding (NVENC) ----

def test_nvenc_cmd():
    cmd = transcode.nvenc_cmd(Path("in.mp4"), Path("out.mp4"))
    assert cmd[:2] == ["ffmpeg", "-hide_banner"]
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc" and cmd[-1] == "out.mp4"
    assert cmd[cmd.index("-progress") + 1] == "pipe:1" and "+faststart" in cmd


def _fake_reencode(status="ok", detail=""):
    calls = []

    def fake(src, dst, duration_s=0.0, on_progress=None, should_abort=None):
        calls.append((Path(src), Path(dst), duration_s, should_abort))
        if status == "ok":
            dst.write_bytes(b"\7" * 400)
        if on_progress and duration_s:
            on_progress(0.5)
        return status, detail
    return calls, fake


def test_run_one_hw_transcode_replaces_file(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(content=b"\0" * 1000))
    calls, fake = _fake_reencode()
    monkeypatch.setattr(svc.transcode, "reencode", fake)
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.run_one(_queue(IDS[0]), {"max_gb": 1, "hw_transcode": True}) == "done"
    src, dst, duration_s, should_abort = calls[0]
    assert dst.parent == svc.staging_dir(IDS[0]) and duration_s == 10 and should_abort is not None
    with session() as s:
        row = s.get(Download, IDS[0])
        assert (row.status, row.size_bytes) == ("done", 400)
        assert row.vcodec == "h264 (NVENC)"
        result = s.exec(select(ReencodeResult)).one()
        assert (result.source, result.outcome, result.original_bytes, result.output_bytes) == ("new", "saved", 1000, 400)
    assert (MEDIA_DIR / svc.rel_path(IDS[0])).read_bytes() == b"\7" * 400
    assert not svc.staging_dir(IDS[0]).exists()
    from bt.db import get_kv
    assert get_kv("downloader_state")["state"] == "transcoding"


def test_run_one_hw_transcode_keeps_smaller_original(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(content=b"x" * 100))
    _, fake = _fake_reencode()
    monkeypatch.setattr(svc.transcode, "reencode", fake)
    assert svc.Downloader().run_one(_queue(IDS[0]), {"max_gb": 1, "hw_transcode": True}) == "done"
    with session() as s:
        row = s.get(Download, IDS[0])
        assert row.size_bytes == 100 and row.vcodec == "avc1.4d401f"
    assert (MEDIA_DIR / svc.rel_path(IDS[0])).read_bytes() == b"x" * 100


def test_run_one_hw_transcode_unavailable_keeps_original_and_disarms(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(content=b"\0" * 1000))
    calls, fake = _fake_reencode(status="unavailable", detail="no capable devices found")
    monkeypatch.setattr(svc.transcode, "reencode", fake)
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.run_one(_queue(IDS[0]), {"max_gb": 1, "hw_transcode": True}) == "done"
    assert dl.run_one(_queue(IDS[1]), {"max_gb": 1, "hw_transcode": True}) == "done"
    assert len(calls) == 1  # the first NVENC-unavailable failure disarms further attempts
    with session() as s:
        a = s.get(Download, IDS[0])
        assert (a.status, a.size_bytes, a.vcodec) == ("done", 1000, "avc1.4d401f")
    assert (MEDIA_DIR / svc.rel_path(IDS[1])).read_bytes() == b"\0" * 1000


def test_run_one_hw_transcode_aborted_keeps_original(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download(content=b"\0" * 1000))
    _, fake = _fake_reencode(status="aborted", detail="stopped")
    monkeypatch.setattr(svc.transcode, "reencode", fake)
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.run_one(_queue(IDS[0]), {"max_gb": 1, "hw_transcode": True}) == "done"
    assert (MEDIA_DIR / svc.rel_path(IDS[0])).read_bytes() == b"\0" * 1000
    with session() as s:
        row = s.get(Download, IDS[0])
        assert (row.status, row.size_bytes, row.vcodec) == ("done", 1000, "avc1.4d401f")


def test_run_one_hw_transcode_off_by_default(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download())
    calls, fake = _fake_reencode()
    monkeypatch.setattr(svc.transcode, "reencode", fake)
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.run_one(_queue(IDS[0]), {"max_gb": 1}) == "done"
    assert calls == []


def test_reencode_plumbing_with_local_ffmpeg(monkeypatch, tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    src, dst = tmp_path / "s.mp4", tmp_path / "d.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=duration=2:size=160x120:rate=10", "-c:v", "libx264",
                    "-preset", "ultrafast", str(src)], check=True)
    monkeypatch.setattr(transcode, "nvenc_cmd",
                        lambda s, d: ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                                      "-i", str(s), "-c:v", "libx264", "-preset", "ultrafast",
                                      "-progress", "pipe:1", "-nostats", str(d)])
    seen = []
    status, detail = transcode.reencode(src, dst, duration_s=2.0, on_progress=seen.append)
    assert status == "ok" and detail == ""
    assert dst.stat().st_size > 0 and seen and seen == sorted(seen)
    status, _ = transcode.reencode(src, dst, duration_s=2.0, should_abort=lambda: "stop")
    assert status == "aborted"


def test_hook_aborts_on_pause_and_on_delete(monkeypatch):
    monkeypatch.setattr(svc, "CONTROL_EVERY_S", 0)
    monkeypatch.setattr(svc.ytdl, "download", _fake_download())
    update_agent_control(paused=True)
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.run_one(_queue(IDS[0]), {"max_gb": 1}) == "aborted"
    with session() as s:
        assert s.get(Download, IDS[0]).status == "queued"  # resumable later
    update_agent_control(paused=False)

    def deleting(vid, workdir, max_height, rate_limit, hook=None, **k):
        with session() as s:
            row = s.get(Download, vid)
            row.status = "evicted"
            s.add(row)
            s.commit()
        return _fake_download()(vid, workdir, max_height, rate_limit, hook)

    monkeypatch.setattr(svc.ytdl, "download", deleting)
    assert dl.run_one(_queue(IDS[1]), {"max_gb": 1}) == "aborted"
    with session() as s:
        assert s.get(Download, IDS[1]).status == "evicted"
    assert not (MEDIA_DIR / svc.rel_path(IDS[1])).exists()


def test_run_one_aborts_when_video_becomes_rejected_or_watched(monkeypatch):
    """A download in flight stops as soon as the video is rejected by Family/Judge or finished-watched;
    the periodic sweep then evicts the row."""
    monkeypatch.setattr(svc, "CONTROL_EVERY_S", 0)

    def fake(vid, workdir, max_height, rate_limit, hook=None, **k):
        with session() as s:
            if vid == IDS[0]:
                c = s.exec(select(Candidate).where(Candidate.video_id == vid)).first()
                c.status, c.dismissed = "rejected", True  # family dislikes it mid-download
                s.add(c)
            else:
                s.add(WatchHistory(profile_id=s.exec(select(Profile)).first().id, video_id=vid, finished=True))
            s.commit()
        return _fake_download()(vid, workdir, max_height, rate_limit, hook)

    monkeypatch.setattr(svc.ytdl, "download", fake)
    dl = svc.Downloader(sleep=lambda s: None)
    # queue both properly (candidate accepted, download queued)
    with session() as s:
        c = _cat(s)
        _cand(s, c, IDS[0])
        _cand(s, c, IDS[1])
        s.add(Download(video_id=IDS[0], status="queued"))
        s.add(Download(video_id=IDS[1], status="queued"))
        s.commit()
    with session() as s:
        d0, d1 = s.get(Download, IDS[0]), s.get(Download, IDS[1])
        assert dl.run_one(d0, {"max_gb": 1}) == "aborted"  # rejected mid-download
        assert dl.run_one(d1, {"max_gb": 1}) == "aborted"  # watched mid-download
        assert s.get(Download, IDS[0]).status == s.get(Download, IDS[1]).status == "queued"
    assert svc.Downloader(sleep=lambda s: None).iterate() == 30  # sweep evicted both, nothing else to do
    with session() as s:
        assert s.get(Download, IDS[0]).status == s.get(Download, IDS[1]).status == "evicted"
        assert not (MEDIA_DIR / svc.rel_path(IDS[0])).exists()
    assert not (MEDIA_DIR / svc.rel_path(IDS[1])).exists()


def test_blocker_gates():
    dl = svc.Downloader(sleep=lambda s: None)
    assert dl.blocker() is None
    update_agent_control(paused=True)
    assert dl.blocker()[0] == "paused"
    update_agent_control(paused=False)
    set_agent_state(state="backoff", backoff_until=(now() + timedelta(minutes=5)).isoformat())
    assert dl.blocker()[0] == "backoff"
    set_agent_state(state="idle", backoff_until=None)
    save_setting("cache", {"enabled": False})
    assert dl.blocker()[0] == "disabled"
    save_setting("cache", {"enabled": True})
    save_setting("agent", {"enabled": False})
    assert dl.blocker()[0] == "disabled"


def test_iterate_end_to_end(monkeypatch):
    monkeypatch.setattr(svc.ytdl, "download", _fake_download())
    with session() as s:
        _cand(s, _cat(s), IDS[0])
    dl = svc.Downloader(sleep=lambda s: None)
    dl.iterate()
    with session() as s:
        assert s.get(Download, IDS[0]).status == "done"
    assert dl.iterate() == 30  # nothing left


def test_download_opts():
    o = ytdl.download_opts("/w", 720, 8_000_000, [print])
    assert o["format"].startswith("bv*[vcodec^=avc1][height<=720]+ba[acodec^=mp4a]")
    assert o["merge_output_format"] == "mp4" and o["continuedl"] and not o["skip_download"]
    assert o["ratelimit"] == 8_000_000 and o["outtmpl"] == "/w/%(id)s.%(ext)s"
    assert o["postprocessor_args"]["merger+ffmpeg_o"] == ["-movflags", "+faststart"]
    assert o["extractor_args"]["youtubepot-bgutilhttp"]["base_url"]


def test_api_downloads_and_file_range():
    from fastapi.testclient import TestClient
    from bt.main import app
    with session() as s:
        if not s.get(Video, IDS[0]):
            s.add(Video(id=IDS[0], title="x"))
            s.commit()
        _done(s, IDS[0], 10)
    with TestClient(app) as c:
        r = c.get(f"/api/video/{IDS[0]}/file", headers={"Range": "bytes=2-5"})
        assert r.status_code == 206 and r.headers["content-range"] == "bytes 2-5/10" and len(r.content) == 4
        with session() as s:
            assert s.get(Download, IDS[0]).last_played_at is not None
        assert c.get(f"/api/video/{IDS[1]}/file").status_code == 404
        lst = c.get("/api/downloads").json()
        assert lst["items"][0]["video_id"] == IDS[0] and lst["items"][0]["video"]["offline"] is True
        assert lst["totals"]["used_bytes"] == 10
        r = c.post("/api/downloads", json={"video_id": IDS[1]}).json()
        assert r["status"] == "queued" and r["pinned"] is True
        assert c.post("/api/downloads", json={"video_id": "bad"}).status_code == 400
        assert c.delete(f"/api/downloads/{IDS[0]}").json()["ok"]
        assert not (MEDIA_DIR / svc.rel_path(IDS[0])).exists()
        assert c.get(f"/api/video/{IDS[0]}/file").status_code == 404
        assert c.delete("/api/downloads").json()["removed"] == 1


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("BT_LIVE") != "1", reason="set BT_LIVE=1")
def test_live_download_short_video():
    _queue("jNQXAC9IVRw", pinned=True)
    dl = svc.Downloader(sleep=lambda s: None)
    t = time.monotonic()
    with session() as s:
        d = s.get(Download, "jNQXAC9IVRw")
    assert dl.run_one(d, {"max_gb": 1, "max_height": 1080, "rate_limit": 0}) == "done"
    p = MEDIA_DIR / svc.rel_path("jNQXAC9IVRw")
    data = p.read_bytes()
    assert data[4:8] == b"ftyp" and b"moov" in data[:4096], "faststart: moov should precede mdat"
    assert time.monotonic() - t < 120


def test_a_copy_being_streamed_is_never_evicted():
    """Evicting a playing copy 404s the player's next Range request, so it is not a candidate."""
    with session() as s:
        c = _cat(s)
        _done(s, IDS[0], 100_000_000)  # never played: evictable
        _cand(s, c, IDS[0])
        playing = _done(s, IDS[1], 400_000_000)  # a file request 30 s ago: may be streaming
        _cand(s, c, IDS[1])
        playing.last_played_at = now() - timedelta(seconds=30)
        s.add(playing)
        s.commit()
        assert [d.video_id for d in svc.eviction_order(s, allow_wanted=True)] == [IDS[0]]
        # 500 MB used against a 300 MB cap: evicting the idle copy is not enough, and the playing
        # copy is off limits, so the download waits rather than breaking the stream.
        assert svc.make_room(s, {"max_gb": 0.3}, 0, allow_wanted=True) is False
        assert s.get(Download, IDS[1]).status == "done"
        assert (MEDIA_DIR / svc.rel_path(IDS[1])).exists()
        assert s.get(Download, IDS[0]).status == "evicted"
        # Once the play window lapses it becomes an ordinary candidate again.
        playing.last_played_at = now() - timedelta(seconds=svc.ACTIVE_PLAY_S + 60)
        s.add(playing); s.commit()
        assert [d.video_id for d in svc.eviction_order(s, allow_wanted=True)] == [IDS[1]]


def test_drop_dead_queue_removes_rows_that_can_never_run():
    with session() as s:
        cat = _cat(s)
        _cand(s, cat, IDS[0])                          # accepted and wanted
        _cand(s, cat, IDS[1], dismissed=True)          # accepted but dismissed
        _cand(s, cat, IDS[2], status="rejected")       # rejected
        _cand(s, cat, IDS[6])                          # accepted, error row below
        s.add(Download(video_id=IDS[0], status="queued"))
        s.add(Download(video_id=IDS[1], status="queued"))
        s.add(Download(video_id=IDS[2], status="queued"))
        s.add(Download(video_id=IDS[3], status="queued"))          # no candidate at all
        s.add(Download(video_id=IDS[4], status="queued", pinned=True))  # manual save, no candidate
        s.add(Download(video_id=IDS[5], status="error"))           # no candidate
        s.add(Download(video_id=IDS[6], status="error"))
        s.add(Download(video_id=IDS[7], status="done", path=svc.rel_path(IDS[7]), size_bytes=1))
        s.commit()
    with session() as s:
        assert svc.drop_dead_queue(s) == 4
    with session() as s:
        left = {d.video_id: d.status for d in s.exec(select(Download)).all()}
    assert left == {IDS[0]: "queued", IDS[4]: "queued", IDS[6]: "error", IDS[7]: "done"}


def test_iterate_drops_dead_queue_rows_and_reports_the_real_count():
    with session() as s:
        cat = _cat(s)
        _cand(s, cat, IDS[0])
        s.add(Download(video_id=IDS[0], status="queued"))
        s.add(Download(video_id=IDS[1], status="queued"))  # no candidate: never downloadable
        s.commit()
    # Disabled cache keeps the loop out of run_one so the reported count is the only thing under test.
    save_setting("cache", {"enabled": False, "max_gb": 1, "max_height": 720, "rate_limit": 0,
                           "min_gap_s": 0, "auto": "accepted"})
    assert svc.Downloader(sleep=lambda s: None).iterate() == 30
    with session() as s:
        assert s.get(Download, IDS[1]) is None
        assert s.get(Download, IDS[0]).status == "queued"
    assert get_kv("downloader_state")["queue"] == 1


def test_seen_cleanup_failure_does_not_fail_save(monkeypatch):
    from fastapi.testclient import TestClient
    from bt.main import app
    from bt.api import core
    def broken(*args):
        raise OSError('media temporarily unavailable')
    monkeypatch.setattr(core, 'drop_unwanted', broken)
    with session() as s:
        cat = _cat(s)
        _cand(s, cat, IDS[0], 90)
    client = TestClient(app)
    for _ in range(2):
        response = client.post('/api/history/seen', json={'video_id': IDS[0]})
        assert response.status_code == 200
        assert response.json()['ok']
    with session() as s:
        entries = s.exec(select(WatchHistory).where(WatchHistory.video_id == IDS[0])).all()
        assert len(entries) == 1 and entries[0].finished
        assert s.exec(select(Candidate).where(Candidate.video_id == IDS[0])).one().dismissed
