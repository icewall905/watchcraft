"""Search, video details, DASH manifest, range proxy, captions, SponsorBlock, comments, thumbs, channels.

Async handlers never touch the DB on the event loop: all SQLModel work runs in `run_in_threadpool`
(own session per call), so a busy SQLite writer can't stall proxy streams.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlmodel import Session, select

from .. import extras, streams, ytdl
from ..db import get_settings, log_yt_request, session
from ..downloader.service import abs_path
from ..models import Candidate, Category, Channel, Download, Profile, ProfileChannel, Video, WatchHistory, now
from .core import _download_statuses, profile_id, video_out
from .downloads import download_out

router = APIRouter()

IMG_CACHE = {"Cache-Control": "public, max-age=604800, immutable"}
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
UC_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
AVATAR_RETRY_S = 24 * 3600


def _vid(video_id: str) -> str:
    if not VID_RE.match(video_id):
        raise HTTPException(404, "bad video id")
    return video_id


async def _yt(fn, *a, **kw):
    """Run a blocking ytdl call in the threadpool and map errors to HTTP."""
    try:
        return await run_in_threadpool(fn, *a, **kw)
    except ytdl.YtUnavailable as e:
        raise HTTPException(404, f"unavailable: {str(e)[:200]}") from e
    except ytdl.YtBlocked as e:
        raise HTTPException(503, f"YouTube blocked the request: {str(e)[:200]}") from e
    except ytdl.YtError as e:
        raise HTTPException(502, f"YouTube error: {str(e)[:200]}") from e


def _db(fn, *a, **kw):
    """Run `fn(s, *a, **kw)` with a fresh session in the threadpool."""
    def run():
        with session() as s:
            return fn(s, *a, **kw)
    return run_in_threadpool(run)


# ---------------------------------------------------------------- sync DB helpers (threadpool only)

def _player_prefs(s: Session, pid: int) -> dict:
    st = get_settings(s)["player"]
    p = s.get(Profile, pid)
    if p and isinstance((p.prefs or {}).get("player"), dict):
        st = {**st, **p.prefs["player"]}
    return st


def _upsert_channel(s: Session, ch: dict) -> None:
    if not ch.get("id"):
        return
    row = s.get(Channel, ch["id"]) or Channel(id=ch["id"])
    row.name = ch.get("name") or row.name
    row.handle = ch.get("handle") or row.handle
    row.avatar = ch.get("avatar") or row.avatar
    row.updated_at = now()
    s.add(row)


def _channel_state(s: Session, pid: int, cid: str) -> dict:
    pc = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == pid,
                                             ProfileChannel.channel_id == cid)).first() if cid else None
    return {"subscribed": bool(pc and pc.subscribed), "blocked": bool(pc and pc.blocked)}


def _store_entries(s: Session, entries: list[dict], channel: dict | None = None) -> list[dict]:
    if channel:
        _upsert_channel(s, channel)
    vids = [ytdl.upsert_video(s, e) for e in entries]
    s.commit()
    st = _download_statuses([v.id for v in vids], s)
    return [video_out(v, offline=st.get(v.id) == "done", download_status=st.get(v.id)) for v in vids]


def _store_video(s: Session, info: dict, pid: int) -> dict:
    v = ytdl.upsert_video(s, info)
    cid = info.get("channel_id") or ""
    if cid:
        ch = s.get(Channel, cid)
        if not ch or not ch.name:
            _upsert_channel(s, {"id": cid, "name": info.get("channel") or info.get("uploader") or ""})
    s.commit()
    cands = s.exec(select(Candidate).join(Category, Category.id == Candidate.category_id)
                   .where(Candidate.video_id == v.id, Category.profile_id == pid)).all()
    h = s.exec(select(WatchHistory).where(WatchHistory.profile_id == pid, WatchHistory.video_id == v.id)).first()
    dl = s.get(Download, v.id)
    out = video_out(v, full=True, offline=bool(dl and dl.status == "done"),
                    download_status=dl.status if dl else None)
    out.update(
        download=download_out(dl),
        channel={"id": cid, "name": v.channel_name, "avatar": f"/api/channel/{cid}/avatar" if cid else "",
                 **_channel_state(s, pid, cid)},
        in_categories=[{"category_id": c.category_id, "candidate_id": c.id, "status": c.status, "score": c.score,
                        "summary": c.summary, "reasons": c.reasons} for c in cands],
        history={"position_s": h.position_s if h else 0, "finished": bool(h and h.finished)},
    )
    return out


# ---------------------------------------------------------------- search / video

@router.get("/search")
async def search(q: str, n: int = 20, pid: int = Depends(profile_id)):
    q = q.strip()
    if not q:
        return []
    entries = await _yt(ytdl.search, q, max(1, min(n, 50)), "user")
    return await _db(_store_entries, entries)


@router.get("/video/{video_id}")
async def video(video_id: str, pid: int = Depends(profile_id)):
    _vid(video_id)
    info = await _yt(ytdl.video_info, video_id, "user")
    out = await _db(_store_video, info, pid)
    ex = (await _db(get_settings))["extras"]
    ryd_t = extras.dislikes(video_id) if ex.get("ryd") else asyncio.sleep(0, None)
    da_t = extras.dearrow(video_id) if ex.get("dearrow") else asyncio.sleep(0, None)
    out["dislikes"], out["dearrow"] = await asyncio.gather(ryd_t, da_t)
    out["has_captions"] = bool(ytdl.caption_tracks(info))
    return out


@router.get("/video/{video_id}/manifest.mpd")
async def manifest(video_id: str, max_height: Optional[int] = None, codec: Optional[str] = None,
                   pid: int = Depends(profile_id)):
    _vid(video_id)
    info = await _yt(ytdl.video_info, video_id, "user")
    if ytdl._is_live(info):
        raise HTTPException(409, "live not supported")
    prefs = await _db(_player_prefs, pid)
    mh = int(max_height or prefs.get("max_height") or 1080)
    pc = codec if codec in streams.CODEC_ORDER else prefs.get("prefer_codec") or "avc1"
    for attempt in (0, 1):
        try:
            mpd = await run_in_threadpool(streams.build_mpd, info, mh, pc, streams.proxy_url,
                                          streams.probe_ranges, streams.check_stream)
            break
        except ValueError as e:
            if attempt:
                raise HTTPException(404, f"no DASH streams: {e}") from e
            # cached stream URLs may have gone bad (googlevideo 403): re-extract once
            ytdl.cache_drop(video_id)
            info = await _yt(ytdl.video_info, video_id, "user")
    return Response(mpd, media_type="application/dash+xml", headers={"Cache-Control": "no-store"})


@router.get("/video/{video_id}/fallback")
async def fallback(video_id: str):
    _vid(video_id)
    info = await _yt(ytdl.video_info, video_id, "user")
    f = streams.best_progressive(info)
    if f and not await run_in_threadpool(streams.check_stream, f):
        ytdl.cache_drop(video_id)  # stale/refused URL: re-extract once
        info = await _yt(ytdl.video_info, video_id, "user")
        f = streams.best_progressive(info)
    if not f:
        raise HTTPException(404, "no progressive format")
    return RedirectResponse(streams.proxy_url(f), status_code=302, headers={"Cache-Control": "no-store"})


def _file_for(s: Session, video_id: str):
    d = s.get(Download, video_id)
    if not d or d.status != "done":
        return None
    p = abs_path(d.path)
    if not p or not p.is_file():
        return None
    t = now()
    last = d.last_played_at
    if last is None or (t - (last if last.tzinfo else last.replace(tzinfo=t.tzinfo))).total_seconds() > 60:
        d.last_played_at = t  # throttled: Range requests arrive in bursts
        s.add(d)
        s.commit()
    return p


@router.get("/video/{video_id}/file")
async def video_file(video_id: str):
    """Offline copy (merged mp4). Starlette FileResponse handles Range/206 itself."""
    _vid(video_id)
    p = await _db(_file_for, video_id)
    if not p:
        raise HTTPException(404, "not available offline")
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/proxy/{token}")
async def proxy(token: str, request: Request):
    # no DB access here: hot path for every media segment
    return await streams.proxy(token, request.headers.get("range"))


# ---------------------------------------------------------------- captions

@router.get("/video/{video_id}/captions")
async def captions(video_id: str):
    _vid(video_id)
    info = await _yt(ytdl.video_info, video_id, "user")
    return [{"lang": t["lang"], "key": t["key"], "name": t["name"], "auto": t["auto"],
             "url": f"/api/video/{video_id}/captions/{t['key']}.vtt"} for t in ytdl.caption_tracks(info)]


@router.get("/video/{video_id}/captions/{name}")
async def caption_vtt(video_id: str, name: str):
    _vid(video_id)
    if not name.endswith(".vtt"):
        raise HTTPException(404, "use .vtt")
    info = await _yt(ytdl.video_info, video_id, "user")
    vtt = await _yt(ytdl.caption_vtt, info, name[:-4], "user")
    return Response(vtt, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------- extras

@router.get("/video/{video_id}/sponsorblock")
async def sponsorblock(video_id: str):
    _vid(video_id)
    sb = (await _db(get_settings))["sponsorblock"]
    if not sb.get("enabled"):
        return []
    cats = [c for c, action in (sb.get("categories") or {}).items() if action != "off"]
    return await extras.sponsorblock(video_id, cats)


def _cached_comments(s: Session, video_id: str, n: int) -> list | None:
    v = s.get(Video, video_id)
    return v.comments[:n] if v and v.comments and len(v.comments) >= min(n, 20) else None


def _save_comments(s: Session, video_id: str, cs: list) -> None:
    v = s.get(Video, video_id) or Video(id=video_id)
    v.comments = cs
    s.add(v)
    s.commit()


@router.get("/video/{video_id}/comments")
async def comments(video_id: str, n: int = 40):
    _vid(video_id)
    n = max(1, min(n, 200))
    if (cached := await _db(_cached_comments, video_id, n)) is not None:
        return cached
    cs = await _yt(ytdl.comments, video_id, n, "user")
    await _db(_save_comments, video_id, cs)
    return cs


def _img_logger(kind: str):
    async def cb(url: str, ok: bool):
        await run_in_threadpool(log_yt_request, kind, "user", ok, url[:200])
    return cb


# Keep community lookups bounded so a page of thumbnails cannot monopolize connections.
_dearrow_thumb_slots = asyncio.Semaphore(8)


async def _community_thumb(video_id: str):
    async with _dearrow_thumb_slots:
        branding = await extras.dearrow(video_id)
        url = branding.get("thumb") if branding else None
        if not url:
            # The thumbnail service may already have a DeArrow fallback frame even
            # when there are no community submissions in the branding API.
            from urllib.parse import urlencode
            url = f"{extras.DEARROW_THUMB}?" + urlencode({"videoID": video_id})
        # The timestamp is part of the cache key, so a changed community pick is refreshed.
        import hashlib
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        path = extras.THUMB_DIR / "dearrow" / f"{video_id}_{key}.jpg"
        return await extras.cached_image(path, [url])


@router.get("/thumb/{video_id}")
async def thumb(video_id: str, q: str = "hq", dearrow: bool = False):
    _vid(video_id)
    q = q if q in extras.THUMB_Q else "hq"
    if dearrow:
        try:
            # A missing or slow community service must not hold the page indefinitely.
            replacement = await asyncio.wait_for(_community_thumb(video_id), timeout=3.0)
        except Exception:
            replacement = None
        if replacement:
            return FileResponse(replacement, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    p = await extras.cached_image(extras.thumb_path(video_id, q), extras.thumb_urls(video_id, q),
                                  _img_logger("thumb"))
    if not p:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"} if dearrow else IMG_CACHE)


# ---------------------------------------------------------------- channels

def _small_avatar(url: str) -> str:
    return re.sub(r"=s\d+[^/]*$", "=s176-c-k-c0x00ffffff-no-rj", url) if url else url


def _channel_avatar_url(s: Session, channel_id: str) -> str:
    ch = s.get(Channel, channel_id)
    return ch.avatar if ch else ""


def _store_channel(s: Session, ch: dict) -> None:
    _upsert_channel(s, ch)
    s.commit()


_avatar_inflight: dict[str, asyncio.Lock] = {}


async def _fetch_avatar(channel_id: str, path, miss) -> bool:
    """One attempt (DB url or a flat channel listing, then the image). Touches the miss marker on failure."""
    ok = False
    try:
        url = await _db(_channel_avatar_url, channel_id)
        if not url:
            try:
                ch, _ = await _yt(ytdl.channel_videos, channel_id, 1, "user")
            except HTTPException:
                return False
            await _db(_store_channel, ch)
            url = ch.get("avatar") or ""
        url = _small_avatar(url)
        ok = bool(url and extras.avatar_allowed(url)
                  and await extras.cached_image(path, [url], _img_logger("avatar")))
        return ok
    finally:
        if ok:
            miss.unlink(missing_ok=True)
        else:
            miss.parent.mkdir(parents=True, exist_ok=True)
            miss.touch()


@router.get("/channel/{channel_id}/avatar")
async def channel_avatar(channel_id: str):
    if not UC_RE.match(channel_id):
        raise HTTPException(404, "bad channel id")
    path = extras.avatar_path(channel_id)
    if not (path.exists() and path.stat().st_size > 0):
        miss = extras.avatar_miss_path(channel_id)
        lock = _avatar_inflight.setdefault(channel_id, asyncio.Lock())
        try:
            async with lock:
                if not (path.exists() and path.stat().st_size > 0):
                    # negative cache: at most one YouTube attempt per channel per day (survives restarts)
                    if miss.exists() and time.time() - miss.stat().st_mtime < AVATAR_RETRY_S:
                        raise HTTPException(404, "no avatar (cached miss)")
                    if not await _fetch_avatar(channel_id, path, miss):
                        raise HTTPException(404, "no avatar")
        finally:
            if _avatar_inflight.get(channel_id) is lock and not lock.locked():
                _avatar_inflight.pop(channel_id, None)
    return FileResponse(path, media_type="image/jpeg", headers=IMG_CACHE)


def _channel_out(s: Session, ch: dict, entries: list[dict], pid: int) -> dict:
    vids = _store_entries(s, entries, ch)
    return {"channel": {**ch, "avatar": f"/api/channel/{ch['id']}/avatar" if ch.get("id") else "",
                        "avatar_src": ch.get("avatar") or "", **_channel_state(s, pid, ch.get("id") or "")},
            "videos": vids}


@router.get("/channel/{ref:path}")
async def channel(ref: str, n: int = 30, pid: int = Depends(profile_id)):
    ch, entries = await _yt(ytdl.channel_videos, ref, max(1, min(n, 100)), "user")
    return await _db(_channel_out, ch, entries, pid)
