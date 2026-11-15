"""Live Pinterest search inside a board: guarded results, server-side previews, add to board.

The browser never receives provider URLs to load: results carry opaque tokens, previews are
captured through the throttled i.pinimg.com fetch and served from this authenticated route, and
adding resolves the token server-side. Same opt-in, budgets and auto-pause as the curator source.
"""
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlmodel import select

from ..access import require_hub
from ..db import session
from . import moodboard_sources, moodboards, pinterest_guard
from .models import MoodPin

router = APIRouter(prefix='/hub/boards', tags=['Mood boards'])
TTL = 900
_lock = threading.Lock()
_results: OrderedDict = OrderedDict()   # (pid, token) -> (expires, candidate)
_pages: OrderedDict = OrderedDict()     # (pid, bid, query) -> (expires, next bookmark)
_previews: OrderedDict = OrderedDict()  # (pid, token) -> (bytes, mime)


def _put(cache, key, value, cap):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > cap:
            cache.popitem(last=False)


def _get(cache, key):
    with _lock:
        hit = cache.get(key)
        if hit is None or (isinstance(hit, tuple) and isinstance(hit[0], float) and hit[0] < time.time()):
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return hit


def _paused(exc):
    headers = {'Retry-After': str(exc.retry_after)}
    if exc.blocked:
        raise HTTPException(503, str(exc), headers=headers) from None
    wait = f'{exc.retry_after} seconds' if exc.retry_after < 120 else f'{(exc.retry_after + 59) // 60} minutes'
    raise HTTPException(429, f'{exc} Try again in {wait}.', headers=headers) from None


@router.get('/pinterest/status')
def status(pid: int = Depends(require_hub)):
    return pinterest_guard.status()


@router.get('/{bid}/pinterest/search')
def search(bid: int, q: str = '', more: bool = False, pid: int = Depends(require_hub)):
    query = q.strip()
    if not query or len(query) > 300:
        raise HTTPException(422, 'Enter what to look for on Pinterest (up to 300 characters).')
    with session() as s:
        moodboards.owned(s, pid, bid)
    if not moodboard_sources.source_settings().get('pinterest'):
        raise HTTPException(409, 'Pinterest search is off. Turn it on in Settings → Search and sources.')
    page_key = (pid, bid, query.lower())
    bookmark = ''
    if more:
        hit = _get(_pages, page_key)
        if not hit:
            return {'results': [], 'has_more': False}
        bookmark = hit[1]
    try:
        found, following = moodboard_sources.pinterest_page(query, bookmark, 'live')
    except pinterest_guard.Paused as exc:
        _paused(exc)
    except Exception:
        raise HTTPException(502, 'Pinterest could not be searched right now. Try again later.') from None
    if following:
        _put(_pages, page_key, (time.time() + TTL, following), 200)
    with session() as s:
        rows = s.exec(select(MoodPin.source_url, MoodPin.image_url).where(MoodPin.board_id == bid, MoodPin.hidden == False)).all()  # noqa: E712
    known = {v for row in rows for v in row if v}
    results = []
    for item in found:
        token = secrets.token_urlsafe(12)
        _put(_results, (pid, token), (time.time() + TTL, item), 1000)
        results.append({'token': token, 'title': item['title'], 'pin_url': item['source_url'],
                        'attribution': item['attribution'], 'width': item.get('width', 0), 'height': item.get('height', 0),
                        'preview_url': f'/api/hub/boards/{bid}/pinterest/preview/{token}',
                        'on_board': item['source_url'] in known or item['image_url'] in known})
    return {'results': results, 'has_more': bool(following)}


def _candidate(pid, token):
    hit = _get(_results, (pid, token)) if len(token) <= 40 else None
    if not hit:
        raise HTTPException(404, 'This Pinterest result expired. Search again.')
    return hit[1]


@router.get('/{bid}/pinterest/preview/{token}')
def preview(bid: int, token: str, pid: int = Depends(require_hub)):
    with session() as s:
        moodboards.owned(s, pid, bid)
    item = _candidate(pid, token)
    cached = _get(_previews, (pid, token))
    if not cached:
        try:
            data, mime, _w, _h = moodboards.capture_image(item.get('preview_url') or item['image_url'])
        except pinterest_guard.Paused as exc:
            _paused(exc)
        except Exception:
            raise HTTPException(404, 'Preview unavailable') from None
        cached = (data, mime)
        _put(_previews, (pid, token), cached, 300)
    return Response(cached[0], media_type=cached[1],
                    headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


class AddRequest(BaseModel):
    token: str = Field(min_length=1, max_length=40)


@router.post('/{bid}/pinterest/add')
def add(bid: int, data: AddRequest, pid: int = Depends(require_hub)):
    item = _candidate(pid, data.token)
    return moodboards.add_image_for(pid, bid, moodboards.ImageInput(
        url=item['image_url'], source_url=item['source_url'], title=item['title'][:500]))
