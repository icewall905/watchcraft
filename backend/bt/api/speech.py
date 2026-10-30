"""Authenticated, profile-aware voice search; recordings are never retained."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import stt
from ..access import profile_id

router = APIRouter(prefix='/speech', tags=['Voice search'])
_slots = asyncio.Semaphore(2)


@router.get('/status')
def status(pid: int = Depends(profile_id)):
    return {'enabled': bool(stt.BASE_URL)}


@router.post('/transcribe')
async def transcribe(request: Request, pid: int = Depends(profile_id)):
    if not stt.BASE_URL:
        raise HTTPException(503, 'Voice search is not configured on this server yet.')
    content_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if content_type not in stt.AUDIO_TYPES:
        raise HTTPException(415, 'Please send a microphone audio recording.')
    if _slots.locked():
        raise HTTPException(429, 'Voice search is busy. Please try again shortly.')
    async with _slots:
        data = bytearray()
        try:
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > stt.MAX_BYTES:
                        raise HTTPException(413, 'The recording is too large. Please try a shorter phrase.')
                    data.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, 'The recording upload timed out. Please try again.') from None
        if not data:
            raise HTTPException(422, 'No microphone audio was received. Please try again.')
        return {'text': await stt.transcribe(bytes(data))}
