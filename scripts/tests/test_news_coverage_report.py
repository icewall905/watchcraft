import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "news_coverage_report.py"
spec = importlib.util.spec_from_file_location("news_coverage_report", SCRIPT)
reporter = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(reporter)

NOW = 1_800_000_000.0
DAY = 86400


def _db(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE curator (
          id INTEGER PRIMARY KEY, profile_id INTEGER, name TEXT, subject TEXT,
          instructions TEXT, queries TEXT, keywords TEXT, exclusions TEXT,
          content_types TEXT, aliases TEXT, enabled INTEGER, kind TEXT,
          interval_hours INTEGER, last_success REAL, last_status TEXT,
          ai_enabled INTEGER, min_score INTEGER, max_per_run INTEGER
        );
        CREATE TABLE source (
          id INTEGER PRIMARY KEY, profile_id INTEGER, provider TEXT, url TEXT,
          enabled INTEGER, last_success REAL, last_error TEXT,
          last_checked REAL, item_count INTEGER
        );
        CREATE TABLE curatorsource (curator_id INTEGER, source_id INTEGER);
        CREATE TABLE topic (profile_id INTEGER, kind TEXT, muted INTEGER,
                           name TEXT, keywords TEXT);
        CREATE TABLE contentitem (
          id INTEGER PRIMARY KEY, type TEXT, title TEXT, summary TEXT,
          publisher TEXT, provider TEXT, published_at REAL, first_seen REAL,
          body TEXT, enrichment TEXT, meta TEXT
        );
        CREATE TABLE curatordiscovery (
          id INTEGER PRIMARY KEY, curator_id INTEGER, source_id INTEGER,
          found_at REAL, item_id INTEGER, profile_id INTEGER
        );
        CREATE TABLE curatorrun (
          id INTEGER PRIMARY KEY, curator_id INTEGER, profile_id INTEGER,
          started_at REAL, finished_at REAL, status TEXT,
          new_items INTEGER, seen_items INTEGER
        );
        CREATE TABLE articletext (item_id INTEGER PRIMARY KEY, raw_body TEXT,
                                  status TEXT, version INTEGER);
        CREATE TABLE itemverdict (item_id INTEGER, profile_id INTEGER, tier TEXT);
        CREATE TABLE itemstate (item_id INTEGER, profile_id INTEGER,
                                read_at REAL, archived INTEGER, dismissed INTEGER);
        CREATE TABLE capturejob (item_id INTEGER, profile_id INTEGER,
                                 status TEXT, summary_status TEXT);
        CREATE TABLE storyitem (item_id INTEGER PRIMARY KEY, story_id INTEGER);
        """
    )
    return db


def _curator(db, cid, pid=1, *, name="News", types=("news",), **values):
    row = {
        "id": cid, "profile_id": pid, "name": name, "subject": name,
        "instructions": "private instructions token=VERY_PRIVATE_KEY", "queries": [name],
        "keywords": [name.lower()], "exclusions": [], "content_types": list(types),
        "aliases": [], "enabled": 1, "kind": "watch", "interval_hours": 6,
        "last_success": NOW - 3600, "last_status": "ok", "ai_enabled": 0,
        "min_score": 0, "max_per_run": 30,
    }
    row.update(values)
    columns = ",".join(row)
    db.execute(f"INSERT INTO curator ({columns}) VALUES ({','.join('?' for _ in row)})",
               [json.dumps(v) if isinstance(v, list) else v for v in row.values()])


def _source(db, sid, pid=1, *, provider="rss", url="https://user:secret@example.test/feed"):
    db.execute("INSERT INTO source VALUES (?,?,?,?,?,?,?,?,?)",
               (sid, pid, provider, url, 1, NOW - 600, "", NOW - 600, 4))


def _item(db, iid, *, pid=1, curator=1, source=1, found=NOW - 3600,
          published=NOW - 3600, title="Article", summary="Summary",
          publisher="Publisher", tier="keep", body="body", raw="raw",
          cleanup="reviewed", version=1, capture="complete", summary_status="ready",
          ai_summary="AI summary", read_at=None, archived=0, dismissed=0,
          language=None, story=None, item_type="news"):
    db.execute("INSERT INTO contentitem VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (iid, item_type, title, summary, publisher, "rss", published,
                NOW - 4 * DAY, body, json.dumps({"summary": ai_summary}),
                json.dumps({"language": language} if language else {})))
    db.execute("INSERT INTO curatordiscovery VALUES (?,?,?,?,?,?)",
               (iid, curator, source, found, iid, pid))
    if tier is not None:
        db.execute("INSERT INTO itemverdict VALUES (?,?,?)", (iid, pid, tier))
    if raw is not None:
        db.execute("INSERT INTO articletext VALUES (?,?,?,?)", (iid, raw, cleanup, version))
    if capture is not None:
        db.execute("INSERT INTO capturejob VALUES (?,?,?,?)", (iid, pid, capture, summary_status))
    if any((read_at, archived, dismissed)):
        db.execute("INSERT INTO itemstate VALUES (?,?,?,?,?)", (iid, pid, read_at, archived, dismissed))
    if story is not None:
        db.execute("INSERT INTO storyitem VALUES (?,?)", (iid, story))


def test_deduplicates_curators_and_separates_publication_from_discovery(tmp_path):
    path = tmp_path / "news.sqlite3"
    db = _db(path)
    _curator(db, 1, name="Science")
    _curator(db, 2, name="Science follow-up")
    _source(db, 1)
    _source(db, 2, url="https://other.example/feed")
    db.execute("INSERT INTO curatorsource VALUES (?,?)", (1, 1))
    db.execute("INSERT INTO curatorsource VALUES (?,?)", (2, 2))
    _item(db, 10, found=NOW - 2 * DAY, published=NOW - 3600, publisher="A", story=7)
    db.execute("INSERT INTO curatordiscovery VALUES (?,?,?,?,?,?)", (20, 2, 2, NOW - 3600, 10, 1))
    _item(db, 11, found=NOW - 3600, published=NOW - 3 * DAY, publisher="B")
    db.execute("INSERT INTO curatorrun VALUES (?,?,?,?,?,?,?,?)",
               (1, 1, 1, NOW - 1800, NOW - 1700, "ok", 1, 2))
    db.commit(); db.close()

    out = reporter.report(path, now=NOW)
    windows = out["profiles"][0]["windows"]
    assert windows["1"]["counts"]["discovered"] == 1
    assert windows["1"]["newly_discovered"] == 1
    assert windows["1"]["newly_discovered_older_articles"] == 1
    assert windows["7"]["counts"]["discovered"] == 2
    assert windows["7"]["newly_discovered"] == 2
    assert windows["7"]["newly_discovered_older_articles"] == 0
    curator = out["profiles"][0]["curators"][0]
    assert curator["recent_discovery_types"] == {"news": 2}
    assert curator["runs"]["1"]["n"] == 1
    assert curator["runs"]["1"]["new_items"] == 1
    assert curator["runs"]["1"]["source_results"] == 2


def test_profile_isolation_and_readiness_blockers_are_disjoint(tmp_path):
    path = tmp_path / "news.sqlite3"
    db = _db(path)
    _curator(db, 1, pid=1, name="One")
    _curator(db, 2, pid=2, name="Two")
    _source(db, 1, pid=1)
    _source(db, 2, pid=2, url="https://two.example/feed")
    db.execute("INSERT INTO curatorsource VALUES (?,?)", (1, 1))
    # A bad cross-profile attachment must not expose source 2 in profile 1's report.
    db.execute("INSERT INTO curatorsource VALUES (?,?)", (1, 2))
    _item(db, 1, pid=1, curator=1, source=1, publisher="A")
    _item(db, 2, pid=1, curator=1, source=1, body="", raw="raw", publisher="B")
    _item(db, 3, pid=1, curator=1, source=1, cleanup="queued", publisher="C")
    _item(db, 4, pid=1, curator=1, source=1, capture="queued", publisher="D")
    _item(db, 5, pid=1, curator=1, source=1, summary_status="failed", ai_summary="", publisher="E")
    _item(db, 6, pid=1, curator=1, source=1, capture="partial", publisher="F", language="da", story=9)
    _item(db, 7, pid=2, curator=2, source=2, publisher="Other")
    # Cross-profile rows for the same item must not override profile 1's joins.
    db.execute("INSERT INTO itemstate VALUES (?,?,?,?,?)", (1, 2, NOW, 1, 1))
    db.execute("INSERT INTO itemverdict VALUES (?,?,?)", (1, 2, "reject"))
    db.execute("INSERT INTO capturejob VALUES (?,?,?,?)", (1, 2, "failed", "failed"))
    db.commit(); db.close()

    out = reporter.report(path, profile=1, now=NOW)
    entry = out["profiles"][0]
    counts = entry["windows"]["7"]["counts"]
    assert counts["discovered"] == 6
    assert counts["approved"] == 6
    assert counts["ready"] == 2
    supply = entry["reference_supply"]
    assert supply["ready_target"] == 100 and supply["candidate_target"] == 300
    assert supply["ready_shortfall_upper_bound"] == 98
    assert supply["candidate_shortfall_upper_bound"] == 294
    assert supply["ready_met"] is False and supply["candidate_buffer_met"] is False and supply["met"] is False
    assert entry["windows"]["7"]["preparation"] == {
        "ready": 2, "missing_local_text": 1, "cleanup_not_current": 1,
        "capture_not_complete": 1, "summary_not_ready": 1,
    }
    assert entry["windows"]["7"]["ready_languages"] == {"unknown": 1, "da": 1}
    assert out["profiles"][0]["source_id_breakdown"]["1"]["7"]["counts"]["discovered"] == 6
    assert out["profiles"][0]["curators"][0]["windows"]["7"]["counts"]["discovered"] == 6
    assert [source["id"] for source in out["profiles"][0]["curators"][0]["sources"]] == [1]


def test_read_archive_mute_unknown_future_and_truncation(tmp_path):
    path = tmp_path / "news.sqlite3"
    db = _db(path)
    _curator(db, 1)
    _source(db, 1)
    db.execute("INSERT INTO curatorsource VALUES (?,?)", (1, 1))
    db.execute("INSERT INTO topic VALUES (?,?,?,?,?)", (1, "news", 1, "Muted", json.dumps(["blocked"])))
    _item(db, 1, title="blocked headline", publisher="Hidden")
    _item(db, 2, read_at=NOW - 1, publisher="Read")
    _item(db, 3, archived=1, publisher="Archived")
    _item(db, 4, published=None, found=NOW - 3600, publisher="Unknown")
    _item(db, 5, published=NOW + 3600, found=NOW - 3600, publisher="Future")
    _item(db, 6, found=NOW - 2 * DAY, published=NOW - 2 * DAY, publisher="Older")
    db.commit(); db.close()

    out = reporter.report(path, now=NOW, limit=5)
    entry = out["profiles"][0]
    assert entry["truncated"] is True
    assert entry["counts_are_lower_bounds"] is True
    assert entry["reference_supply"]["ready_met"] is None
    assert entry["reference_supply"]["met"] is None
    w = entry["windows"]["7"]
    assert w["future_dated"] >= 1
    assert w["counts"]["unknown_publication_date"] >= 1
    assert w["counts"]["hidden"] >= 1
    assert w["counts"]["consumed_or_archived"] >= 2


def test_default_output_is_metadata_only_and_report_is_read_only(tmp_path):
    path = tmp_path / "news.sqlite3"
    db = _db(path)
    _curator(db, 1)
    _source(db, 1)
    db.execute("INSERT INTO curatorsource VALUES (?,?)", (1, 1))
    _item(db, 1, title="TOP SECRET ARTICLE", summary="private body", publisher="Private Press")
    db.commit(); db.close()
    before = hashlib.sha256(path.read_bytes()).digest()

    out = reporter.report(path, now=NOW)
    encoded = json.dumps(out)
    assert "TOP SECRET ARTICLE" not in encoded
    assert "private body" not in encoded
    assert "private instructions" not in encoded
    assert "secret@example.test" not in encoded
    assert "url" not in out["profiles"][0]["curators"][0]["sources"][0]
    assert hashlib.sha256(path.read_bytes()).digest() == before

    configured = reporter.report(path, now=NOW, include_config=True)
    source = configured["profiles"][0]["curators"][0]["sources"][0]
    assert "instructions" not in configured["profiles"][0]["curators"][0]["configuration"]
    assert "VERY_PRIVATE_KEY" not in json.dumps(configured)
    assert source["url"] == "https://example.test/feed"
    assert "secret" not in json.dumps(configured)


def test_cleanup_version_is_kept_in_sync_with_application(tmp_path):
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from bt.hub.article_cleanup import VERSION
        from bt.hub.news_supply import TARGET, CANDIDATE_TARGET
    finally:
        sys.path.pop(0)
    assert reporter.CLEANUP_VERSION == VERSION
    assert reporter.READY_TARGET == TARGET
    assert reporter.CANDIDATE_TARGET == CANDIDATE_TARGET


@pytest.mark.parametrize("limit", [0, 200001])
def test_report_rejects_unsafe_limits(tmp_path, limit):
    path = tmp_path / "empty.sqlite3"
    db = _db(path); _curator(db, 1); db.commit(); db.close()
    with pytest.raises(ValueError, match="limit"):
        reporter.report(path, now=NOW, limit=limit)
