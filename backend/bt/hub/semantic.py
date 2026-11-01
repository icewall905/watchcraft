"""Semantic search over stored items, using the configured OpenAI-compatible embeddings endpoint.

Optional by design: without an embeddings model the feature reports why it is unavailable and
keyword search stays the only path. Vectors are stored per item and per model, so switching
models re-embeds rather than comparing vectors from different spaces.
"""
import array
import math
import time
from collections.abc import Sequence

import httpx
from sqlmodel import select

from ..db import get_settings, session
from .models import ContentItem, CuratorDiscovery, ItemState, ItemVector

BATCH = 32        # items embedded per scheduler tick
MAX_COMPARE = 20000  # vectors compared per query; beyond this the oldest are skipped
THROTTLE = 120.0


def base_url(url: str) -> str:
    """Accept the API base or the full /embeddings URL; both name the same endpoint.

    The settings hint invites pasting an endpoint, and a pasted ".../v1/embeddings" would
    otherwise be posted to as ".../v1/embeddings/embeddings".
    """
    url = (url or '').strip().rstrip('/')
    return url[:-len('/embeddings')] if url.endswith('/embeddings') else url


def config() -> dict:
    """Embeddings may live on their own server; an empty endpoint falls back to the judge's.

    A separate server never receives the judge's key — it uses only its own, so a typo cannot
    send one service's credential to another. On the judge's server its own key wins if set.
    """
    llm = get_settings()['llm']
    own = (llm.get('embed_base_url') or '').strip()
    key = (llm.get('embed_api_key') or '').strip()
    return {'base_url': base_url(own or llm.get('base_url') or ''),
            'model': (llm.get('embed_model') or '').strip(),
            'api_key': key if own else (key or (llm.get('api_key') or '')),
            'timeout_s': 60}


def available() -> tuple[bool, str]:
    cfg = config()
    if not cfg['base_url']:
        return False, 'Configure the AI connection under Settings → AI.'
    if not cfg['model']:
        return False, 'Set an embeddings model under Settings → AI.'
    return True, ''


def embed(texts: list[str]) -> list[list[float]]:
    """One embeddings request. Raises ValueError with a readable reason on any failure."""
    ok, reason = available()
    if not ok:
        raise ValueError(reason)
    cfg = config()
    headers = {'Content-Type': 'application/json'}
    if cfg['api_key']:
        headers['Authorization'] = f'Bearer {cfg["api_key"]}'
    try:
        r = httpx.post(f'{cfg["base_url"]}/embeddings', json={'model': cfg['model'], 'input': texts},
                       headers=headers, timeout=cfg['timeout_s'])
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        raise ValueError(f'The embeddings endpoint returned HTTP {e.response.status_code}.') from e
    except (httpx.HTTPError, ValueError) as e:
        raise ValueError(f'The embeddings endpoint is unreachable ({type(e).__name__}).') from e
    rows = data.get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise ValueError('The embeddings endpoint returned an unexpected response.')
    out = []
    for row in rows:
        vec = row.get('embedding') if isinstance(row, dict) else None
        if not isinstance(vec, list) or not vec:
            raise ValueError('The embeddings endpoint returned an empty vector.')
        out.append([float(x) for x in vec])
    return out


def _pack(vec: list[float]) -> bytes:
    """Store a vector as packed float32. 768 dims become 3,072 bytes instead of ~17,000."""
    return array.array('f', vec).tobytes()


def _unpack(blob, dim: int) -> array.array | None:
    """Unpack a stored vector, or None if it cannot be compared with a query.

    A row written before the float32 change holds JSON text, and a truncated write holds a
    partial BLOB. Both are skipped rather than raised, so one bad row cannot break search.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    raw = bytes(blob)
    if dim and len(raw) != dim * 4:
        return None
    vec = array.array('f')
    try:
        vec.frombytes(raw)
    except ValueError:
        return None
    return vec


def _norm(vec: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vec)) or 1.0


def _visible(s, pid: int, ids: list[int]) -> set[int]:
    """Same rule as the rest of the hub: found by this profile's curators, or touched by it."""
    if not ids:
        return set()
    found = set(s.exec(select(CuratorDiscovery.item_id)
                       .where(CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.in_(ids))).all())
    found |= set(s.exec(select(ItemState.item_id)
                        .where(ItemState.profile_id == pid, ItemState.item_id.in_(ids))).all())
    return found


def embed_pending(limit: int = BATCH) -> int:
    """Embed the newest items that have no vector for the current model. Bounded per call."""
    ok, _ = available()
    if not ok:
        return 0
    cfg = config()
    with session() as s:
        done = select(ItemVector.item_id).where(ItemVector.model == cfg['model'])
        rows = s.exec(select(ContentItem.id, ContentItem.title, ContentItem.summary)
                      .where(ContentItem.id.not_in(done)).order_by(ContentItem.id.desc()).limit(limit)).all()
    if not rows:
        return 0
    texts = [(f'{title or ""}\n{summary or ""}'.strip() or 'empty')[:2000] for _, title, summary in rows]
    vectors = embed(texts)
    with session() as s:
        for (iid, _, _), vec in zip(rows, vectors):
            s.merge(ItemVector(item_id=iid, model=cfg['model'], dim=len(vec), vector=_pack(vec)))
        s.commit()
    return len(rows)


def search(q: str, pid: int, limit: int = 20) -> list[tuple[int, float]]:
    """Rank stored items by cosine similarity to the query. Returns (item_id, score) pairs."""
    if not q.strip():
        return []
    cfg = config()
    query = embed([q.strip()[:2000]])[0]
    qn = _norm(query)
    with session() as s:
        rows = s.exec(select(ItemVector.item_id, ItemVector.vector, ItemVector.dim)
                      .where(ItemVector.model == cfg['model'])
                      .order_by(ItemVector.item_id.desc()).limit(MAX_COMPARE)).all()
        visible = _visible(s, pid, [r[0] for r in rows])
    scored = []
    for iid, raw, dim in rows:
        if iid not in visible:
            continue
        vec = _unpack(raw, dim)
        if vec is None or len(vec) != len(query):
            continue
        dot = sum(a * b for a, b in zip(query, vec))
        scored.append((dot / (qn * _norm(vec)), iid))
    scored.sort(reverse=True)
    return [(iid, round(score, 4)) for score, iid in scored[:limit]]


def status() -> dict:
    ok, reason = available()
    cfg = config()
    with session() as s:
        indexed = s.exec(select(ItemVector.item_id).where(ItemVector.model == cfg['model'])).all() if ok else []
        total = s.exec(select(ContentItem.id)).all()
    # Past MAX_COMPARE the oldest vectors stop being compared, silently. Reporting both numbers
    # is the only way that cliff is visible before it starts costing answers.
    return {'available': ok, 'reason': reason, 'model': cfg['model'],
            'indexed': len(indexed), 'items': len(total),
            'compared': min(len(indexed), MAX_COMPARE), 'cap': MAX_COMPARE}


def sweep(throttled=True) -> dict:
    """Scheduler entry point: keep the index moving without blocking a tick."""
    ok, _ = available()
    if not ok:
        return {'skipped': 'not configured'}
    from ..db import get_kv, save_setting
    state = get_kv('watchcraft_semantic_sweep', {})
    if throttled and time.time() - float(state.get('at') or 0) < THROTTLE:
        return {'skipped': True}
    try:
        done = embed_pending()
    except ValueError as e:
        save_setting('watchcraft_semantic_sweep', {'at': time.time(), 'error': str(e)[:200]})
        return {'error': str(e)[:200]}
    save_setting('watchcraft_semantic_sweep', {'at': time.time(), 'embedded': done})
    return {'embedded': done}
