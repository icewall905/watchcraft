"""Offline cache: list/queue/delete downloads. Files are served by GET /api/video/{id}/file (api/video.py)."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, col, select

from ..db import get_kv, get_session, get_settings
from ..access import require_admin
from ..downloader.service import GB, delete_files, used_bytes
from ..models import Download, ReencodeJob, Video, now
from .core import profile_id, video_out

router = APIRouter()
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def download_out(d: Optional[Download]) -> Optional[dict]:
    if not d or d.status == "evicted":
        return None
    return {"status": d.status, "progress": d.progress, "height": d.height, "size_bytes": d.size_bytes,
            "pinned": d.pinned, "error": d.error, "attempts": d.attempts}


@router.get("/downloads")
def list_downloads(s: Session = Depends(get_session)):
    rows = s.exec(select(Download, Video).join(Video, Video.id == Download.video_id, isouter=True)
                  .where(Download.status != "evicted")
                  .order_by(col(Download.status) != "downloading", col(Download.created_at).desc())).all()
    cfg = get_settings(s)["cache"]
    items, counts = [], {}
    for d, v in rows:
        counts[d.status] = counts.get(d.status, 0) + 1
        items.append({"video_id": d.video_id, **download_out(d), "created_at": d.created_at,
                      "finished_at": d.finished_at, "last_played_at": d.last_played_at,
                      "video": video_out(v, offline=d.status == "done") if v else {"id": d.video_id}})
    jobs = s.exec(select(ReencodeJob)).all()
    return {"items": items, "reencode": {"queued": sum(j.status == "queued" for j in jobs),
                                          "failed": sum(j.status == "error" for j in jobs)},
            "totals": {"used_bytes": used_bytes(s), "max_bytes": int(float(cfg.get("max_gb") or 0) * GB),
                       "counts": counts},
            "state": get_kv("downloader_state", {"state": "offline"}, s)}


class DownloadIn(BaseModel):
    video_id: str


@router.post("/downloads/reencode", dependencies=[Depends(require_admin)])
def queue_reencode(s: Session = Depends(get_session)):
    """Queue saved copies that have not already been re-encoded; retry failed jobs."""
    n = 0
    for d in s.exec(select(Download).where(Download.status == "done")).all():
        if d.vcodec == "h264 (NVENC)":
            continue
        job = s.get(ReencodeJob, d.video_id)
        if job and job.status == "queued":
            continue
        job = job or ReencodeJob(video_id=d.video_id)
        job.status, job.error = "queued", ""
        s.add(job)
        n += 1
    s.commit()
    return {"queued": n}


@router.post("/downloads")
def queue_download(body: DownloadIn, pid: int = Depends(profile_id), s: Session = Depends(get_session)):
    """Manual "Save offline": pin + queue (re-queues evicted/failed ones)."""
    vid = body.video_id
    if not VID_RE.match(vid):
        raise HTTPException(400, "bad video id")
    if not s.get(Video, vid):
        s.add(Video(id=vid))
    d = s.get(Download, vid) or Download(video_id=vid)
    d.pinned, d.requested_by_profile = True, d.requested_by_profile or pid
    if d.status in ("evicted", "error") or not d.status:
        d.status, d.attempts, d.error, d.progress, d.created_at = "queued", 0, "", 0.0, now()
    s.add(d)
    s.commit()
    return {"video_id": vid, **download_out(d)}


def _remove(s: Session, d: Download) -> None:
    job = s.get(ReencodeJob, d.video_id)
    if job:
        s.delete(job)
    delete_files(d)  # the downloader also notices (status != downloading) and stops within ~2 s
    d.status, d.pinned, d.size_bytes, d.progress = "evicted", False, 0, 0.0
    s.add(d)


@router.delete("/downloads/{video_id}")
def delete_download(video_id: str, s: Session = Depends(get_session)):
    s.exec(text("BEGIN IMMEDIATE"))
    d = s.get(Download, video_id)
    if not d:
        raise HTTPException(404, "not downloaded")
    _remove(s, d)
    s.commit()
    return {"ok": True}


@router.delete("/downloads")
def clear_downloads(s: Session = Depends(get_session)):
    s.exec(text("BEGIN IMMEDIATE"))
    n = 0
    for d in s.exec(select(Download).where(Download.status != "evicted")).all():
        _remove(s, d)
        n += 1
    s.commit()
    return {"ok": True, "removed": n}
