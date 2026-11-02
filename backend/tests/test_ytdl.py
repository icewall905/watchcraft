"""ytdl helpers (no network unless BT_LIVE=1)."""
import os
import tempfile

os.environ.setdefault("BT_DATA_DIR", tempfile.mkdtemp(prefix="bt-test-"))

import base64  # noqa: E402
import gzip  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from datetime import timezone  # noqa: E402

from bt.db import get_kv, init_db, save_setting  # noqa: E402

init_db()

import pytest  # noqa: E402

from bt import feeds, ytdl  # noqa: E402


@pytest.mark.parametrize("msg,cls", [
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies", ytdl.YtBlocked),
    ("HTTP Error 429: Too Many Requests", ytdl.YtBlocked),
    ("Got a captcha page", ytdl.YtBlocked),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate", ytdl.YtUnavailable),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", ytdl.YtUnavailable),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", ytdl.YtUnavailable),
    ("Join this channel to get access to members-only content", ytdl.YtUnavailable),
    ("ERROR: [youtube] aaaaaaaaaaa: This video is unavailable", ytdl.YtUnavailable),
    ("ERROR: [youtube] abc: Video unavailable. Sign in to confirm you're not a bot", ytdl.YtBlocked),
    ("Some weird extractor failure", ytdl.YtError),
])
def test_classify(msg, cls):
    assert type(ytdl.classify(Exception(msg))) is cls


def test_extract_raises_classified(monkeypatch):
    logged = []
    monkeypatch.setattr(ytdl, "log_yt_request", lambda *a: logged.append(a))

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k): raise Exception("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(ytdl.yt_dlp, "YoutubeDL", Boom)
    with pytest.raises(ytdl.YtBlocked):
        ytdl.search("x", origin="user")
    assert logged[0][0] == "search" and logged[0][1] == "user" and logged[0][2] is False


def test_proxy_setting_reaches_ytdlp_opts():
    from bt.db import save_setting
    save_setting("throttle", {"proxy": "socks5://u:p@proxy-nl.privateinternetaccess.com:1080"})
    assert ytdl.yt_proxy() == "socks5://u:p@proxy-nl.privateinternetaccess.com:1080"
    assert ytdl._opts()["proxy"] == "socks5://u:p@proxy-nl.privateinternetaccess.com:1080"
    save_setting("throttle", {"proxy": ""})
    assert ytdl.yt_proxy() == ""
    assert "proxy" not in ytdl._opts()


def test_proxy_setting_without_scheme_is_ignored():
    from bt.db import save_setting
    save_setting("throttle", {"proxy": "proxy-nl.privateinternetaccess.com:1080"})
    assert ytdl.yt_proxy() == ""
    assert "proxy" not in ytdl._opts()
    save_setting("throttle", {"proxy": ""})


def test_socks_rdns_patch_installed():
    # PIA's SOCKS5 cannot resolve hostnames: without local DNS every request fails with 0x04
    assert ytdl.SOCKS_RDNS_PATCH is True
    from yt_dlp.socks import sockssocket
    s = sockssocket()
    s.setproxy(1, "127.0.0.1", 1080, rdns=True)  # type: ignore[arg-type]
    assert s._proxy.remote_dns is False


def test_pinned_proxy_url_leaves_http_and_ip_hosts_alone():
    assert ytdl.pinned_proxy_url("http://gluetun:8888") == "http://gluetun:8888"
    assert ytdl.pinned_proxy_url("socks5://u:p@127.0.0.1:1080") == "socks5://u:p@127.0.0.1:1080"
    assert ytdl.pinned_proxy_url("") == ""


def test_pinned_proxy_url_unresolvable_host_unchanged():
    url = "socks5://u:p@no-such-host.invalid:1080"
    assert ytdl.pinned_proxy_url(url) == url


def test_pinned_proxy_url_keeps_credentials(monkeypatch):
    import socket as sockmod

    def fake_getaddrinfo(host, port, family=0):
        return [(sockmod.AF_INET, sockmod.SOCK_STREAM, 6, "", ("109.201.152.161", port or 1080))]

    monkeypatch.setattr(ytdl, "_socks_probe", lambda ip, p: True)
    monkeypatch.setattr(sockmod, "getaddrinfo", fake_getaddrinfo)
    ytdl._pin_cache.clear()
    assert ytdl.pinned_proxy_url("socks5://u%40x:p%40y@proxy.example.com:1080") \
        == "socks5://u%40x:p%40y@109.201.152.161:1080"
    ytdl._pin_cache.clear()


JSON3 = json.dumps({"events": [
    {"tStartMs": 0, "dDurationMs": 1000},  # window event without segs
    {"tStartMs": 100, "dDurationMs": 2000, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
    {"tStartMs": 2100, "dDurationMs": 2000, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 2200, "dDurationMs": 2000, "segs": [{"utf8": "hello world"}]},  # rolling repeat
    {"tStartMs": 4000, "dDurationMs": 1500, "segs": [{"utf8": "second   line\nthird line"}]},
]})


def test_json3_to_text():
    assert ytdl.json3_to_text(JSON3) == "hello world second line third line"
    assert ytdl.json3_to_text("not json") == ""


def test_json3_to_vtt():
    vtt = ytdl.json3_to_vtt(JSON3)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.100 --> 00:00:02.100\nhello world" in vtt
    assert "00:00:04.000 --> 00:00:05.500\nsecond line\nthird line" in vtt


def test_vtt_to_text():
    raw = ("WEBVTT\nKind: captions\nLanguage: en\n\n00:00:00.000 --> 00:00:01.000 align:start\n"
           "<c>hello</c><00:00:00.500><c> there</c>\n\n00:00:01.000 --> 00:00:02.000\nhello there\nnext\n")
    assert ytdl.vtt_to_text(raw) == "hello there next"


def _sub(lang, auto=False, tlang=None, ext="json3"):
    u = f"https://www.youtube.com/api/timedtext?v=x&lang={lang}" + ("&kind=asr" if auto else "")
    if tlang:
        u += f"&tlang={tlang}"
    return {"ext": ext, "url": u, "name": lang}


def test_caption_tracks_and_pick():
    info = {"language": "de", "subtitles": {"en": [_sub("en")], "live_chat": [_sub("x")]},
            "automatic_captions": {"de": [_sub("de", True)], "fr": [_sub("de", True, tlang="fr")]}}
    keys = [t["key"] for t in ytdl.caption_tracks(info)]
    assert keys == ["en", "de-auto"]
    assert ytdl.pick_track(info, ["en"])["key"] == "en"  # manual in preferred lang
    assert ytdl.pick_track(info)["key"] == "de-auto"  # video language first (manual, then auto)
    assert ytdl.pick_track(info, ["ja"])["key"] == "en"  # nothing matches -> manual first
    assert ytdl.pick_track({"subtitles": {}}) is None


def test_transcript(monkeypatch):
    monkeypatch.setattr(ytdl, "_http_get", lambda url, kind, origin: JSON3.encode())
    info = {"subtitles": {"en-US": [_sub("en-US")]}, "automatic_captions": {}}
    assert ytdl.transcript(info, 11) == "hello world"
    assert ytdl.transcript({"subtitles": {}}, 100) == ""


def test_entry_and_upsert(monkeypatch):
    e = ytdl._entry({"id": "26P1KyvrmjI", "title": "T", "duration": 45, "channel_id": "UCx", "channel": "C",
                     "view_count": 10, "url": "https://www.youtube.com/watch?v=26P1KyvrmjI", "timestamp": 1700000000})
    assert e["is_short"] and not e["is_live"]
    assert e["published_at"].tzinfo is timezone.utc
    assert ytdl._entry({"id": "UCY1kMZp36IQSyNx_9h4mpCg", "url": "x"}) is None

    from bt.db import init_db, session
    from bt.models import Video
    init_db()
    with session() as s:
        v = ytdl.upsert_video(s, e)
        s.commit()
        full = {"id": "26P1KyvrmjI", "title": "T2", "formats": [], "duration": 300, "upload_date": "20240102",
                "description": "d", "tags": ["a"], "chapters": [{"start_time": 0, "end_time": 5, "title": "c"}],
                "live_status": "not_live", "channel": "C", "like_count": 3}
        v = ytdl.upsert_video(s, full)
        s.commit()
        v = s.get(Video, "26P1KyvrmjI")
        assert (v.title, v.duration_s, v.is_short, v.like_count, v.description) == ("T2", 300, False, 3, "d")
        assert v.chapters[0]["title"] == "c" and v.fetched_at is not None
        assert v.published_at.year == 2024


def test_channel_url():
    assert ytdl._channel_url("UCY1kMZp36IQSyNx_9h4mpCg").endswith("/channel/UCY1kMZp36IQSyNx_9h4mpCg/videos")
    assert ytdl._channel_url("@MarkRober") == "https://www.youtube.com/@MarkRober/videos"
    assert ytdl._channel_url("https://www.youtube.com/@MarkRober") == "https://www.youtube.com/@MarkRober/videos"
    assert ytdl._channel_url("https://youtube.com/@MarkRober/streams").endswith("/@MarkRober/streams")
    with pytest.raises(ytdl.YtError):
        ytdl._channel_url("https://evil.com/@x")


def test_comments_mapping(monkeypatch):
    got = {}

    def fake(url, kind, origin, detail, **opts):
        got.update(opts)
        return {"comments": [
            {"author": "a", "text": "top", "like_count": 50, "parent": "root"},
            {"author": "b", "text": "reply", "like_count": 99, "parent": "abc"},
            {"author": "c", "text": "second", "like_count": None, "parent": "root"},
            {"author": "d", "text": "third", "like_count": 1, "parent": "root"},
        ]}

    monkeypatch.setattr(ytdl, "_extract", fake)
    logged = []
    monkeypatch.setattr(ytdl, "log_yt_request", lambda *a: logged.append(a))
    assert ytdl.comments("dQw4w9WgXcQ", 2) == [{"author": "a", "text": "top", "likes": 50},
                                               {"author": "c", "text": "second", "likes": 0}]
    assert got["getcomments"] is True
    assert got["extractor_args"]["youtube"]["max_comments"] == ["2", "all", "0", "0"]
    assert got["extractor_args"]["youtube"]["comment_sort"] == ["top"]
    assert [x[0] for x in logged] == ["comments_page"]  # 4 raw comments -> 1 page
    got["comments"] = [{"text": "x", "parent": "root"}] * 41
    monkeypatch.setattr(ytdl, "_extract", lambda *a, **k: {"comments": got["comments"]})
    logged.clear()
    ytdl.comments("dQw4w9WgXcQ", 40)
    assert len(logged) == 3


def test_m3u8_captions_log_each_request(monkeypatch):
    seen = []

    def fake_get(url, kind, origin):
        seen.append((url, kind))
        if url.endswith(".m3u8"):
            return b"#EXTM3U\n#EXTINF:10,\nseg0.vtt\n#EXTINF:10,\nseg1.vtt\n"
        return b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhi " + url[-8:].encode() + b"\n"

    monkeypatch.setattr(ytdl, "_http_get", fake_get)
    fmt, text = ytdl.fetch_track({"formats": {"m3u8": "https://manifest.googlevideo.com/x/index.m3u8"}})
    assert fmt == "vtt" and len(seen) == 3 and all(k == "transcript" for _, k in seen)
    assert "seg0.vtt" in text and "seg1.vtt" in text


def test_opts_pot_and_merge():
    o = ytdl._opts(extractor_args={"youtube": {"x": ["1"]}})
    assert o["extractor_args"]["youtubepot-bgutilhttp"]["base_url"]
    assert o["extractor_args"]["youtube"] == {"x": ["1"]}
    assert o["skip_download"] and o["quiet"]


def test_range_hook_attaches_ranges():
    from yt_dlp.extractor.youtube._video import YoutubeIE
    assert getattr(YoutubeIE._extract_formats_and_subtitles, "_bt_hooked", False)
    prs = [{"streamingData": {"adaptiveFormats": [
        {"itag": 137, "contentLength": "555", "initRange": {"start": "0", "end": "741"},
         "indexRange": {"start": "742", "end": "1229"}},
        {"itag": 140, "contentLength": "777"}]}}, None]
    items = [{"format_id": "137", "url": "https://x.googlevideo.com/videoplayback?itag=137&clen=555"},
             {"format_id": "140", "url": "https://x.googlevideo.com/videoplayback?itag=140&clen=777"},
             {"format_id": "137b", "url": "https://x.googlevideo.com/videoplayback?itag=137&clen=999"},
             {"en": []}]
    out = list(ytdl.attach_ranges(prs, iter(items)))
    assert out[0]["bt_init_range"] == "0-741" and out[0]["bt_index_range"] == "742-1229"
    assert "bt_init_range" not in out[1] and "bt_init_range" not in out[2] and out[3] == {"en": []}


def test_feed_parse():
    xml = """<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
      <title>Chan</title><yt:channelId>UCabc</yt:channelId>
      <entry><yt:videoId>dQw4w9WgXcQ</yt:videoId><yt:channelId>UCabc</yt:channelId><title>Vid</title>
       <link rel="alternate" href="https://www.youtube.com/shorts/dQw4w9WgXcQ"/>
       <author><name>Chan</name></author><published>2025-01-02T03:04:05+00:00</published>
       <media:group><media:community><media:statistics views="123"/></media:community></media:group></entry>
    </feed>"""
    [e] = feeds.parse_feed(xml)
    assert e["id"] == "dQw4w9WgXcQ" and e["channel_id"] == "UCabc" and e["channel_name"] == "Chan"
    assert e["view_count"] == 123 and e["is_short"] and e["published_at"].tzinfo is not None


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("BT_LIVE") != "1", reason="set BT_LIVE=1")
def test_live_transcript_comments_rss():
    info = ytdl.video_info("dQw4w9WgXcQ", origin="user")
    t = ytdl.transcript(info, 5000, ["en"], origin="user")
    assert "never gonna" in t.lower()
    cs = ytdl.comments("dQw4w9WgXcQ", 5, origin="user")
    assert 1 <= len(cs) <= 5 and cs[0]["text"]
    ch, vids = ytdl.channel_videos("@RickAstleyYT", 3, origin="user")
    assert ch["id"].startswith("UC") and vids
    assert feeds.channel_feed(ch["id"], origin="user")


def test_video_info_stored_in_shared_cache(monkeypatch):
    """A successful extraction is written to the shared KV store (compressed) so another
    process — worker enriched it, api plays it — reads it warm without extracting again."""
    ytdl.cache_clear()
    calls = []

    def fake(url, kind, origin, detail, **opts):
        calls.append(url)
        return {"id": "shared00000", "title": "Warm", "formats": [
            {"format_id": "18", "url": "https://x/v", "bt_init_range": "0-999", "bt_index_range": "1000-2000"}]}

    monkeypatch.setattr(ytdl, "_extract", fake)
    got = ytdl.video_info("shared00000", origin="worker")
    assert got["id"] == "shared00000" and len(calls) == 1
    row = get_kv("ytinfo:shared00000")
    assert row["ts"] > 0 and row["gz"]
    assert json.loads(gzip.decompress(base64.b64decode(row["gz"])))["id"] == "shared00000"

    # Fresh process: memory empty, shared store warm -> no second extraction, ranges intact.
    ytdl._cache.clear()
    got2 = ytdl.video_info("shared00000", origin="user")
    assert len(calls) == 1
    assert got2["id"] == "shared00000"
    assert got2["formats"][0]["bt_init_range"] == "0-999"

    # cache_drop forgets both layers: next call extracts again.
    ytdl.cache_drop("shared00000")
    ytdl._cache.clear()
    ytdl.video_info("shared00000")
    assert len(calls) == 2 and get_kv("ytinfo:shared00000")["ts"] > 0

    # A stale shared entry is ignored and re-extracted (same 4h TTL as the memory cache).
    row = get_kv("ytinfo:shared00000")
    save_setting("ytinfo:shared00000", {"ts": time.time() - ytdl.INFO_TTL_S - 10, "gz": row["gz"]})
    ytdl._cache.clear()
    ytdl.video_info("shared00000")
    assert len(calls) == 3

    # A corrupt shared entry must never break playback: ignored, extracted fresh.
    save_setting("ytinfo:shared00000", {"ts": time.time(), "gz": "not-base64!"})
    ytdl._cache.clear()
    assert ytdl.video_info("shared00000")["id"] == "shared00000"
    assert len(calls) == 4
    ytdl.cache_clear()


def test_cache_clear_purges_shared_infos(monkeypatch):
    monkeypatch.setattr(ytdl, "_extract", lambda *a, **k: {"id": "purge000000", "formats": []})
    ytdl.cache_clear()
    ytdl._cache.clear()
    ytdl.video_info("purge000000")
    assert get_kv("ytinfo:purge000000")["gz"]
    ytdl.cache_clear()
    assert get_kv("ytinfo:purge000000") == {}


def test_video_info_failure_cached_and_shared(monkeypatch):
    import threading
    import time as _t
    ytdl.cache_clear()
    calls = []

    def fake(url, kind, origin, detail, **opts):
        calls.append(url)
        _t.sleep(0.2)
        raise ytdl.YtBlocked("Sign in to confirm you're not a bot")

    monkeypatch.setattr(ytdl, "_extract", fake)
    errs = []

    def worker():
        try:
            ytdl.video_info("bbbbbbbbbbb")
        except ytdl.YtError as e:
            errs.append(type(e))

    ts = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and errs == [ytdl.YtBlocked] * 5
    with pytest.raises(ytdl.YtBlocked):
        ytdl.video_info("bbbbbbbbbbb")
    assert len(calls) == 1
    assert "bbbbbbbbbbb" not in ytdl._id_locks
    until, _ = ytdl._fail["bbbbbbbbbbb"]
    assert until - _t.time() > ytdl.FAIL_TTL_S  # blocked -> long ttl

    # unavailable: short ttl, then retried after expiry
    monkeypatch.setattr(ytdl, "_extract", lambda *a, **k: (calls.append(1), (_ for _ in ()).throw(
        ytdl.YtUnavailable("This video is unavailable")))[1])
    with pytest.raises(ytdl.YtUnavailable):
        ytdl.video_info("ccccccccccc")
    ytdl._fail["ccccccccccc"] = (_t.time() - 1, ytdl._fail["ccccccccccc"][1])
    monkeypatch.setattr(ytdl, "_extract", lambda *a, **k: {"id": "ccccccccccc", "formats": []})
    assert ytdl.video_info("ccccccccccc")["id"] == "ccccccccccc"
    assert "ccccccccccc" not in ytdl._id_locks and "ccccccccccc" not in ytdl._fail
    ytdl.cache_clear()


def test_range_hook_import_failure_is_graceful(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "yt_dlp.extractor.youtube._video", None)  # -> ImportError
    assert ytdl._install_range_hook() is False


def test_classify_transient_5xx_is_not_unavailable():
    from bt import ytdl
    e = ytdl.classify("ERROR: Unable to download API page: HTTP Error 503: Service Unavailable")
    assert type(e) is ytdl.YtError
    assert isinstance(ytdl.classify("ERROR: [youtube] x: This video is unavailable"), ytdl.YtUnavailable)


@pytest.mark.parametrize('ref', [
    'https://notyoutube.com/@Tasty', 'https://youtube.com.evil.org/@Tasty',
    'https://www.youtube.com/feed/subscriptions', 'https://www.youtube.com/playlist?list=private',
    'https://user:password@youtube.com/@Tasty', '@Tasty/../feed/subscriptions',
    'https://www.youtube.com/@Tasty%2f..%2ffeed',
])
def test_public_channel_rejects_non_channel_sources(monkeypatch, ref):
    calls = []
    monkeypatch.setattr(ytdl, '_extract', lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ytdl.YtError):
        ytdl.channel_videos(ref)
    assert calls == []


@pytest.mark.parametrize('tab,expected', [(None, 'videos'), ('shorts', 'shorts')])
def test_public_channel_authcheck_skip_is_scoped(monkeypatch, tab, expected):
    calls = []
    def fake(url, *a, **opts):
        calls.append((url, opts))
        return {'entries': [{'id': '26P1KyvrmjI', 'duration': 150, 'url': '26P1KyvrmjI'}]}
    monkeypatch.setattr(ytdl, '_extract', fake)
    ch, entries = ytdl.channel_videos('@Tasty', tab=tab)
    assert calls[0][0] == f'https://www.youtube.com/@Tasty/{expected}'
    assert calls[0][1]['extractor_args'] == {'youtubetab': {'skip': ['authcheck']}}
    assert entries[0]['is_short'] is (tab == 'shorts')
    assert 'youtubetab' not in ytdl._opts()['extractor_args']
    ytdl.search('cooking')
    assert 'extractor_args' not in calls[1][1]


def test_explicit_short_tab_replaces_other_channel_tab():
    assert ytdl._channel_url('https://youtube.com/@Tasty/videos', 'shorts').endswith('/@Tasty/shorts')
    assert ytdl._channel_url('https://youtube.com/@Tasty/shorts').endswith('/@Tasty/shorts')
