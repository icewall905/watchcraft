"""yt-dlp wrapper (sync). Search/channel listing (flat), full video info (TTL cache), transcript, comments.

All functions are blocking; the API runs them via `run_in_threadpool`, the worker in its own thread.
Every outbound YouTube request is logged via `db.log_yt_request`.
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
import re
import socket
import threading
import time
import urllib.parse as up
from datetime import datetime, timezone
from typing import Any

import yt_dlp

from .config import COOKIES_PATH, POT_URL
from .db import drop_kv, drop_kv_prefix, get_kv, get_settings, log_yt_request, save_setting
from .models import Video, now

log = logging.getLogger("bt.ytdl")

INFO_TTL_S = 4 * 3600
SHORT_MAX_S = 60

_PROXY_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")


def _raw_proxy() -> str:
    """The `throttle.proxy` setting, validated ("" = direct)."""
    raw = str((get_settings().get("throttle") or {}).get("proxy") or "").strip()
    if raw and not re.match(r"(https?|socks[45]h?)://", raw):
        log.warning("ignoring invalid proxy setting %r (expected http(s):// or socks[45]:// URL)", raw)
        return ""
    return raw


def _install_socks_rdns_patch() -> bool:
    """Force local DNS in yt-dlp's SOCKS layer (rdns=False).

    Some SOCKS proxies cannot resolve hostnames themselves — PIA's SOCKS5 answers
    0x04 "Host unreachable" for any hostname. yt-dlp rewrites socks5:// to socks5h://
    ("compat: socks5 was treated as socks5h"), which sends hostnames to the proxy.
    Patching sockssocket.setproxy resolves targets locally and sends IPv4s instead.
    Best effort: if upstream renames things we log and the proxy may misbehave.
    """
    try:
        from yt_dlp.socks import sockssocket
        orig = sockssocket.setproxy
    except Exception as e:
        log.warning("socks rdns patch unavailable (%s)", e)
        return False
    if getattr(orig, "_bt_patched", False):
        return True

    def patched(self, proxytype, addr, port, rdns=True, username=None, password=None):
        return orig(self, proxytype, addr, port, rdns=False, username=username, password=password)

    patched._bt_patched = True  # type: ignore[attr-defined]
    sockssocket.setproxy = patched
    return True


SOCKS_RDNS_PATCH = _install_socks_rdns_patch()


# ---------------------------------------------------------------- socks backend pinning
# A socks hostname may be a pool of backends and some pools contain backends that
# reject valid credentials (PIA: 5 of 30). Resolve, probe briefly and pin a working
# IPv4; PIA's own docs recommend pinning an IP and re-resolving when it breaks.

_PIN_TTL_S = 30 * 60
_pin_lock = threading.Lock()
_pin_cache: dict[str, tuple[float, str]] = {}


def _socks_probe(ip: str, p, timeout: float = 4.0) -> bool:
    try:
        import socks as pys  # PySocks
    except ImportError:
        return False
    s = pys.socksocket()
    s.settimeout(timeout)
    s.set_proxy(pys.SOCKS5, ip, p.port or 1080, rdns=False, username=p.username, password=p.password)
    try:
        s.connect(("8.8.8.8", 443))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _is_ip(host: str | None) -> bool:
    try:
        socket.inet_aton(host or "")
        return True
    except OSError:
        return False


def pinned_proxy_url(url: str) -> str:
    """Rewrite a socks*:// URL so its host is a working IPv4 backend (cached). Non-socks
    URLs, IP hosts and unresolvable/unreachable hosts are returned unchanged."""
    if not url or not up.urlparse(url).scheme.startswith("socks"):
        return url
    p = up.urlparse(url)
    if _is_ip(p.hostname):
        return url
    with _pin_lock:
        hit = _pin_cache.get(url)
        if hit and time.time() < hit[0]:
            return hit[1]
    try:
        ips = sorted({x[4][0] for x in socket.getaddrinfo(p.hostname, p.port or 1080, socket.AF_INET)})
    except OSError as e:
        log.warning("proxy host %r does not resolve: %s", p.hostname, e)
        return url
    out = url
    for ip in ips[:12]:
        if _socks_probe(ip, p):
            creds = f"{p.netloc.rsplit('@', 1)[0]}@" if "@" in p.netloc else ""
            out = up.urlunparse(p._replace(netloc=f"{creds}{ip}:{p.port or 1080}"))
            break
    with _pin_lock:
        _pin_cache[url] = (time.time() + _PIN_TTL_S, out)
    return out


def yt_proxy() -> str:
    """The `throttle.proxy` setting, validated and (for socks) pinned to a working backend.
    Read live so Settings edits apply at once; "" = direct."""
    return pinned_proxy_url(_raw_proxy())


class YtError(Exception):
    pass


class YtBlocked(YtError):
    pass


class YtUnavailable(YtError):
    pass


# ---------------------------------------------------------------- DASH byte ranges hook
# yt-dlp drops `initRange`/`indexRange` from the player response, but DASH SegmentBase needs them.
# Wrap the format extractor: remember ranges per (itag, contentLength) and attach them to every
# yielded format as `bt_init_range` / `bt_index_range` ("start-end" strings).

def attach_ranges(player_responses, items):
    """Generator: pass through yt-dlp's format items, adding byte ranges from the raw player responses."""
    ranges: dict[tuple[str, str], tuple[str, str]] = {}
    try:
        for pr in player_responses or []:
            sd = (pr or {}).get("streamingData") or {}
            for fs in (sd.get("formats") or []) + (sd.get("adaptiveFormats") or []):
                ir, xr = fs.get("initRange"), fs.get("indexRange")
                if ir and xr:
                    ranges[(str(fs.get("itag")), str(fs.get("contentLength")))] = (
                        f"{ir['start']}-{ir['end']}", f"{xr['start']}-{xr['end']}")
    except Exception:  # never break extraction because of our hook
        log.exception("range hook: failed to read player responses")
    for item in items:
        if ranges and isinstance(item, dict) and item.get("format_id") and item.get("url"):
            q = up.parse_qs(up.urlparse(item["url"]).query)
            r = ranges.get((q.get("itag", [""])[0], q.get("clen", [""])[0]))
            if r:
                item["bt_init_range"], item["bt_index_range"] = r
        yield item


def _install_range_hook() -> bool:
    """Best effort: relies on a private yt-dlp method. If upstream renames it we log and fall back to
    streams.probe_ranges (mp4 only) instead of failing at import."""
    try:
        from yt_dlp.extractor.youtube._video import YoutubeIE
        orig = YoutubeIE._extract_formats_and_subtitles
    except Exception as e:  # ImportError / AttributeError after a yt-dlp upgrade
        log.warning("DASH range hook unavailable (%s); falling back to range probing", e)
        return False
    if getattr(orig, "_bt_hooked", False):
        return True

    def patched(self, video_id, player_responses, *a, **kw):
        yield from attach_ranges(player_responses, orig(self, video_id, player_responses, *a, **kw))

    patched._bt_hooked = True  # type: ignore[attr-defined]
    YoutubeIE._extract_formats_and_subtitles = patched
    return True


RANGE_HOOK = _install_range_hook()


# ---------------------------------------------------------------- core helpers

def _opts(**extra) -> dict:
    ea: dict[str, Any] = {"youtubepot-bgutilhttp": {"base_url": [POT_URL]}}
    ea.update(extra.pop("extractor_args", {}))
    o: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
        "socket_timeout": 20, "extractor_retries": 1, "extractor_args": ea,
    }
    if COOKIES_PATH.exists():
        o["cookiefile"] = str(COOKIES_PATH)
    if p := yt_proxy():
        o["proxy"] = p
    o.update(extra)
    return o


_UNAVAILABLE = ("private video", "video unavailable", "has been removed", "members-only", "members only",
                "join this channel", "confirm your age", "age-restricted", "inappropriate for some users",
                "not available", "copyright", "account associated with this video has been terminated",
                "this live event will begin", "premieres in", "does not exist", "no longer available",
                "this channel does not exist", "removed by the uploader", "http error 404",
                "is unavailable")  # checked after _BLOCKED
_TRANSIENT = ("http error 5", "service unavailable", "temporarily", "timed out", "connection reset")
_BLOCKED = ("sign in to confirm", "not a bot", "http error 429", "too many requests", "captcha",
            "http error 403", "unusual traffic", "rate-limit", "rate limit")


def classify(exc: BaseException | str) -> YtError:
    msg = str(exc)
    low = msg.lower()
    if "confirm your age" in low:  # must win over the generic "sign in to confirm" bot check
        return YtUnavailable(msg)
    if any(p in low for p in _BLOCKED):
        return YtBlocked(msg)
    if any(p in low for p in _TRANSIENT):
        return YtError(msg)
    if any(p in low for p in _UNAVAILABLE):
        return YtUnavailable(msg)
    return YtError(msg)


def _extract(url: str, kind: str, origin: str, detail: str, **opts) -> dict:
    t = time.monotonic()
    try:
        with yt_dlp.YoutubeDL(_opts(**opts)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # DownloadError / ExtractorError / network
        err = classify(e)
        log_yt_request(kind, origin, False, f"{detail}: {type(err).__name__}: {str(e)[:300]}")
        raise err from e
    log_yt_request(kind, origin, True, f"{detail} ({time.monotonic() - t:.1f}s)")
    if not info:
        raise YtError(f"no result for {url}")
    return info


def _ts(d: dict) -> datetime | None:
    for k in ("timestamp", "release_timestamp"):
        if isinstance(d.get(k), (int, float)):
            return datetime.fromtimestamp(d[k], timezone.utc)
    if (ud := d.get("upload_date")) and re.fullmatch(r"\d{8}", str(ud)):
        return datetime.strptime(str(ud), "%Y%m%d").replace(tzinfo=timezone.utc)
    pa = d.get("published_at")
    if isinstance(pa, datetime):
        return pa if pa.tzinfo else pa.replace(tzinfo=timezone.utc)
    return None


def _is_live(d: dict) -> bool:
    return bool(d.get("is_live")) or d.get("live_status") in ("is_live", "is_upcoming")


def _entry(e: dict, channel: dict | None = None) -> dict | None:
    vid = e.get("id")
    if not vid or not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(vid)):
        return None  # channels / playlists in search results
    dur = int(e.get("duration") or 0)
    url = e.get("url") or e.get("webpage_url") or ""
    ch = channel or {}
    return {
        "id": vid,
        "title": e.get("title") or "",
        "channel_id": e.get("channel_id") or ch.get("id") or "",
        "channel_name": e.get("channel") or e.get("uploader") or ch.get("name") or "",
        "duration_s": dur,
        "view_count": int(e.get("view_count") or 0),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "published_at": _ts(e),
        "is_short": "/shorts/" in url or (0 < dur <= SHORT_MAX_S),
        "is_live": _is_live(e),
    }


# ---------------------------------------------------------------- public API

def search(query: str, n: int = 15, origin: str = "worker") -> list[dict]:
    n = max(1, min(int(n), 50))
    info = _extract(f"ytsearch{n}:{query}", "search", origin, f"search {query!r}", extract_flat="in_playlist")
    return [x for x in (_entry(e) for e in info.get("entries") or [] if e) if x]


def _channel_url(ref: str) -> str:
    ref = ref.strip()
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", ref):
        return f"https://www.youtube.com/channel/{ref}/videos"
    if ref.startswith("@"):
        return f"https://www.youtube.com/{ref}/videos"
    if ref.startswith(("http://", "https://")) or ref.startswith(("www.", "youtube.com", "m.youtube.com")):
        u = ref if "://" in ref else "https://" + ref
        p = up.urlparse(u)
        if not (p.hostname or "").endswith("youtube.com"):
            raise YtError(f"not a youtube url: {ref}")
        path = p.path.rstrip("/")
        if re.fullmatch(r"/(channel/[^/]+|@[^/]+|c/[^/]+|user/[^/]+)", path):
            path += "/videos"
        return f"https://www.youtube.com{path}"
    return f"https://www.youtube.com/@{ref}/videos"


def _avatar(info: dict) -> str:
    thumbs = info.get("thumbnails") or []
    for t in thumbs:
        if t.get("id") == "avatar_uncropped" and t.get("url"):
            return t["url"]
    sq = [t for t in thumbs if t.get("width") and t.get("width") == t.get("height") and t.get("url")]
    return max(sq, key=lambda t: t["width"])["url"] if sq else ""


def channel_videos(ref: str, n: int = 30, origin: str = "worker") -> tuple[dict, list[dict]]:
    n = max(1, min(int(n), 100))
    info = _extract(_channel_url(ref), "channel", origin, f"channel {ref}", extract_flat="in_playlist",
                    playlistend=n)
    uid = info.get("uploader_id") or ""
    ch = {
        "id": info.get("channel_id") or info.get("id") or "",
        "name": info.get("channel") or info.get("uploader") or (info.get("title") or "").removesuffix(" - Videos"),
        "handle": uid if uid.startswith("@") else "",
        "avatar": _avatar(info),
    }
    entries = [x for x in (_entry(e, ch) for e in list(info.get("entries") or [])[:n] if e) if x]
    return ch, entries


_cache: dict[str, tuple[float, dict]] = {}
_fail: dict[str, tuple[float, YtError]] = {}  # video_id -> (until, error): short negative cache
_cache_lock = threading.Lock()
_id_locks: dict[str, threading.Lock] = {}
FAIL_TTL_S = 60
BLOCKED_TTL_S = 600

# Shared info cache: the in-memory `_cache` lives per process, but the worker (enrich), the API
# (play) and the downloader all extract the same videos. Each extraction is stored in the shared
# KV store so the next process plays it warm — the common case is the worker enriching a candidate
# minutes before the family clicks it. Same TTL as the memory cache.
_INFO_PREFIX = "ytinfo:"
_INFO_BLOB_MAX = 4_000_000  # b64 chars; a pathological info dict is simply not shared


def _db_read_info(video_id: str) -> dict | None:
    """Fresh info dict from the shared store, or None (absent, stale, corrupt)."""
    try:
        row = get_kv(_INFO_PREFIX + video_id)
        gz = row.get("gz") if isinstance(row, dict) else None
        if not gz or time.time() - float(row.get("ts") or 0) >= INFO_TTL_S:
            return None
        info = json.loads(gzip.decompress(base64.b64decode(gz)))
        return info if isinstance(info, dict) and info.get("id") == video_id else None
    except Exception:
        log.debug("shared info read failed for %s", video_id, exc_info=True)
        return None


def _db_write_info(video_id: str, info: dict) -> None:
    """Best-effort share; never let a storage problem fail an extraction."""
    try:
        blob = base64.b64encode(gzip.compress(json.dumps(info, default=str).encode(), 6)).decode()
        if len(blob) > _INFO_BLOB_MAX:
            return
        save_setting(_INFO_PREFIX + video_id, {"ts": time.time(), "gz": blob})
    except Exception:
        log.debug("shared info write failed for %s", video_id, exc_info=True)


def _cached(video_id: str) -> dict | None:
    """Caller holds _cache_lock. Returns info, raises cached failure, or None."""
    t = time.time()
    hit = _cache.get(video_id)
    if hit and t - hit[0] < INFO_TTL_S:
        return hit[1]
    f = _fail.get(video_id)
    if f:
        if t < f[0]:
            raise type(f[1])(str(f[1]))  # fresh instance per caller (thread-safe tracebacks)
        _fail.pop(video_id, None)
    return None


def video_info(video_id: str, origin: str = "worker") -> dict:
    """Full yt-dlp info (formats with bt_init_range/bt_index_range, subtitles, chapters...). Cached 4h
    in memory and in the shared KV store (read across processes: worker enrich warms api plays);
    failures cached 60s (YtBlocked 10 min) so concurrent/repeated callers don't hammer YouTube.
    The returned dict is shared: treat as read-only."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise YtUnavailable(f"bad video id {video_id!r}")
    with _cache_lock:
        if (hit := _cached(video_id)) is not None:
            return hit
        lock = _id_locks.setdefault(video_id, threading.Lock())
    with lock:  # one extraction per id at a time; waiters reuse the result or the failure
        with _cache_lock:
            if (hit := _cached(video_id)) is not None:
                return hit
        shared = _db_read_info(video_id)  # another process (worker/downloader) may have it warm
        if shared is not None:
            with _cache_lock:
                _cache[video_id] = (time.time(), shared)
            return shared
        try:
            info = _extract(f"https://www.youtube.com/watch?v={video_id}", "video", origin, f"video {video_id}")
        except YtError as e:
            with _cache_lock:
                ttl = BLOCKED_TTL_S if isinstance(e, YtBlocked) else FAIL_TTL_S
                _fail[video_id] = (time.time() + ttl, e)
            raise
        else:
            with _cache_lock:
                t = time.time()
                for k in [k for k, (ts, _) in _cache.items() if t - ts >= INFO_TTL_S]:
                    _cache.pop(k, None)
                for k in [k for k, (until, _) in _fail.items() if t >= until]:
                    _fail.pop(k, None)
                _cache[video_id] = (t, info)
            _db_write_info(video_id, info)
            return info
        finally:  # result/failure is cached before the lock is dropped, so no gap for re-extraction
            with _cache_lock:
                if _id_locks.get(video_id) is lock:
                    _id_locks.pop(video_id, None)


def cache_drop(video_id: str) -> None:
    """Forget a cached info (e.g. its stream URLs started returning 403)."""
    with _cache_lock:
        _cache.pop(video_id, None)
    drop_kv(_INFO_PREFIX + video_id)


def cache_clear() -> None:
    with _cache_lock:
        _cache.clear()
        _fail.clear()
    drop_kv_prefix(_INFO_PREFIX)


def cookie_check(origin: str = "user") -> dict:
    """-> {present, logged_in, history_items?, error?}.

    Only a signed-in session gets entries from the watch-history feed: anonymously
    the same page extracts fine but has zero entries. Uses the normal options
    (cookies + proxy if one is set), so it also catches a session that YouTube
    has invalidated and bot-checks raised on the check itself.
    """
    if not COOKIES_PATH.exists():
        return {"present": False, "logged_in": False}
    opts = _opts(extractor_retries=0, socket_timeout=15)
    opts.update(extract_flat="in_playlist", playlistend=3)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/history", download=False)
    except Exception as e:
        err = classify(e)
        log_yt_request("cookie_test", origin, False, f"cookie test: {type(err).__name__}: {str(e)[:200]}")
        out = {"present": True, "logged_in": False, "error": str(e)[:300]}
        if isinstance(err, YtBlocked):
            out["blocked"] = True
        return out
    n = len(info.get("entries") or [])
    log_yt_request("cookie_test", origin, True, f"cookie test: {n} history items")
    return {"present": True, "logged_in": n > 0, "history_items": n}


def upsert_video(s, d: dict) -> Video:
    """Map a yt-dlp info dict or our entry dict onto a Video row (added to session, not committed)."""
    vid = d["id"]
    v = s.get(Video, vid) or Video(id=vid)
    full = "formats" in d or "subtitles" in d
    title = d.get("title") or ""
    if title:
        v.title = title
    v.channel_id = d.get("channel_id") or v.channel_id
    v.channel_name = d.get("channel_name") or d.get("channel") or d.get("uploader") or v.channel_name
    dur = int(d.get("duration_s") or d.get("duration") or 0)
    if dur:
        v.duration_s = dur
    if (pa := _ts(d)) is not None:
        v.published_at = pa
    v.thumbnail = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    vc = int(d.get("view_count") or 0)
    if vc:
        v.view_count = vc
    if d.get("like_count"):
        v.like_count = int(d["like_count"])
    url = d.get("webpage_url") or d.get("url") or ""
    v.is_short = bool(d["is_short"]) if "is_short" in d else ("/shorts/" in url or 0 < v.duration_s <= SHORT_MAX_S)
    v.is_live = _is_live(d)
    if full:
        v.description = d.get("description") or ""
        v.tags = list(d.get("tags") or [])
        v.chapters = [{"start_time": c.get("start_time"), "end_time": c.get("end_time"), "title": c.get("title")}
                      for c in d.get("chapters") or []]
        v.fetched_at = now()
    s.add(v)
    return v


# ---------------------------------------------------------------- captions / transcript

def _lang_match(code: str, want: str) -> bool:
    code, want = code.lower(), want.lower()
    return code == want or code.split("-")[0] == want.split("-")[0]


def caption_tracks(info: dict) -> list[dict]:
    """Manual subs + original-language (non-translated) auto captions.
    -> [{key, lang, name, auto, formats:{ext:url}}]; key is lang for manual, lang+'-auto' for auto."""
    out = []
    for auto, src in ((False, info.get("subtitles") or {}), (True, info.get("automatic_captions") or {})):
        for lang, fmts in src.items():
            if lang == "live_chat":
                continue
            by_ext: dict[str, str] = {}
            name = ""
            for f in fmts or []:
                url = f.get("url") or ""
                if not url or f.get("protocol", "").startswith("m3u8"):
                    continue
                if auto and "tlang" in up.parse_qs(up.urlparse(url).query):
                    continue  # machine translation of the auto track
                by_ext.setdefault(f.get("ext") or "", url)
                name = name or f.get("name") or ""
            if not by_ext and auto:
                # some clients only expose auto captions as an HLS vtt playlist
                hls = [f for f in fmts or [] if f.get("ext") == "vtt" and f.get("protocol", "").startswith("m3u8")]
                if hls and lang.split("-")[0] == (info.get("language") or lang).split("-")[0]:
                    by_ext["m3u8"] = hls[0]["url"]
            if by_ext:
                code = re.sub(r"-[A-Za-z0-9_-]{11}$", "", lang)  # yt-dlp suffixes extra tracks with an id
                out.append({"key": f"{lang}-auto" if auto else lang, "lang": code, "auto": auto,
                            "name": (name or lang) + (" (auto)" if auto else ""), "formats": by_ext})
    return out


def pick_track(info: dict, langs: list[str] | None = None) -> dict | None:
    tracks = caption_tracks(info)
    if not tracks:
        return None
    pref = [x for x in (langs or []) if x] or [info.get("language") or "", "en"]
    pref = [p for p in pref if p]
    for want in pref:  # per preferred language: manual first, then auto
        for auto in (False, True):
            for t in tracks:
                if t["auto"] == auto and _lang_match(t["lang"], want):
                    return t
    for auto in (False, True):  # nothing in preferred langs: original manual, then auto
        for t in tracks:
            if t["auto"] == auto:
                return t
    return None


def _http_get(url: str, kind: str, origin: str) -> bytes:
    """GET through yt-dlp's opener (cookies, headers, proxy settings)."""
    try:
        with yt_dlp.YoutubeDL(_opts()) as ydl:
            data = ydl.urlopen(url).read()
    except Exception as e:
        err = classify(e)
        log_yt_request(kind, origin, False, f"{type(err).__name__}: {str(e)[:300]}")
        raise err from e
    log_yt_request(kind, origin, True, f"{len(data)} bytes")
    return data


def fetch_track(track: dict, origin: str = "worker", kind: str = "transcript") -> tuple[str, str]:
    """-> (format, text) where format is 'json3' or 'vtt'."""
    f = track["formats"]
    if "json3" in f:
        return "json3", _http_get(f["json3"], kind, origin).decode("utf-8", "replace")
    if "vtt" in f:
        return "vtt", _http_get(f["vtt"], kind, origin).decode("utf-8", "replace")
    if "m3u8" in f:
        pl = _http_get(f["m3u8"], kind, origin).decode("utf-8", "replace")
        parts = []
        for line in pl.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                parts.append(_http_get(up.urljoin(f["m3u8"], line), kind, origin).decode("utf-8", "replace"))
        return "vtt", _merge_vtt(parts)
    for ext, url in f.items():  # srv1/ttml etc.: try asking YouTube for json3
        if "timedtext" in url:
            u = up.urlparse(url)
            q = dict(up.parse_qsl(u.query))
            q["fmt"] = "json3"
            return "json3", _http_get(u._replace(query=up.urlencode(q)).geturl(), kind, origin).decode()
    raise YtError("no usable caption format")


def _merge_vtt(parts: list[str]) -> str:
    out = ["WEBVTT", ""]
    for p in parts:
        body = re.split(r"\n\s*\n", p.replace("\r\n", "\n"), maxsplit=1)
        if len(body) == 2 and body[0].startswith("WEBVTT"):
            out.append(body[1].strip())
            out.append("")
    return "\n".join(out)


def _json3_events(raw: str) -> list[tuple[int, int, str]]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    evs = []
    for e in data.get("events") or []:
        segs = e.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs)
        if not text.strip():
            continue
        start = int(e.get("tStartMs") or 0)
        evs.append((start, start + int(e.get("dDurationMs") or 0), text))
    return evs


def _clean_lines(lines) -> list[str]:
    """Normalize whitespace and drop consecutive duplicates (rolling auto-caption repeats)."""
    out: list[str] = []
    for ln in lines:
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln or (out and out[-1] == ln):
            continue
        out.append(ln)
    return out


def json3_to_text(raw: str) -> str:
    lines = []
    for _, _, t in _json3_events(raw):
        lines.extend(t.split("\n"))
    return " ".join(_clean_lines(lines))


_VTT_TAG = re.compile(r"<[^>]+>")
_VTT_TIME = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->")


def vtt_to_text(raw: str) -> str:
    lines = []
    for ln in raw.replace("\r\n", "\n").split("\n"):
        s = ln.strip()
        if not s or s == "WEBVTT" or s.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE")) \
                or _VTT_TIME.match(s) or s.isdigit():
            continue
        lines.append(_VTT_TAG.sub("", s))
    return " ".join(_clean_lines(lines))


def _ms(ms: int) -> str:
    h, ms = divmod(max(ms, 0), 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def json3_to_vtt(raw: str) -> str:
    out = ["WEBVTT", ""]
    for start, end, text in _json3_events(raw):
        text = "\n".join(_clean_lines(text.split("\n")))
        if not text:
            continue
        out += [f"{_ms(start)} --> {_ms(max(end, start + 1))}", text.replace("-->", "->"), ""]
    return "\n".join(out)


def caption_vtt(info: dict, key: str, origin: str = "user") -> str:
    track = next((t for t in caption_tracks(info) if t["key"] == key), None)
    if not track:
        raise YtUnavailable(f"no captions {key!r}")
    fmt, raw = fetch_track(track, origin, "captions")
    from .captions import two_line_vtt
    return two_line_vtt(raw, fmt, rolling=bool(track.get("auto")))


def transcript(info: dict, max_chars: int, langs: list[str] | None = None, origin: str = "worker") -> str:
    """Plain-text transcript, manual subs preferred over auto; '' if none or on fetch failure."""
    track = pick_track(info, langs)
    if not track:
        return ""
    try:
        fmt, raw = fetch_track(track, origin)
    except YtBlocked:
        raise
    except YtError as e:
        log.warning("transcript %s failed: %s", info.get("id"), e)
        return ""
    text = json3_to_text(raw) if fmt == "json3" else vtt_to_text(raw)
    return text[:max_chars] if max_chars and max_chars > 0 else text


# ---------------------------------------------------------------- comments

def comments(video_id: str, max_comments: int = 40, origin: str = "worker") -> list[dict]:
    n = max(1, int(max_comments))
    info = _extract(f"https://www.youtube.com/watch?v={video_id}", "comments", origin, f"comments {video_id}",
                    getcomments=True,
                    extractor_args={"youtube": {"max_comments": [str(n), "all", "0", "0"],
                                                "comment_sort": ["top"]}})
    raw = info.get("comments") or []
    # yt-dlp pages comments internally (~20 per continuation request) and can't be hooked cheaply:
    # log one estimated row per page so the worker's hourly/daily caps count them.
    for i in range(max(1, -(-len(raw) // 20))):
        log_yt_request("comments_page", origin, True, f"comments {video_id} page {i + 1} (estimated)")
    out = []
    for c in raw:
        if c.get("parent", "root") != "root":
            continue
        text = (c.get("text") or "").strip()
        if not text:
            continue
        out.append({"author": c.get("author") or "", "text": text, "likes": int(c.get("like_count") or 0)})
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------- downloads (local cache)

class DownloadAborted(yt_dlp.utils.DownloadCancelled):
    """Raise from a progress hook to stop a download; partial `.part` files stay resumable."""


def download_format(max_height: int) -> str:
    h = int(max_height or 1080)
    return (f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]"
            f"/b[ext=mp4][vcodec^=avc1][height<={h}]/b[ext=mp4][height<={h}]")


def download_opts(workdir: str, max_height: int = 1080, rate_limit: int = 0, hooks: list | None = None) -> dict:
    """yt-dlp options for one merged H.264/AAC mp4 (faststart) in `workdir` (same PO token/cookies as _opts)."""
    o = _opts(
        skip_download=False, format=download_format(max_height), merge_output_format="mp4",
        outtmpl=f"{workdir}/%(id)s.%(ext)s", continuedl=True, nopart=False, overwrites=False,
        retries=3, fragment_retries=3, socket_timeout=30, noplaylist=True,
        postprocessor_args={"merger+ffmpeg_o": ["-movflags", "+faststart"]},
        progress_hooks=list(hooks or []),
    )
    if rate_limit and rate_limit > 0:
        o["ratelimit"] = int(rate_limit)
    return o


def download(video_id: str, workdir: str, max_height: int = 1080, rate_limit: int = 0,
             hook=None, origin: str = "worker") -> dict:
    """Blocking. Download+merge into `workdir/<id>.mp4`. Returns {path, height, vcodec, acodec, title, info}.
    Raises DownloadAborted (hook asked to stop), YtBlocked/YtUnavailable/YtError."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise YtUnavailable(f"bad video id {video_id!r}")
    t = time.monotonic()
    try:
        with yt_dlp.YoutubeDL(download_opts(workdir, max_height, rate_limit, [hook] if hook else [])) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    except yt_dlp.utils.DownloadCancelled as e:
        log_yt_request("download", origin, True, f"download {video_id}: aborted")
        raise DownloadAborted(str(e)) from e
    except Exception as e:
        err = classify(e)
        log_yt_request("download", origin, False, f"download {video_id}: {type(err).__name__}: {str(e)[:300]}")
        raise err from e
    req = (info or {}).get("requested_downloads") or [{}]
    path = req[0].get("filepath") or f"{workdir}/{video_id}.mp4"
    log_yt_request("download", origin, True, f"download {video_id} ({time.monotonic() - t:.0f}s)")
    return {"path": path, "height": int(info.get("height") or 0), "vcodec": info.get("vcodec") or "",
            "acodec": info.get("acodec") or "", "title": info.get("title") or "", "info": info}
