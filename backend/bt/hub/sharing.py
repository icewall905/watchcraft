"""Opt-in public archive links. Public responses contain only explicitly selected source fields."""
import html
import re
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..access import profile_id, require_hub, hub_allowed
from ..db import session
from .api import can_see
from .models import ArticleShare, CapturedAsset, ContentItem

def _private_headers(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(prefix='/hub', dependencies=[Depends(require_hub), Depends(_private_headers)])
public_router = APIRouter()
HEADERS = {'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow, noarchive',
           'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
           'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}


def _owned(s, pid, iid):
    if not s.get(ContentItem, iid) or not can_see(s, pid, iid):
        raise HTTPException(404, 'Item not found')


def _result(row):
    return {'path': f'/share/{row.token}' if row else None}


@router.get('/items/{iid}/share')
def status(iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        row = s.get(ArticleShare, (pid, iid))
        if not row:
            _owned(s, pid, iid)
        return _result(row)


@router.post('/items/{iid}/share')
def create(iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        _owned(s, pid, iid)
        row = s.get(ArticleShare, (pid, iid))
        if not row:
            row = ArticleShare(profile_id=pid, item_id=iid, token=secrets.token_urlsafe(32))
            s.add(row)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                row = s.get(ArticleShare, (pid, iid))
                if not row:
                    raise
        return _result(row)


@router.delete('/items/{iid}/share')
def revoke(iid: int, pid: int = Depends(profile_id)):
    with session() as s:
        row = s.get(ArticleShare, (pid, iid))
        if not row:
            _owned(s, pid, iid)
        if row:
            s.delete(row)
            s.commit()
        return {'path': None}


def _shared(s, token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise HTTPException(404, 'Shared article unavailable', headers=HEADERS)
    row = s.exec(select(ArticleShare).where(ArticleShare.token == token)).first()
    item = s.get(ContentItem, row.item_id) if row else None
    if not row or not item or not hub_allowed(s, row.profile_id) or not can_see(s, row.profile_id, item.id):
        raise HTTPException(404, 'Shared article unavailable', headers=HEADERS)
    return item


def _text(value):
    return html.escape(value if isinstance(value, str) else '')


@public_router.get('/share/{token}', response_class=HTMLResponse)
def read(token: str):
    with session() as s:
        item = _shared(s, token)
        image = s.get(CapturedAsset, (item.id, 'image'))
        has_image = image and image.mime in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
        summary = item.enrichment.get('summary', '')
        summary = summary if isinstance(summary, str) else ''
        original = item.url if urlsplit(item.url).scheme in {'https', 'http'} else ''
        content = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><title>{_text(item.title)} · WatchCraft</title>
<style>body{{margin:0;background:#f7f5ef;color:#242622;font:18px/1.65 system-ui,sans-serif}}main{{max-width:780px;margin:auto;padding:2rem 1.25rem}}h1{{font:600 2.4rem/1.15 Georgia,serif;overflow-wrap:anywhere}}h2{{font-size:1.2rem}}.prose{{white-space:pre-wrap;overflow-wrap:anywhere}}img{{display:block;max-width:100%;height:auto}}a{{color:#315c50}}.meta{{color:#62665e}}section{{margin-block:2rem}}</style></head><body><main><p class="meta">Shared article · WatchCraft</p><h1>{_text(item.title)}</h1><p class="meta">{_text(item.publisher)}{(' · ' + _text(item.author)) if item.author else ''}</p>'''
        if original:
            content += f'<p><a href="{_text(original)}" rel="noreferrer noopener">Open original ↗</a></p>'
        if summary:
            content += f'<section><h2>AI summary</h2><p class="meta">AI-generated overview</p><p class="prose">{_text(summary)}</p></section>'
        if has_image:
            content += f'<img src="/share/{token}/image" alt="Archived article image">'
        content += f'<section><h2>Archived text</h2><p class="prose">{_text(item.body or item.summary) or "Readable text is not available locally yet."}</p></section></main></body></html>'
        return HTMLResponse(content, headers=HEADERS)


@public_router.get('/share/{token}/image')
def image(token: str):
    with session() as s:
        item = _shared(s, token)
        asset = s.get(CapturedAsset, (item.id, 'image'))
        if not asset or asset.mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
            raise HTTPException(404, 'Image unavailable', headers=HEADERS)
        return Response(asset.data, media_type=asset.mime, headers=HEADERS)
