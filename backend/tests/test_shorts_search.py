"""Shorts-filtered search parsing (no network): YouTube nests Shorts in grid shelves yt-dlp skips."""
import os
import tempfile

os.environ.setdefault("BT_DATA_DIR", tempfile.mkdtemp(prefix="bt-test-"))

from bt import ytdl  # noqa: E402


def lockup(vid, title="", views="1.2K views", a11y=""):
    return {"shortsLockupViewModel": {
        "entityId": f"shorts-shelf-item-{vid}", "accessibilityText": a11y,
        "onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": vid}}},
        "overlayMetadata": {"primaryText": {"content": title}, "secondaryText": {"content": views}}}}


RESPONSE = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
    {"itemSectionRenderer": {"contents": [{"gridShelfViewModel": {"contents": [
        lockup("AAAAAAAAAAA", "Kernel 7.1 in 60 seconds"),
        lockup("BBBBBBBBBBB", a11y="Rust lands, 3.4K views - play Short"),
        {"shortsLockupViewModel": {"entityId": "x"}},  # no video id
    ]}}]}},
    {"continuationItemRenderer": {"continuationEndpoint": {
        "clickTrackingParams": "ctp", "continuationCommand": {"token": "NEXT"}}}},
]}}}}}


def test_shorts_lockups_become_verified_short_entries():
    entries = [e for e in (ytdl._shorts_entry(vm) for vm in ytdl._shorts_lockups(RESPONSE, [])) if e]
    assert [e["id"] for e in entries] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert entries[0]["title"] == "Kernel 7.1 in 60 seconds" and entries[0]["view_count"] == 1200
    assert entries[1]["title"] == "Rust lands"
    assert all(e["is_short"] and e["shorts_verified"] and e["duration_s"] == 0 for e in entries)


def test_search_continuation_uses_the_result_list_token():
    class Ie:
        _extract_continuation_ep_data = staticmethod(
            lambda ep: {"continuation": ep["continuationCommand"]["token"]} if ep else None)
    assert ytdl._search_continuation(RESPONSE, Ie()) == {"continuation": "NEXT"}
    assert ytdl._search_continuation({"contents": []}, Ie()) is None


def test_search_routes_shorts_and_offsets(monkeypatch):
    seen = {}
    monkeypatch.setattr(ytdl, "search_shorts", lambda q, n, origin, start: seen.update(q=q, n=n, start=start) or [])
    ytdl.search("linux", 20, start=999, shorts=True)
    assert seen == {"q": "linux", "n": 20, "start": ytdl.SEARCH_DEPTH_MAX}

    def extract(url, kind, origin, detail, **opts):
        seen.update(url=url, opts=opts)
        return {"entries": []}
    monkeypatch.setattr(ytdl, "_extract", extract)
    ytdl.search("linux", 20, start=40)
    assert seen["url"] == "ytsearch60:linux" and seen["opts"]["playliststart"] == 41
