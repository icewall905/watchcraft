"""WatchCraft's additive, profile-owned non-video discovery slice."""
import asyncio
import http.client
import ipaddress
import json
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit, urljoin

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field as Input
from sqlmodel import Field, SQLModel, select
from .db import session
from .api.core import profile_id

class Curator(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    instructions: str = ''
    provider: str = 'rss'
    source: str
    interval_hours: int = 6
    enabled: bool = True
    next_run: float = 0
    last_success: float | None = None
    error: str = ''

class Discovery(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    curator_id: int = Field(index=True)
    url: str
    title: str
    provider: str
    discovered_at: float = Field(default_factory=time.time)
    saved: bool = False

class CuratorInput(BaseModel):
    name: str = Input(min_length=1, max_length=100)
    instructions: str = Input(default='', max_length=4000)
    provider: str = Input(pattern='^(rss|github)$')
    source: str = Input(max_length=2000)
    interval_hours: int = Input(default=6, ge=1, le=168)
    enabled: bool = True

def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Use a public HTTPS URL on port 443.')
    return urlunsplit(('https', p.netloc.lower(), p.path or '/', p.query, ''))

def fetch(url):
    """Pin validated public DNS to the actual socket; validate every redirect."""
    for _ in range(4):
        url = canonical(url)
        p = urlsplit(url)
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Source must resolve exclusively to public addresses.')
        conn = http.client.HTTPSConnection(p.hostname, timeout=15, context=ssl.create_default_context())
        # HTTPSConnection wraps this pinned socket with the original hostname for TLS.
        conn._create_connection = lambda *args, **kwargs: socket.create_connection((addresses[0][4][0], 443), timeout=15)
        try:
            conn.request('GET', (p.path or '/') + ('?' + p.query if p.query else ''), headers={'User-Agent': 'WatchCraft/0.1', 'Accept': 'application/atom+xml, application/rss+xml, application/json, application/xml, text/xml'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise ValueError(f'Source returned HTTP {response.status}.')
            kind = response.getheader('Content-Type', '').split(';')[0].lower()
            if kind not in ('application/atom+xml', 'application/rss+xml', 'application/xml', 'text/xml', 'application/json'):
                raise ValueError('Source must return XML or JSON.')
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError('Source exceeds 2 MB limit.')
            return data
        finally:
            conn.close()
    raise ValueError('Too many redirects.')

def discover(curator):
    if curator.provider == 'github':
        p = urlsplit(curator.source)
        parts = p.path.strip('/').split('/')
        if p.hostname != 'github.com' or len(parts) != 2 or any(not x or not all(c.isalnum() or c in '-_.' for c in x) for x in parts):
            raise ValueError('Use https://github.com/owner/repository.')
        data = json.loads(fetch(f'https://api.github.com/repos/{parts[0]}/{parts[1]}/releases?per_page=20'))
        if not isinstance(data, list):
            raise ValueError('Unexpected GitHub response.')
        return [(r.get('name') or r['tag_name'], r['html_url']) for r in data[:20]]
    data = fetch(curator.source)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML entities are not allowed.')
    root = ET.fromstring(data)
    result = []
    for item in root.iter():
        if item.tag.split('}')[-1] not in ('item', 'entry'):
            continue
        title, link = '', ''
        for child in item:
            tag = child.tag.split('}')[-1]
            if tag == 'title': title = ''.join(child.itertext())[:500]
            if tag == 'link' and child.get('rel', 'alternate') == 'alternate': link = child.get('href') or child.text or ''
        if title and link:
            try: result.append((title, canonical(urljoin(curator.source, link.strip()))))
            except ValueError: pass
        if len(result) >= 50: break
    return result

def run(cid):
    with session() as s:
        c = s.get(Curator, cid)
        if not c or not c.enabled or c.next_run > time.time(): return
        # Serialize claims across API processes; next_run doubles as a bounded lease.
        from sqlalchemy import update
        claim = s.exec(update(Curator).where(Curator.id == cid, Curator.enabled == True, Curator.next_run <= time.time()).values(next_run=time.time() + 600))
        s.commit()
        if claim.rowcount != 1: return
        try:
            items = discover(c)
            for title, url in items:
                url = canonical(url)
                if not s.exec(select(Discovery).where(Discovery.profile_id == c.profile_id, Discovery.url == url)).first():
                    s.add(Discovery(profile_id=c.profile_id, curator_id=c.id, title=title[:500], url=url, provider=c.provider))
            c.last_success = time.time()
            c.error = ''
        except Exception:
            s.rollback()
            c = s.get(Curator, cid)
            c.error = 'Discovery failed. Check the public source URL, feed format, or provider availability.'
        c.next_run = time.time() + c.interval_hours * 3600
        s.add(c)
        s.commit()

async def scheduler():
    while True:
        with session() as s:
            ids = [c.id for c in s.exec(select(Curator).where(Curator.enabled == True, Curator.next_run <= time.time())).all()]
        for cid in ids:
            await asyncio.to_thread(run, cid)
        await asyncio.sleep(30)

router = APIRouter(prefix='/curation', tags=['WatchCraft'])

@router.get('/curators')
def curators(pid: int = Depends(profile_id)):
    with session() as s:
        return s.exec(select(Curator).where(Curator.profile_id == pid)).all()

@router.post('/curators')
def create(body: CuratorInput, pid: int = Depends(profile_id)):
    try: source = canonical(body.source)
    except ValueError as e: raise HTTPException(422, str(e))
    with session() as s:
        if len(s.exec(select(Curator).where(Curator.profile_id == pid)).all()) >= 20:
            raise HTTPException(422, "Maximum 20 curators per profile.")
        c = Curator(profile_id=pid, **{**body.model_dump(), 'source': source})
        s.add(c); s.commit(); s.refresh(c)
        return c

@router.post('/curators/{cid}/{action}')
def control(cid: int, action: str, pid: int = Depends(profile_id)):
    with session() as s:
        c = s.get(Curator, cid)
        if not c or c.profile_id != pid: raise HTTPException(404, 'Curator not found')
        if action == 'run': c.next_run = 0; c.enabled = True
        elif action == 'pause': c.enabled = False
        elif action == 'resume': c.enabled = True
        else: raise HTTPException(404, 'Unknown action')
        s.add(c); s.commit()
        return {'ok': True}

@router.get('/discoveries')
def discoveries(saved: bool = False, pid: int = Depends(profile_id)):
    with session() as s:
        q = select(Discovery).where(Discovery.profile_id == pid)
        if saved: q = q.where(Discovery.saved == True)
        return s.exec(q.order_by(Discovery.discovered_at.desc()).limit(200)).all()

@router.post('/discoveries/{did}/save')
def save(did: int, pid: int = Depends(profile_id)):
    with session() as s:
        d = s.get(Discovery, did)
        if not d or d.profile_id != pid: raise HTTPException(404, 'Discovery not found')
        d.saved = not d.saved
        s.add(d); s.commit()
        return {'saved': d.saved}
