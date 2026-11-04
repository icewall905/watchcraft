"""Profile-unlocked generation controls; tracks share ordinary video-media authorization."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
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


class GenerateIn(BaseModel):
    local_only: bool = False
    regenerate: bool = False


@router.post('/{video_id}/subtitles')
async def generate(video_id: str, body: GenerateIn | None = None, pid: int = Depends(profile_id)):
    validate(video_id)
    await run_in_threadpool(subtitle_jobs.enqueue, video_id, pid, body.local_only if body else False,
                            body.regenerate if body else False)
    return await run_in_threadpool(subtitle_jobs.status, video_id)


@router.get('/{video_id}/subtitles/generated.vtt')
def track(video_id: str):
    validate(video_id)
    with session() as s:
        row = s.get(GeneratedSubtitles, video_id)
        if not row or row.status != 'ready' or not subtitle_jobs.usable_vtt(row.vtt):
            raise HTTPException(404, 'Generated subtitles are not ready.')
        return Response(row.vtt, media_type='text/vtt; charset=utf-8',
                        headers={'Cache-Control': 'private, max-age=3600'})
