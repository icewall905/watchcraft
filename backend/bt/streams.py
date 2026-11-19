"""DASH MPD generation from yt-dlp adaptive formats + signed, allow-listed range proxy to googlevideo.

MPD: static, SegmentBase (initRange/indexRange; captured from the player response by the hook in ytdl.py,
or probed from the mp4 box layout as a fallback). Every media URL is replaced by `/api/proxy/{token}` where
token = itsdangerous-signed {u: url, a: user-agent}, valid ~6h, only for googlevideo.com / youtube.com hosts.
"""
from __future__ import annotations

import asyncio
import logging
import re
import struct
import time
import urllib.parse as up
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Callable, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import SECRET

log = logging.getLogger("bt.streams")

TOKEN_MAX_AGE_S = 6 * 3600
PROXY_PREFIX = "/api/proxy/"
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
ALLOWED_SUFFIXES = ("googlevideo.com", "youtube.com")
CODEC_ORDER = ("avc1", "vp9", "av01")

_ser = URLSafeTimedSerializer(SECRET, salt="bt-proxy")


# ---------------------------------------------------------------- tokens

def host_allowed(url: str) -> bool:
    try:
        p = up.urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower().rstrip(".")
    if p.scheme != "https" or not host or p.username or p.password:
        return False
    return any(host == s or host.endswith("." + s) for s in ALLOWED_SUFFIXES)


def sign_url(url: str, user_agent: str | None = None) -> str:
    if not host_allowed(url):
        raise ValueError(f"host not allowed: {up.urlparse(url).hostname}")
    d = {"u": url}
    if user_agent and user_agent != DEFAULT_UA:
        d["a"] = user_agent
    return _ser.dumps(d)


def verify_token(token: str, max_age: int = TOKEN_MAX_AGE_S) -> tuple[str, str]:
    """-> (url, user_agent). Raises ValueError on bad/expired token or disallowed host."""
    try:
        d = _ser.loads(token, max_age=max_age)
    except SignatureExpired as e:
        raise ValueError("token expired") from e
    except BadSignature as e:
        raise ValueError("bad token") from e
    url = d.get("u") if isinstance(d, dict) else None
    if not isinstance(url, str) or not host_allowed(url):
        raise ValueError("host not allowed")
    return url, d.get("a") or DEFAULT_UA


def proxy_url(fmt: dict, prefix: str = PROXY_PREFIX) -> str:
    return prefix + sign_url(fmt["url"], (fmt.get("http_headers") or {}).get("User-Agent"))


# ---------------------------------------------------------------- format selection

def _vfamily(vcodec: str) -> str:
    v = (vcodec or "").lower()
    if v.startswith("avc"):
        return "avc1"
    if v.startswith(("vp9", "vp09")):
        return "vp9"
    if v.startswith("av01"):
        return "av01"
    return ""


def _afamily(acodec: str) -> str:
    a = (acodec or "").lower()
    if a.startswith("mp4a"):
        return "mp4a"
    if a.startswith("opus"):
        return "opus"
    return ""


def _usable(f: dict) -> bool:
    note = (f.get("format_note") or "").lower()
    return (f.get("protocol") == "https" and bool(f.get("url")) and not f.get("has_drm")
            and "premium" not in note and "upscaled" not in note and host_allowed(f.get("url") or ""))


def _has_ranges(f: dict) -> bool:
    return bool(f.get("bt_init_range") and f.get("bt_index_range"))


def video_candidates(info: dict) -> list[dict]:
    return [f for f in info.get("formats") or []
            if _usable(f) and f.get("acodec") in ("none", None) and _vfamily(f.get("vcodec")) and f.get("height")
            and (f.get("dynamic_range") or "SDR") == "SDR"]


def audio_candidates(info: dict) -> list[dict]:
    return [f for f in info.get("formats") or []
            if _usable(f) and f.get("vcodec") in ("none", None) and _afamily(f.get("acodec"))
            and "drc" not in (f.get("format_id") or "").lower()]


def select_video(info: dict, max_height: int = 1080, prefer_codec: str = "avc1") -> tuple[str, list[dict]]:
    """One codec family + one container (MSE can't switch codecs inside an AdaptationSet reliably)."""
    cands = [f for f in video_candidates(info) if f["height"] <= (max_height or 10_000)]
    if not cands:  # nothing small enough: take the smallest available
        allc = video_candidates(info)
        if allc:
            mh = min(f["height"] for f in allc)
            cands = [f for f in allc if f["height"] == mh]
    order = [prefer_codec] + [c for c in CODEC_ORDER if c != prefer_codec]
    for fam in order:
        fs = [f for f in cands if _vfamily(f["vcodec"]) == fam]
        if not fs:
            continue
        exts: dict[str, int] = {}
        for f in fs:
            exts[f.get("ext")] = exts.get(f.get("ext"), 0) + 1
        ext = max(exts, key=lambda e: (exts[e], e == "mp4"))
        best: dict[tuple, dict] = {}
        for f in fs:
            if f.get("ext") != ext:
                continue
            k = (f["height"], f.get("fps") or 0)
            if k not in best or (f.get("tbr") or 0) > (best[k].get("tbr") or 0):
                best[k] = f
        return fam, sorted(best.values(), key=lambda f: (f["height"], f.get("fps") or 0))
    return "", []


def _abr(f: dict) -> float:
    return f.get("tbr") or f.get("abr") or 0


def select_audio(info: dict, video_family: str = "avc1") -> dict[str, list[dict]]:
    """-> {lang: [formats]}: one exact codec string + channel count per language.

    Mixing HE-AAC (mp4a.40.5, 22.05 kHz core + SBR) and AAC-LC (mp4a.40.2, 44.1 kHz) in one
    AdaptationSet makes ABR reconfigure the audio decoder mid-stream (sample-rate change), a known
    source of MSE decode errors/stalls; likewise 5.1 vs stereo. Keep only the group (codec, channels)
    with the best stereo bitrate."""
    pref = ["mp4a", "opus"] if video_family in ("avc1", "") else ["opus", "mp4a"]
    by_lang: dict[str, list[dict]] = {}
    for f in audio_candidates(info):
        by_lang.setdefault(f.get("language") or "und", []).append(f)
    out = {}
    for lang, fs in by_lang.items():
        for fam in pref:
            sel = [f for f in fs if _afamily(f["acodec"]) == fam]
            if sel:
                groups: dict[tuple, list[dict]] = {}
                for f in sel:
                    groups.setdefault(((f.get("acodec") or "").lower(), int(f.get("audio_channels") or 2)),
                                      []).append(f)
                best = max(groups, key=lambda k: (k[1] == 2, max(_abr(f) for f in groups[k])))
                out[lang] = sorted(groups[best], key=_abr)
                break
    return out


def best_progressive(info: dict) -> Optional[dict]:
    fs = [f for f in info.get("formats") or [] if _usable(f) and f.get("vcodec") not in ("none", None)
          and f.get("acodec") not in ("none", None)]
    return max(fs, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0)) if fs else None


# ---------------------------------------------------------------- range probing fallback (mp4)

def parse_mp4_ranges(data: bytes) -> tuple[str, str] | None:
    """Find moov (init) and sidx (index) in the first bytes of a fragmented mp4."""
    pos, moov_end = 0, None
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1 and pos + 16 <= len(data):
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        if size < 8:
            return None
        if typ == b"moov":
            moov_end = pos + size
        elif typ == b"sidx":
            if moov_end is None:
                return None
            return f"0-{moov_end - 1}", f"{pos}-{pos + size - 1}"
        pos += size
    return None


_probe_cache: dict[str, tuple[str, str] | None] = {}  # "itag:clen:lmt" -> ranges (None = probe failed)
PROBE_TIMEOUT_S = 5.0
PROBE_TOTAL_S = 12.0
PROBE_WORKERS = 6


def _probe_key(f: dict) -> str:
    q = up.parse_qs(up.urlparse(f.get("url") or "").query)
    return ":".join(q.get(k, [""])[0] for k in ("id", "itag", "clen", "lmt"))


def _get_head(url: str, ua: str, nbytes: int, timeout: float) -> bytes | None:
    """Blocking ranged GET following redirects only to allowed hosts."""
    headers = {"User-Agent": ua, "Range": f"bytes=0-{nbytes - 1}", "Accept-Encoding": "identity"}
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        for _ in range(5):
            if not host_allowed(url):
                return None
            with c.stream("GET", url, headers=headers) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = up.urljoin(url, r.headers["location"])
                    continue
                if r.status_code not in (200, 206):
                    return None
                buf = b""
                for chunk in r.iter_bytes():  # upstream may ignore Range: never read the whole file
                    buf += chunk
                    if len(buf) >= nbytes:
                        break
                return buf[:nbytes]
    return None


def probe_ranges(f: dict, timeout: float = PROBE_TIMEOUT_S, origin: str = "user") -> bool:
    """Blocking. Fetch the head of an mp4 format and set bt_init_range/bt_index_range on `f`
    (callers pass a copy, never the shared cached info). Results cached per stream. True on success."""
    if _has_ranges(f):
        return True
    if f.get("ext") not in ("mp4", "m4a"):
        return False
    key = _probe_key(f)
    if key in _probe_cache:
        rng = _probe_cache[key]
    else:
        from .db import log_yt_request  # local import keeps streams importable without DB side effects
        ua = (f.get("http_headers") or {}).get("User-Agent") or DEFAULT_UA
        try:
            data = _get_head(f["url"], ua, 65536, timeout)
            rng = parse_mp4_ranges(data) if data else None
            log_yt_request("probe", origin, rng is not None, f"probe itag {f.get('format_id')}")
        except httpx.HTTPError as e:
            log.info("probe %s failed: %s", f.get("format_id"), e)
            log_yt_request("probe", origin, False, f"probe itag {f.get('format_id')}: {type(e).__name__}")
            rng = None
        if len(_probe_cache) > 2000:
            _probe_cache.clear()
        if rng:  # failures are not cached (could be transient)
            _probe_cache[key] = rng
    if rng:
        f["bt_init_range"], f["bt_index_range"] = rng
    return bool(rng)


_check_cache: dict[str, bool] = {}
# googlevideo answers 403 for the first seconds after a stream URL is minted (seen on prod 2026-11-18: TVHTML5
# URLs 403 at 0-4 s, 206 from ~8 s). A check that fails inside this window says nothing about the stream.
URL_SETTLE_S = 10.0


def settle_wait(info: dict) -> float:
    """Seconds until `info`'s stream URLs are old enough to be served (0 when they already are)."""
    return max(0.0, URL_SETTLE_S - (time.time() - float(info.get("epoch") or 0)))


def check_stream(f: dict, timeout: float = PROBE_TIMEOUT_S, origin: str = "user") -> bool:
    """Blocking. 1-byte ranged GET to verify googlevideo actually serves this format (some clients'
    formats 403 without a PO token, e.g. 144p on TVHTML5). Successes are cached per stream URL; failures
    are not, since a freshly minted URL is refused for a few seconds (URL_SETTLE_S)."""
    key = f.get("url") or ""
    if key in _check_cache:
        return _check_cache[key]
    from .db import log_yt_request
    ua = (f.get("http_headers") or {}).get("User-Agent") or DEFAULT_UA
    try:
        ok = _get_head(key, ua, 1, timeout) is not None
    except httpx.HTTPError:
        ok = False
    log_yt_request("probe", origin, ok, f"check itag {f.get('format_id')}")
    if len(_check_cache) > 5000:
        _check_cache.clear()
    if ok:
        _check_cache[key] = ok
    return ok


def _run_all(fmts: list[dict], fn: Callable[[dict], bool]) -> set[int]:
    """Run fn over fmts in parallel with an overall deadline. -> ids (id()) of formats where fn returned
    False. Unfinished calls count as neither (caller decides)."""
    if not fmts:
        return set()
    ex = ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(fmts)))
    try:
        futs = {ex.submit(fn, f): f for f in fmts}
        done, _ = wait(futs, timeout=PROBE_TOTAL_S)
        return {id(futs[x]) for x in done if x.exception() is not None or not x.result()}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------- MPD

def _duration(info: dict, fmts: list[dict]) -> float:
    durs = []
    for f in fmts:
        q = up.parse_qs(up.urlparse(f.get("url") or "").query)
        try:
            durs.append(float(q["dur"][0]))
        except (KeyError, ValueError, IndexError):
            pass
    return max(durs) if durs else float(info.get("duration") or 0)


def _bandwidth(f: dict, dur: float) -> int:
    if f.get("tbr"):
        return int(f["tbr"] * 1000)
    size = f.get("filesize") or f.get("filesize_approx")
    if size and dur:
        return int(size * 8 / dur)
    return 1_000_000 if f.get("vcodec") not in ("none", None) else 128_000


def _mime(f: dict) -> str:
    kind = "video" if f.get("vcodec") not in ("none", None) else "audio"
    ext = f.get("ext")
    return f"{kind}/{'webm' if ext == 'webm' else 'mp4'}"


def _codec(f: dict) -> str:
    c = f.get("vcodec") if f.get("vcodec") not in ("none", None) else f.get("acodec")
    return c or ""


def _rep(parent, f: dict, dur: float, url_for: Callable[[dict], str]) -> ET.Element:
    rep = ET.SubElement(parent, "Representation", id=str(f["format_id"]), bandwidth=str(_bandwidth(f, dur)),
                        codecs=_codec(f), mimeType=_mime(f))
    if f.get("vcodec") not in ("none", None):
        rep.set("width", str(f.get("width") or 0))
        rep.set("height", str(f.get("height") or 0))
        if f.get("fps"):
            rep.set("frameRate", str(int(f["fps"])))
        rep.set("sar", "1:1")
    else:
        if f.get("asr"):
            rep.set("audioSamplingRate", str(int(f["asr"])))
        ET.SubElement(rep, "AudioChannelConfiguration",
                      schemeIdUri="urn:mpeg:dash:23003:3:audio_channel_configuration:2011",
                      value=str(int(f.get("audio_channels") or 2)))
    ET.SubElement(rep, "BaseURL").text = url_for(f)
    sb = ET.SubElement(rep, "SegmentBase", indexRange=f["bt_index_range"], indexRangeExact="true")
    ET.SubElement(sb, "Initialization", range=f["bt_init_range"])
    return rep


def build_mpd(info: dict, max_height: int = 1080, prefer_codec: str = "avc1",
              url_for: Callable[[dict], str] = proxy_url, probe: Callable[[dict], bool] | None = None,
              validate: Callable[[dict], bool] | None = None) -> str:
    """Build a static DASH MPD. `probe` (blocking, run in parallel with an overall deadline) fills missing
    byte ranges on copies of the formats; formats still lacking ranges are dropped. `validate` (blocking,
    parallel) drops formats it reports as unreachable. Raises ValueError if no playable video or audio remains."""
    fam, vids = select_video(info, max_height, prefer_codec)
    auds = select_audio(info, fam)
    # work on copies: `info` is the shared 4h cache entry and must not be mutated
    vids = [dict(f) for f in vids]
    auds = {lang: [dict(f) for f in fs] for lang, fs in auds.items()}
    if probe:
        _run_all([f for f in vids + [a for fs in auds.values() for a in fs] if not _has_ranges(f)], probe)
    if validate and auds:
        # drop formats googlevideo refuses. Checks every video rep but only the original-language audio
        # (videos can carry ~50 dubs; checking all would cost ~100 requests per manifest).
        main = max(auds, key=lambda lang: max(f.get("language_preference") or -1 for f in auds[lang]))
        bad = _run_all([f for f in vids + auds[main] if _has_ranges(f)], validate)
        vids = [f for f in vids if id(f) not in bad]
        auds = {lang: [f for f in fs if id(f) not in bad] for lang, fs in auds.items()}
    vids = [f for f in vids if _has_ranges(f)]
    auds = {lang: [f for f in fs if _has_ranges(f)] for lang, fs in auds.items()}
    auds = {lang: fs for lang, fs in auds.items() if fs}
    if not vids or not auds:
        raise ValueError("no DASH formats with byte ranges")
    all_f = vids + [a for fs in auds.values() for a in fs]
    dur = _duration(info, all_f)

    ET.register_namespace("", "urn:mpeg:dash:schema:mpd:2011")
    mpd = ET.Element("MPD", {
        "xmlns": "urn:mpeg:dash:schema:mpd:2011", "type": "static",
        "profiles": "urn:mpeg:dash:profile:isoff-on-demand:2011", "minBufferTime": "PT1.500S",
        "mediaPresentationDuration": f"PT{dur:.3f}S",
    })
    period = ET.SubElement(mpd, "Period", id="0", start="PT0S", duration=f"PT{dur:.3f}S")
    aid = 0
    vset = ET.SubElement(period, "AdaptationSet", id=str(aid), contentType="video", mimeType=_mime(vids[0]),
                         subsegmentAlignment="true", subsegmentStartsWithSAP="1",
                         maxWidth=str(max(f.get("width") or 0 for f in vids)),
                         maxHeight=str(max(f.get("height") or 0 for f in vids)))
    for f in vids:
        _rep(vset, f, dur, url_for)
    # original audio language first, marked main
    langs = sorted(auds, key=lambda lang: -max(f.get("language_preference") or -1 for f in auds[lang]))
    for i, lang in enumerate(langs):
        aid += 1
        fs = auds[lang]
        attrs = {"id": str(aid), "contentType": "audio", "mimeType": _mime(fs[0]),
                 "subsegmentAlignment": "true", "subsegmentStartsWithSAP": "1"}
        if lang != "und":
            attrs["lang"] = lang
        aset = ET.SubElement(period, "AdaptationSet", attrs)
        ET.SubElement(aset, "Role", schemeIdUri="urn:mpeg:dash:role:2011", value="main" if i == 0 else "dub")
        for f in fs:
            _rep(aset, f, dur, url_for)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(mpd, encoding="unicode")


# ---------------------------------------------------------------- proxy

_clients: dict[int, httpx.AsyncClient] = {}
_PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "last-modified", "etag",
                 "content-encoding")
_RANGE_RE = re.compile(r"^bytes=\d*-\d*(,\s*\d*-\d*)*$")


def _client() -> httpx.AsyncClient:
    loop = id(asyncio.get_running_loop())
    c = _clients.get(loop)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=60.0), follow_redirects=False,
                              limits=httpx.Limits(max_connections=64, max_keepalive_connections=16))
        _clients[loop] = c
    return c


async def open_upstream(url: str, ua: str, range_header: str | None) -> httpx.Response:
    """Open a streaming GET, following up to 4 redirects only to allowed hosts."""
    headers = {"User-Agent": ua, "Accept": "*/*", "Accept-Encoding": "identity"}
    if range_header:
        headers["Range"] = range_header
    client = _client()
    for _ in range(5):
        resp = await client.send(client.build_request("GET", url, headers=headers), stream=True)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            await resp.aclose()
            url = up.urljoin(url, resp.headers["location"])
            if not host_allowed(url):
                raise HTTPException(502, "upstream redirect to disallowed host")
            continue
        return resp
    raise HTTPException(502, "too many upstream redirects")


async def proxy(token: str, range_header: str | None) -> Response:
    try:
        url, ua = verify_token(token)
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    if range_header and not _RANGE_RE.match(range_header.strip()):
        range_header = None
    try:
        resp = await open_upstream(url, ua, range_header)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"upstream error: {type(e).__name__}") from e
    if resp.status_code >= 400:
        await resp.aclose()
        # 403 = expired/IP-mismatch/PO-token; client should re-fetch the manifest
        return Response(status_code=502 if resp.status_code >= 500 else resp.status_code,
                        content=f"upstream HTTP {resp.status_code}", media_type="text/plain")
    headers = {k: resp.headers[k] for k in _PASS_HEADERS if k in resp.headers}
    headers.setdefault("accept-ranges", "bytes")
    headers["cache-control"] = "no-store"

    async def body():
        try:
            async for chunk in resp.aiter_raw(64 * 1024):
                yield chunk
        finally:  # also runs when the client disconnects (generator cancelled/closed)
            await resp.aclose()

    return StreamingResponse(body(), status_code=resp.status_code, headers=headers)
