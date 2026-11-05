from datetime import timedelta

from sqlmodel import select

from test_agent import fresh_db  # noqa: F401
from bt.agent import data, shelf
from bt.config import MEDIA_DIR
from bt.db import session
from bt.downloader import service as downloader
from bt.models import (Candidate, Category, Download, GeneratedSubtitles, Profile,
                       Video, WatchHistory, now)


def _profile_id():
    with session() as s:
        return s.exec(select(Profile.id)).first()


def _add_item(s, category, video_id, *, published_at=None, short=False,
              status="accepted", dismissed=False, path=None, subtitle=None):
    s.add(Video(id=video_id, title=video_id, channel_id="UC-" + video_id,
                channel_name="Creator " + video_id, published_at=published_at,
                is_short=short, duration_s=30 if short else 300))
    s.add(Candidate(category_id=category.id, video_id=video_id,
                    status=status, dismissed=dismissed))
    if path is not None:
        s.add(Download(video_id=video_id, status="done", path=path))
    if subtitle is not None:
        s.add(GeneratedSubtitles(video_id=video_id, status="ready", vtt=subtitle))


def _file(tmp_path, video_id):
    relative = video_id[:2] + "/" + video_id + ".mp4"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    return relative


def test_readiness_excludes_stale_watched_missing_local_and_counts_timeless(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "MEDIA_DIR", tmp_path)
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Recent", kind="playlist",
                            max_age_days=30, target_queue=5)
        timeless = Category(profile_id=pid, name="Any age", kind="playlist",
                            max_age_days=0, target_queue=4, position=1)
        disabled = Category(profile_id=pid, name="Disabled", enabled=False, target_queue=50)
        other_profile = Profile(name="Other")
        s.add_all([category, timeless, disabled, other_profile]); s.commit()
        s.refresh(category); s.refresh(timeless); s.refresh(other_profile)

        _add_item(s, category, "fresh-local", published_at=t - timedelta(days=2),
                  path=_file(tmp_path, "fresh-local"))
        _add_item(s, category, "stale-local", published_at=t - timedelta(days=60),
                  path=_file(tmp_path, "stale-local"))
        _add_item(s, category, "watched-local", published_at=t - timedelta(days=1),
                  path=_file(tmp_path, "watched-local"))
        _add_item(s, category, "missing-local", published_at=t - timedelta(days=1),
                  path="../outside.mp4")
        _add_item(s, timeless, "timeless-local", published_at=None,
                  path=_file(tmp_path, "timeless-local"))
        s.add(WatchHistory(profile_id=pid, video_id="watched-local", finished=True))

        # A different profile's enabled category must not leak into this snapshot.
        foreign = Category(profile_id=other_profile.id, name="Foreign", target_queue=99)
        s.add(foreign); s.commit(); s.refresh(foreign)
        _add_item(s, foreign, "foreign-local", published_at=t,
                  path=_file(tmp_path, "foreign-local"))
        s.commit()

        result = shelf.snapshot(s, pid, now=t)
        assert result["ready"] == 2
        assert result["target"] == 9
        assert result["fresh_7d"] == 2
        assert result["fresh_7d_published"] == 1
        assert result["fresh_7d_first_seen"] == 1
        assert result["categories"][0]["name"] == "Recent"
        recent = result["categories"][0]
        assert recent["ready"] == 1
        assert recent["accepted"] == 2  # stale and watched never mask the deficit
        assert recent["needs_refill"] is True
        assert "Disabled" not in {c["name"] for c in result["categories"]}
        assert "Foreign" not in {c["name"] for c in result["categories"]}


def test_shorts_require_valid_subtitles_for_muted_ready_count(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "MEDIA_DIR", tmp_path)
    pid = _profile_id()
    good_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello there\n"
    with session() as s:
        category = Category(profile_id=pid, name="Reels", kind="shorts", target_queue=3)
        s.add(category); s.commit(); s.refresh(category)
        _add_item(s, category, "short-valid", published_at=now(), short=True,
                  path=_file(tmp_path, "short-valid"), subtitle=good_vtt)
        _add_item(s, category, "short-no-vtt", published_at=now(), short=True,
                  path=_file(tmp_path, "short-no-vtt"))
        _add_item(s, category, "short-bad-vtt", published_at=now(), short=True,
                  path=_file(tmp_path, "short-bad-vtt"), subtitle="WEBVTT\nnot a cue\n")
        s.commit()

        result = shelf.snapshot(s, pid)
        assert result["ready"] == 1
        assert result["categories"][0]["pending"] == 1
        assert result["categories"][0]["failed"] == 1
        assert result["categories"][0]["needs_refill"] is True
        assert result["categories"][0]["accepted"] == 3


def test_freshness_uses_first_seen_only_when_publication_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "MEDIA_DIR", tmp_path)
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Freshness", target_queue=1)
        s.add(category); s.commit(); s.refresh(category)
        _add_item(s, category, "unknown-date", published_at=None,
                  path=_file(tmp_path, "unknown-date"))
        candidate = s.exec(select(Candidate).where(Candidate.video_id == "unknown-date")).one()
        candidate.created_at = t - timedelta(days=2)
        s.add(candidate); s.commit()
        result = shelf.snapshot(s, pid, now=t)
        assert result["fresh_7d"] == 1
        assert result["fresh_7d_published"] == 0
        assert result["fresh_7d_first_seen"] == 1


def test_accepted_unwatched_does_not_count_published_stale_inventory():
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Age limited", max_age_days=10)
        s.add(category); s.commit(); s.refresh(category)
        _add_item(s, category, "old-published", published_at=t - timedelta(days=20))
        _add_item(s, category, "unknown-published", published_at=None)
        s.commit()
        assert data.accepted_unwatched(s, category) == 1


def test_full_stale_single_creator_queue_gets_bounded_variety_refill():
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Stale", target_queue=5)
        s.add(category); s.commit(); s.refresh(category)
        for n in range(5):
            video_id = f"stale-{n}"
            s.add(Video(id=video_id, channel_id="one-creator", published_at=t - timedelta(days=30)))
            s.add(Candidate(category_id=category.id, video_id=video_id, status="accepted"))
        s.commit()
        needs = data.category_needs(s)
        need = next(row for row in needs if row.cat.id == category.id)
        assert need.deficit == 3


def test_variety_refill_stops_at_buffer_ceiling_and_cooldown_keeps_backlog_visible():
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Buffered", target_queue=5)
        s.add(category); s.commit(); s.refresh(category)
        for n in range(8):
            video_id = f"buffer-{n}"
            s.add(Video(id=video_id, channel_id="one-creator", published_at=t - timedelta(days=30)))
            s.add(Candidate(category_id=category.id, video_id=video_id, status="accepted"))
        s.commit()
        assert not any(row.cat.id == category.id for row in data.category_needs(s))

        category.last_worked_at = t - timedelta(minutes=20)
        pending = Candidate(category_id=category.id, video_id="pending-candidate", status="found")
        s.add(Video(id="pending-candidate", channel_id="another")); s.add(pending); s.add(category); s.commit()
        need = next(row for row in data.category_needs(s) if row.cat.id == category.id)
        assert need.deficit == 0
        assert need.counts["found"] == 1


def test_fresh_diverse_full_queue_does_not_refill_for_variety():
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Fresh", target_queue=5)
        s.add(category); s.commit(); s.refresh(category)
        for n in range(5):
            video_id = f"fresh-{n}"
            s.add(Video(id=video_id, channel_id=f"creator-{n}", published_at=t))
            s.add(Candidate(category_id=category.id, video_id=video_id, status="accepted"))
        s.commit()
        assert not any(row.cat.id == category.id for row in data.category_needs(s))


def test_snapshot_accepts_unix_timestamp_asof(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "MEDIA_DIR", tmp_path)
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Unix asof", target_queue=1)
        s.add(category); s.commit(); s.refresh(category)
        _add_item(s, category, "unix-asof", published_at=t, path=_file(tmp_path, "unix-asof"))
        s.commit()
        result = shelf.snapshot(s, pid, now=t.timestamp())
        assert result["ready"] == 1


def test_terminal_failures_allow_only_bounded_replacement(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "MEDIA_DIR", tmp_path)
    pid = _profile_id()
    t = now()
    with session() as s:
        category = Category(profile_id=pid, name="Broken downloads", target_queue=5,
                            last_worked_at=t - timedelta(hours=2))
        s.add(category); s.commit(); s.refresh(category)
        for n in range(5):
            video_id = f"failed-{n}"
            s.add(Video(id=video_id, channel_id=f"creator-{n}", published_at=t))
            s.add(Candidate(category_id=category.id, video_id=video_id, status="accepted"))
            s.add(Download(video_id=video_id, status="error", attempts=3))
        s.commit()
        assert data.category_refill_deficit(s, category, t) == 3
        for n in range(5, 8):
            video_id = f"failed-{n}"
            s.add(Video(id=video_id, channel_id=f"creator-{n}", published_at=t))
            s.add(Candidate(category_id=category.id, video_id=video_id, status="accepted"))
            s.add(Download(video_id=video_id, status="error", attempts=3))
        s.commit()
        assert data.category_refill_deficit(s, category, t) == 0
