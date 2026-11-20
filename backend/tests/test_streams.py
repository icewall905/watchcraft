"""MPD generation, proxy tokens, range probing, extras (no network)."""
import asyncio
import os
import struct
import xml.etree.ElementTree as ET

import httpx
import pytest

from bt import extras, streams

NS = {"d": "urn:mpeg:dash:schema:mpd:2011"}
GV = "https://rr1---sn-abc.googlevideo.com/videoplayback?itag={itag}&clen=1000&dur=212.091&mime=x"


def vf(itag, codec, h, ext="mp4", fps=30, tbr=1000.0, **kw):
    return {"format_id": str(itag), "url": GV.format(itag=itag), "protocol": "https", "ext": ext,
            "vcodec": codec, "acodec": "none", "height": h, "width": h * 16 // 9, "fps": fps, "tbr": tbr,
            "dynamic_range": "SDR", "bt_init_range": "0-740", "bt_index_range": "741-1228", **kw}


def af(itag, codec, ext="m4a", tbr=130.0, lang="en", pref=10, **kw):
    return {"format_id": str(itag), "url": GV.format(itag=itag), "protocol": "https", "ext": ext,
            "vcodec": "none", "acodec": codec, "asr": 48000 if codec == "opus" else 44100, "audio_channels": 2,
            "tbr": tbr, "language": lang, "language_preference": pref,
            "bt_init_range": "0-722", "bt_index_range": "723-1018", **kw}


@pytest.fixture
def info():
    return {"id": "dQw4w9WgXcQ", "duration": 212, "formats": [
        {"format_id": "233", "protocol": "m3u8_native", "url": "https://manifest.googlevideo.com/x",
         "vcodec": "none", "acodec": "mp4a.40.5"},
        af(139, "mp4a.40.5", tbr=48.0), af(140, "mp4a.40.2"), af(251, "opus", ext="webm"),
        af("140-drc", "mp4a.40.2"),
        af(140, "mp4a.40.2", lang="de", pref=-1) | {"format_id": "140-de"},
        vf(160, "avc1.4D400C", 144, tbr=100), vf(134, "avc1.4D401E", 360, tbr=400),
        vf(136, "avc1.4D401F", 720, tbr=1500), vf(137, "avc1.640028", 1080, tbr=3000),
        vf(248, "vp9", 1080, ext="webm"), vf(271, "vp9", 1440, ext="webm"),
        vf(399, "av01.0.08M.08", 1080), vf(337, "vp09.00.51.10", 1080, ext="webm", dynamic_range="HDR10"),
        vf(299, "avc1.64002a", 1080, fps=60, tbr=5000, has_drm=True),
        vf(135, "avc1.4D401E", 480) | {"bt_init_range": None},  # no ranges -> dropped
        {"format_id": "18", "url": GV.format(itag=18), "protocol": "https", "ext": "mp4", "vcodec": "avc1.42001E",
         "acodec": "mp4a.40.2", "height": 360, "tbr": 500},
    ]}


def _parse(mpd):
    return ET.fromstring(mpd.split("\n", 1)[1])


def test_mpd_avc_default(info):
    mpd = streams.build_mpd(info, 1080, "avc1", url_for=lambda f: f"/p/{f['format_id']}")
    root = _parse(mpd)
    assert root.get("type") == "static"
    assert root.get("mediaPresentationDuration") == "PT212.091S"
    sets = root.findall("d:Period/d:AdaptationSet", NS)
    video = [a for a in sets if a.get("contentType") == "video"]
    audio = [a for a in sets if a.get("contentType") == "audio"]
    assert len(video) == 1 and video[0].get("mimeType") == "video/mp4"
    ids = [r.get("id") for r in video[0].findall("d:Representation", NS)]
    assert ids == ["160", "134", "136", "137"]  # sorted, no drm/hdr/vp9/av1, 135 lacks ranges
    r137 = video[0].find("d:Representation[@id='137']", NS)
    assert r137.get("codecs") == "avc1.640028" and r137.get("bandwidth") == "3000000"
    assert r137.find("d:BaseURL", NS).text == "/p/137"
    sb = r137.find("d:SegmentBase", NS)
    assert sb.get("indexRange") == "741-1228"
    assert sb.find("d:Initialization", NS).get("range") == "0-740"
    # audio: original language first (main), mp4a preferred for avc, drc excluded
    assert [a.get("lang") for a in audio] == ["en", "de"]
    assert audio[0].find("d:Role", NS).get("value") == "main"
    en_ids = [r.get("id") for r in audio[0].findall("d:Representation", NS)]
    assert en_ids == ["140"]  # HE-AAC (139, mp4a.40.5) never mixed with AAC-LC in one set
    assert audio[0].find("d:Representation/d:AudioChannelConfiguration", NS).get("value") == "2"


def test_select_audio_single_codec_and_stereo():
    fmts = [af(139, "mp4a.40.5", tbr=48.0), af(140, "mp4a.40.2", tbr=130.0),
            af(256, "mp4a.40.5", tbr=192.0, audio_channels=6), af(258, "mp4a.40.2", tbr=384.0, audio_channels=6),
            af(250, "opus", ext="webm", tbr=70.0), af(251, "opus", ext="webm", tbr=140.0)]
    a = streams.select_audio({"formats": fmts}, "avc1")
    assert [f["format_id"] for f in a["en"]] == ["140"]
    a = streams.select_audio({"formats": fmts}, "vp9")
    assert [f["format_id"] for f in a["en"]] == ["250", "251"]
    only_he = streams.select_audio({"formats": [af(139, "mp4a.40.5")]}, "avc1")
    assert [f["format_id"] for f in only_he["en"]] == ["139"]


def test_mpd_vp9_and_max_height(info):
    root = _parse(streams.build_mpd(info, 720, "vp9", url_for=lambda f: "x"))
    v = root.find("d:Period/d:AdaptationSet[@contentType='video']", NS)
    # no vp9 <= 720 in fixture -> falls back to avc1 capped at 720
    assert [r.get("height") for r in v.findall("d:Representation", NS)] == ["144", "360", "720"]
    root = _parse(streams.build_mpd(info, 1440, "vp9", url_for=lambda f: "x"))
    v = root.find("d:Period/d:AdaptationSet[@contentType='video']", NS)
    assert v.get("mimeType") == "video/webm"
    assert [r.get("id") for r in v.findall("d:Representation", NS)] == ["248", "271"]
    a = root.find("d:Period/d:AdaptationSet[@contentType='audio']", NS)
    assert a.find("d:Representation", NS).get("codecs") == "opus"


def test_mpd_default_signed_urls(info):
    root = _parse(streams.build_mpd(info))
    url = root.find(".//d:BaseURL", NS).text
    assert url.startswith("/api/proxy/")
    assert streams.verify_token(url.removeprefix("/api/proxy/"))[0].startswith("https://rr1---sn-abc.googlevideo.com/")


def test_mpd_probe_fills_ranges(info):
    calls = []

    def probe(f):
        calls.append(f["format_id"])
        f["bt_init_range"], f["bt_index_range"] = "0-9", "10-99"
        return True

    root = _parse(streams.build_mpd(info, 1080, "avc1", url_for=lambda f: "x", probe=probe))
    assert calls == ["135"]
    v = root.find("d:Period/d:AdaptationSet[@contentType='video']", NS)
    assert "135" in [r.get("id") for r in v.findall("d:Representation", NS)]


def test_mpd_does_not_mutate_info(info):
    import copy
    before = copy.deepcopy(info)
    streams.build_mpd(info, 1080, "avc1", url_for=lambda f: "x",
                      probe=lambda f: f.update(bt_init_range="0-1", bt_index_range="2-3") or True)
    assert info == before


def test_probe_parallel_deadline(info, monkeypatch):
    import time
    monkeypatch.setattr(streams, "PROBE_TOTAL_S", 0.3)
    for f in info["formats"]:
        f.pop("bt_init_range", None)
    t = time.monotonic()
    with pytest.raises(ValueError):  # all probes too slow -> nothing usable
        streams.build_mpd(info, 1080, "avc1", url_for=lambda f: "x", probe=lambda f: time.sleep(2) or False)
    assert time.monotonic() - t < 1.5


def test_probe_ranges_redirect_allowlist_and_log(monkeypatch):
    data = _box(b"ftyp", b"\0" * 16) + _box(b"moov", b"\0" * 100) + _box(b"sidx", b"\0" * 40)
    hits, logged = [], []

    def handler(req):
        hits.append(req.url.host)
        if req.url.host == "a.googlevideo.com":
            return httpx.Response(302, headers={"location": "https://b.googlevideo.com/v?x=1"})
        if req.url.host == "b.googlevideo.com":
            return httpx.Response(206, content=data)
        return httpx.Response(302, headers={"location": "https://evil.example.com/"})

    real = httpx.Client
    monkeypatch.setattr(streams.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    import bt.db
    monkeypatch.setattr(bt.db, "log_yt_request", lambda *a: logged.append(a))
    streams._probe_cache.clear()
    f = {"format_id": "1", "ext": "mp4", "url": "https://a.googlevideo.com/v?itag=1&clen=5"}
    assert streams.probe_ranges(f) and f["bt_init_range"] == "0-131" and f["bt_index_range"] == "132-179"
    assert logged and logged[0][0] == "probe" and logged[0][2] is True
    g = {"format_id": "2", "ext": "mp4", "url": "https://c.googlevideo.com/v?itag=2&clen=5"}
    assert streams.probe_ranges(g) is False
    assert "evil.example.com" not in hits
    n = len(hits)
    assert streams.probe_ranges(dict(g)) is False and len(hits) > n  # misses are not cached (may be transient)


def test_mpd_validate_drops_unreachable(info):
    root = _parse(streams.build_mpd(info, 1080, "avc1", url_for=lambda f: "x",
                                    validate=lambda f: f["format_id"] not in ("160", "140-de")))
    ids = [r.get("id") for r in root.iter("{urn:mpeg:dash:schema:mpd:2011}Representation")]
    assert "160" not in ids and "137" in ids and "140" in ids


def test_mpd_no_formats():
    with pytest.raises(ValueError):
        streams.build_mpd({"formats": []})


def test_best_progressive(info):
    assert streams.best_progressive(info)["format_id"] == "18"


def test_token_roundtrip_and_tamper():
    u = "https://rr3---sn-x.googlevideo.com/videoplayback?a=1"
    t = streams.sign_url(u, "UA/1")
    assert streams.verify_token(t) == (u, "UA/1")
    assert streams.verify_token(streams.sign_url(u))[1] == streams.DEFAULT_UA
    with pytest.raises(ValueError):
        streams.verify_token(t[:-2] + ("AA" if not t.endswith("AA") else "BB"))
    with pytest.raises(ValueError):
        streams.verify_token(t, max_age=-1)


@pytest.mark.parametrize("url,ok", [
    ("https://rr1---sn-abc.googlevideo.com/videoplayback", True),
    ("https://www.youtube.com/api/timedtext", True),
    ("https://youtube.com/x", True),
    ("http://rr1.googlevideo.com/x", False),
    ("https://evil.com/?x=googlevideo.com", False),
    ("https://googlevideo.com.evil.com/x", False),
    ("https://evilgooglevideo.com/x", False),
    ("https://user:pw@rr1.googlevideo.com/x", False),
    ("file:///etc/passwd", False),
])
def test_host_allowlist(url, ok):
    assert streams.host_allowed(url) is ok
    if not ok:
        with pytest.raises(ValueError):
            streams.sign_url(url)


def test_forged_token_for_bad_host_rejected():
    forged = streams._ser.dumps({"u": "https://example.com/secret"})
    with pytest.raises(ValueError):
        streams.verify_token(forged)


def _box(typ, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def test_parse_mp4_ranges():
    data = _box(b"ftyp", b"dash" + b"\0" * 12) + _box(b"moov", b"\0" * 700) + _box(b"sidx", b"\0" * 480) \
        + _box(b"moof", b"\0" * 10)
    init_end = 24 + 708 - 1
    assert streams.parse_mp4_ranges(data) == (f"0-{init_end}", f"{init_end + 1}-{init_end + 488}")
    assert streams.parse_mp4_ranges(_box(b"ftyp") + _box(b"moov")) is None


def test_proxy_bad_token_403():
    from fastapi.testclient import TestClient
    from bt.main import app
    with TestClient(app) as c:
        assert c.get("/api/proxy/garbage").status_code == 403


# ---------------------------------------------------------------- extras

def test_sponsorblock_filter_and_mock(monkeypatch):
    vid = "dQw4w9WgXcQ"
    payload = [
        {"videoID": "zzzzzzzzzzz", "segments": [{"segment": [1, 2], "category": "sponsor", "UUID": "x"}]},
        {"videoID": vid, "segments": [
            {"segment": [30.5, 60], "category": "sponsor", "actionType": "skip", "UUID": "b"},
            {"segment": [0, 5], "category": "intro", "actionType": "skip", "UUID": "a"},
            {"segment": "bad"},
        ]},
    ]
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(extras.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    extras._cache.clear()
    segs = asyncio.run(extras.sponsorblock(vid, ["sponsor", "intro"]))
    assert [s["UUID"] for s in segs] == ["a", "b"]
    assert segs[1]["segment"] == [30.5, 60.0]
    import hashlib
    assert f"/skipSegments/{hashlib.sha256(vid.encode()).hexdigest()[:4]}" in seen["url"]
    assert vid not in seen["url"]  # k-anonymous

    def fail(req):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(extras.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(fail), **kw))
    extras._cache.clear()
    assert asyncio.run(extras.sponsorblock(vid, ["sponsor"])) == []
    assert asyncio.run(extras.dislikes(vid)) is None
    assert asyncio.run(extras.dearrow(vid)) is None


def test_fetch_image_redirect_allowlist(monkeypatch):
    def handler(req):
        if req.url.host == "i.ytimg.com":
            return httpx.Response(302, headers={"location": "https://evil.example.com/x.jpg"})
        return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})

    real = httpx.AsyncClient
    monkeypatch.setattr(extras.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(extras._fetch_image("https://i.ytimg.com/vi/x/hqdefault.jpg")) is None
    assert asyncio.run(extras._fetch_image("https://evil.example.com/x.jpg")) is None


def test_cached_image_lock_cleanup(tmp_path, monkeypatch):
    async def none(url):
        return None
    monkeypatch.setattr(extras, "_fetch_image", none)
    p = tmp_path / "x.jpg"
    assert asyncio.run(extras.cached_image(p, ["https://i.ytimg.com/a.jpg"])) is None
    assert str(p) not in extras._img_locks


def test_avatar_validation_and_negative_cache(monkeypatch):
    from fastapi.testclient import TestClient
    from bt import ytdl
    from bt.main import app
    calls = []

    def fake_channel(ref, n=30, origin="worker"):
        calls.append(ref)
        raise ytdl.YtUnavailable("This channel does not exist")

    monkeypatch.setattr(ytdl, "channel_videos", fake_channel)
    cid = "UC" + "z" * 22
    extras.avatar_miss_path(cid).unlink(missing_ok=True)
    with TestClient(app) as c:
        assert c.get("/api/channel/notachannel/avatar").status_code == 404
        assert c.get(f"/api/channel/{cid}/avatar").status_code == 404
        assert c.get(f"/api/channel/{cid}/avatar").status_code == 404
    assert calls == [cid]  # second request served from the negative cache
    extras.avatar_miss_path(cid).unlink(missing_ok=True)


def test_dearrow_pick():
    d = {"titles": [{"title": "Orig", "original": True, "votes": 5},
                    {"title": ">Better Title", "original": False, "votes": 1, "locked": False}],
         "thumbnails": [{"timestamp": 12.5, "original": False, "votes": 0}]}
    b = extras.pick_branding(d, "dQw4w9WgXcQ")
    assert b["title"] == "Better Title"
    assert "videoID=dQw4w9WgXcQ" in b["thumb"] and "time=12.5" in b["thumb"]
    assert extras.pick_branding({"titles": [], "thumbnails": []}, "dQw4w9WgXcQ") is None


# ---------------------------------------------------------------- live

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("BT_LIVE") != "1", reason="set BT_LIVE=1")
def test_live_search_mpd_proxy():
    from bt import ytdl
    res = ytdl.search("rick astley never gonna give you up", n=3, origin="user")
    assert res and all(r["id"] for r in res)
    vid = "jNQXAC9IVRw"
    info = ytdl.video_info(vid, origin="user")
    vids = streams.video_candidates(info)
    assert vids and sum(1 for f in vids if f.get("bt_init_range")) >= len(vids) // 2, "range hook broken"
    try:
        mpd = streams.build_mpd(info, 720, "avc1", probe=streams.probe_ranges, validate=streams.check_stream)
    except ValueError:  # same retry as the manifest endpoint
        ytdl.cache_drop(vid)
        info = ytdl.video_info(vid, origin="user")
        mpd = streams.build_mpd(info, 720, "avc1", probe=streams.probe_ranges, validate=streams.check_stream)
    root = _parse(mpd)
    url = root.find(".//d:AdaptationSet[@contentType='video']/d:Representation/d:BaseURL", NS).text
    token = url.removeprefix("/api/proxy/")

    async def first_kb():
        resp = await streams.proxy(token, "bytes=0-1023")
        chunks = b""
        async for c in resp.body_iterator:
            chunks += c
        return resp, chunks

    resp, body = asyncio.run(first_kb())
    assert resp.status_code == 206, resp.status_code
    assert len(body) == 1024
    assert body[4:8] == b"ftyp"
    assert resp.headers["content-range"].startswith("bytes 0-1023/")


# ---------------------------------------------------------------- freshly minted stream URLs

def test_failed_check_is_not_cached_and_fresh_info_waits(monkeypatch):
    """googlevideo refuses a just-minted URL for a few seconds: a failed check must be retried later."""
    answers = iter([None, b"x"])
    monkeypatch.setattr(streams, "_get_head", lambda *a: next(answers))
    monkeypatch.setattr("bt.db.log_yt_request", lambda *a, **k: None)
    f = {"url": "https://rr1.googlevideo.com/videoplayback?itag=137&fresh=1", "format_id": "137"}
    assert streams.check_stream(f) is False
    assert streams.check_stream(f) is True  # asked again, not answered from a cached failure
    assert streams.check_stream(f) is True
    import time
    assert 9 < streams.settle_wait({"epoch": time.time()}) <= streams.URL_SETTLE_S
    assert streams.settle_wait({"epoch": time.time() - 60}) == 0


def test_manifest_waits_out_fresh_urls_before_giving_up(monkeypatch):
    from bt.api import video
    import time
    calls, slept, dropped = [], [], []
    fresh = {"id": "dQw4w9WgXcQ", "epoch": time.time()}

    def build(info, *a):
        calls.append(info)
        if len(calls) == 1:
            raise ValueError("no DASH formats with byte ranges")
        return "<?xml?>\n<MPD/>"

    async def nap(s):
        slept.append(s)

    async def yt(fn, *a):
        return fresh
    monkeypatch.setattr(video.streams, "build_mpd", build)
    monkeypatch.setattr(video.asyncio, "sleep", nap)
    monkeypatch.setattr(video, "_yt", yt)
    monkeypatch.setattr(video.ytdl, "cache_drop", dropped.append)
    monkeypatch.setattr(video.ytdl, "_is_live", lambda i: False)

    async def prefs(*a):
        return {}
    monkeypatch.setattr(video, "_db", prefs)
    r = asyncio.run(video.manifest("dQw4w9WgXcQ", None, None, pid=1))
    assert r.status_code == 200 and len(calls) == 2 and calls[1] is fresh
    assert dropped == [] and slept and slept[0] > 9  # same URLs, after they settled
