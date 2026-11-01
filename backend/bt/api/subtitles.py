"""Profile-unlocked generation controls; tracks share ordinary video-media authorization."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .. import subtitle_jobs
from ..access import profile_id
from ..db import session
from ..downloader.service import VID_RE
from ..models import GeneratedSubtitles

router = APIRouter(prefix='/video', tags=['Generated subtitles'])


def validate(video_id):
    if not VID_RE.fullmatch(video_id): raise HTTPException(400, 'Invalid video id')


@router.get('/{video_id}/subtitles')
async def status(video_id: str, pid: int = Depends(profile_id)):
    validate(video_id)
    return await run_in_threadpool(subtitle_jobs.status, video_id)


@router.post('/{video_id}/subtitles')
async def generate(video_id: str, pid: int = Depends(profile_id)):
    validate(video_id)
    await run_in_threadpool(subtitle_jobs.enqueue, video_id, pid)
    return await run_in_threadpool(subtitle_jobs.status, video_id)


@router.get('/{video_id}/subtitles/generated.vtt')
def track(video_id: str):
    validate(video_id)
    with session() as s:
        row = s.get(GeneratedSubtitles, video_id)
        if not row or row.status != 'ready' or not row.vtt:
            raise HTTPException(404, 'Generated subtitles are not ready.')
        return Response(row.vtt, media_type='text/vtt; charset=utf-8',
                        headers={'Cache-Control': 'private, max-age=3600'})
