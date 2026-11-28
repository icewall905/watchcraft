"""Third-party extras (SponsorBlock, Return YouTube Dislike, DeArrow) and the thumbnail/avatar disk cache.
All network failures degrade to empty/None. Short timeouts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import urllib.parse as up
from pathlib import Path

import httpx

from . import netfetch
from .config import DATA_DIR

log = logging.getLogger("bt.extras")

SB_API = "https://sponsor.ajay.app/api"
RYD_API = "https://returnyoutubedislikeapi.com/votes"
DEARROW_THUMB = "https://dearrow-thumb.ajay.app/api/v1/getThumbnail"
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
UA = "BetterTube/1.0 (self-hosted)"
THUMB_DIR = DATA_DIR / "thumbs"
THUMB_Q = {"hq": "hqdefault", "mq": "mqdefault", "maxres": "maxresdefault", "sd": "sddefault"}
AVATAR_HOSTS = ("ggpht.com", "googleusercontent.com", "ytimg.com")
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_cache: dict[tuple, tuple[float, object]] = {}


def _cget(key, ttl):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return True, hit[1]
    return False, None


def _cset(key, val):
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = (time.time(), val)


async def _get_json(url: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as c:
        r = await c.get(url, params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------- SponsorBlock

def filter_segments(data, video_id: str) -> list[dict]:
    out = []
    for item in data or []:
        if not isinstance(item, dict) or item.get("videoID") != video_id:
            continue
        for s in item.get("segments") or []:
            seg = s.get("segment")
            if not (isinstance(seg, list) and len(seg) == 2):
                continue
            out.append({"segment": [float(seg[0]), float(seg[1])], "category": s.get("category"),
                        "actionType": s.get("actionType", "skip"), "UUID": s.get("UUID")})
    return sorted(out, key=lambda s: s["segment"][0])


async def sponsorblock(video_id: str, categories: list[str]) -> list[dict]:
    """k-anonymous lookup (first 4 hex of sha256(video_id)); cached 1h; [] on error."""
    if not VID_RE.match(video_id) or not categories:
        return []
    cats = sorted(set(categories))
    key = ("sb", video_id, tuple(cats))
    ok, val = _cget(key, 3600)
    if ok:
        return val
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    try:
        data = await _get_json(f"{SB_API}/skipSegments/{prefix}",
                               {"categories": json.dumps(cats), "actionTypes": '["skip","mute","poi","full"]'})
        segs = filter_segments(data, video_id)
    except Exception as e:  # network, json, http
        log.info("sponsorblock %s: %s", video_id, e)
        return []
    _cset(key, segs)
    return segs


# ---------------------------------------------------------------- Return YouTube Dislike

async def dislikes(video_id: str) -> dict | None:
    if not VID_RE.match(video_id):
        return None
    key = ("ryd", video_id)
    ok, val = _cget(key, 3600)
    if ok:
        return val
    try:
        d = await _get_json(RYD_API, {"videoId": video_id})
        val = None if not d else {k: d.get(k) for k in ("likes", "dislikes", "rating", "viewCount")}
    except Exception as e:
        log.info("ryd %s: %s", video_id, e)
        return None
    _cset(key, val)
    return val


# ---------------------------------------------------------------- DeArrow

def pick_branding(d: dict | None, video_id: str) -> dict | None:
    if not isinstance(d, dict):
        return None
    title = None
    for t in d.get("titles") or []:
        if not t.get("original") and (t.get("locked") or (t.get("votes") or 0) >= 0) and t.get("title"):
            title = re.sub(r"(^|\s)>(\S)", r"\1\2", t["title"])  # DeArrow '>' = keep-case marker
            break
    thumb = None
    for t in d.get("thumbnails") or []:
        if not t.get("original") and (t.get("locked") or (t.get("votes") or 0) >= 0) \
                and t.get("timestamp") is not None:
            thumb = f"{DEARROW_THUMB}?" + up.urlencode({"videoID": video_id, "time": t["timestamp"]})
            break
    return {"title": title, "thumb": thumb} if (title or thumb) else None


async def dearrow(video_id: str) -> dict | None:
    if not VID_RE.match(video_id):
        return None
    key = ("da", video_id)
    ok, val = _cget(key, 3600)
    if ok:
        return val
    try:
        val = pick_branding(await _get_json(f"{SB_API}/branding", {"videoID": video_id}), video_id)
    except Exception as e:
        log.info("dearrow %s: %s", video_id, e)
        return None
    _cset(key, val)
    return val


# ---------------------------------------------------------------- image disk cache

_img_locks: dict[str, asyncio.Lock] = {}


IMAGE_HOSTS = ("ytimg.com",) + AVATAR_HOSTS


def image_host_allowed(url: str) -> bool:
    p = up.urlparse(url or "")
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (host == "dearrow-thumb.ajay.app" or
                                    any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS))


async def _fetch_image(url: str) -> bytes | None:
    """GET an image, following redirects only to allowed image hosts (through the proxy if set)."""
    for _ in range(5):
        if not image_host_allowed(url):
            return None
        try:
            r = await netfetch.aget(url, headers={"User-Agent": UA}, timeout=httpx.Timeout(10.0, connect=4.0))
        except (httpx.HTTPError, OSError) as e:
            log.info("image %s: %s", url, e)
            return None
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = up.urljoin(url, r.headers["location"])
            continue
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        return r.content
    return None


async def cached_image(path: Path, urls: list[str], on_fetch=None) -> Path | None:
    """Return cached file or fetch the first url that works into `path` (atomic write)."""
    if path.exists() and path.stat().st_size > 0:
        return path
    key = str(path)
    lock = _img_locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            if path.exists() and path.stat().st_size > 0:
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            for url in urls:
                try:
                    data = await _fetch_image(url)
                except httpx.HTTPError as e:
                    data = None
                    log.info("image %s: %s", url, e)
                if on_fetch:
                    await on_fetch(url, data is not None)
                if data:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(path)
                    return path
            return None
    finally:
        if _img_locks.get(key) is lock and not lock.locked():
            _img_locks.pop(key, None)


def thumb_urls(video_id: str, q: str) -> list[str]:
    base = f"https://i.ytimg.com/vi/{video_id}/"
    names = [THUMB_Q.get(q, "hqdefault")]
    if names[0] != "hqdefault":
        names.append("hqdefault")  # maxres often 404s
    return [base + n + ".jpg" for n in names]


def thumb_path(video_id: str, q: str) -> Path:
    return THUMB_DIR / f"{video_id}_{q if q in THUMB_Q else 'hq'}.jpg"


def avatar_allowed(url: str) -> bool:
    p = up.urlparse(url or "")
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in AVATAR_HOSTS)


def avatar_miss_path(channel_id: str) -> Path:
    """Negative-cache marker: mtime = last time we asked YouTube for this channel's avatar."""
    return avatar_path(channel_id).with_suffix(".miss")


def avatar_path(channel_id: str) -> Path:
    return THUMB_DIR / "avatars" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', channel_id)}.jpg"
