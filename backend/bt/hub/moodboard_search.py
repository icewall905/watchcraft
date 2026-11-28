"""Search Mood Board pins by what the picture shows.

Each pin's image (plus its caption) is embedded with the configured embeddings model, which must
accept images (llama.cpp multimodal, e.g. embeddinggemma-2). A text query lands in the same space,
so "flower dress with stars" finds the picture even when its caption says "Product 123".
Vectors live in SQLite next to the pins, keyed per model like the article index in `semantic`.
"""
import logging
import time

from sqlalchemy import func
from sqlmodel import select

from ..db import get_kv, save_setting, session
from . import semantic
from .models import MoodAsset, MoodBoard, MoodPin, MoodPinVector

log = logging.getLogger(__name__)
BATCH = 8            # pins per embeddings request; the server has one slot
THROTTLE = 15.0      # seconds between sweep batches
MAX_COMPARE = 20000  # 50 boards x 1,000 pins is 50,000; compare the newest
IMAGE_EDGE = 768
STATE_KEY = 'watchcraft_moodboard_index'


def _input(title: str, data: bytes) -> dict:
    from .moodboards import vision_image
    parts = [{'type': 'text', 'text': (title or '').strip()[:300]}] if (title or '').strip() else []
    return {'content': parts + [{'type': 'image_url', 'image_url': {'url': vision_image(data, IMAGE_EDGE)}}]}


def _store(s, pin_id: int, model: str, vec: list[float] | None) -> None:
    s.merge(MoodPinVector(pin_id=pin_id, model=model, dim=len(vec) if vec else 0,
                          vector=semantic._pack(vec) if vec else b''))


def embed_pending(limit: int = BATCH) -> int:
    """Embed the newest pins with no vector under the current model. Returns pins handled."""
    ok, _ = semantic.available()
    if not ok:
        return 0
    model = semantic.config()['model']
    with session() as s:
        done = select(MoodPinVector.pin_id).where(MoodPinVector.model == model)
        rows = s.exec(select(MoodPin.id, MoodPin.title, MoodAsset.data)
                      .join(MoodAsset, MoodAsset.pin_id == MoodPin.id)
                      .where(MoodPin.id.not_in(done)).order_by(MoodPin.id.desc()).limit(limit)).all()
    if not rows:
        return 0
    inputs, ids, broken = [], [], []
    for pin_id, title, data in rows:
        try:
            inputs.append(_input(title, data))
            ids.append(pin_id)
        except Exception:
            broken.append(pin_id)  # Undecodable image: mark it, never retry.
    vectors: dict[int, list[float] | None] = {pid: None for pid in broken}
    if inputs:
        try:
            vectors.update(zip(ids, semantic.embed(inputs, timeout_s=120)))
        except ValueError:
            if len(inputs) == 1:
                raise
            # One bad image must not stall the batch: retry singly, mark only real failures.
            for pin_id, item in zip(ids, inputs):
                try:
                    vectors[pin_id] = semantic.embed([item], timeout_s=120)[0]
                except ValueError as e:
                    if 'HTTP' not in str(e) or 'HTTP 429' in str(e) or 'HTTP 503' in str(e):
                        raise  # Server down or busy: leave unmarked, retry next sweep.
                    vectors[pin_id] = None  # The server rejects this image itself.
    with session() as s:
        live = set(s.exec(select(MoodPin.id).where(MoodPin.id.in_(list(vectors)))).all())
        for pin_id, vec in vectors.items():
            if pin_id in live:  # Pin deleted mid-request: do not leave an orphan for a reused id.
                _store(s, pin_id, model, vec)
        s.commit()
    return len(vectors)


def sweep(throttled: bool = True) -> dict:
    ok, _ = semantic.available()
    if not ok:
        return {'skipped': 'not configured'}
    state = get_kv(STATE_KEY, {})
    if throttled and time.time() - float(state.get('at') or 0) < THROTTLE:
        return {'skipped': True}
    try:
        done = embed_pending()
    except ValueError as e:
        save_setting(STATE_KEY, {'at': time.time(), 'error': str(e)[:200]})
        log.warning('Mood Board image indexing failed: %s', str(e)[:200])
        return {'error': str(e)[:200]}
    save_setting(STATE_KEY, {'at': time.time(), 'embedded': done})
    return {'embedded': done}


def _visible(s, pid: int):
    """Pins on boards this profile owns or works on; children only see what they may see (moodboards.kid_visible)."""
    from sqlalchemy import and_, or_
    from ..access import is_child
    from .moodboards import accessible
    boards = select(MoodBoard.id).where(accessible(pid))
    cond = MoodPin.board_id.in_(boards) & (MoodPin.hidden == False)  # noqa: E712
    if is_child(s, pid):
        own = select(MoodBoard.id).where(MoodBoard.profile_id == pid)
        cond = cond & or_(MoodPin.kid_state.in_(('ok', 'override')),
                          and_(MoodPin.kid_state == '', MoodPin.profile_id == pid, MoodPin.board_id.in_(own)))
    return cond


def _counts(s, pid: int, model: str) -> tuple[int, int]:
    visible = _visible(s, pid)
    total = s.exec(select(func.count()).select_from(MoodPin).where(visible)).one()
    indexed = s.exec(select(func.count()).select_from(MoodPin)
                     .join(MoodPinVector, MoodPinVector.pin_id == MoodPin.id)
                     .where(visible, MoodPinVector.model == model, MoodPinVector.dim > 0)).one()
    return indexed, total


def status(pid: int) -> dict:
    ok, reason = semantic.available()
    model = semantic.config()['model']
    with session() as s:
        indexed, total = _counts(s, pid, model)
    state = get_kv(STATE_KEY, {})
    return {'available': ok, 'reason': reason, 'model': model, 'indexed': indexed, 'pins': total,
            'error': state.get('error', '')}


def search(q: str, pid: int, board_id: int | None = None, limit: int = 40) -> list[tuple[int, float]]:
    """Rank this profile's visible pins by cosine similarity to the query. (pin_id, score) pairs."""
    q = (q or '').strip()[:300]
    if not q:
        return []
    model = semantic.config()['model']
    query = semantic.embed([q])[0]
    qn = semantic._norm(query)
    with session() as s:
        stmt = (select(MoodPinVector.pin_id, MoodPinVector.vector, MoodPinVector.dim)
                .join(MoodPin, MoodPin.id == MoodPinVector.pin_id)
                .where(MoodPinVector.model == model, MoodPinVector.dim == len(query), _visible(s, pid)))
        if board_id is not None:
            stmt = stmt.where(MoodPin.board_id == board_id)
        rows = s.exec(stmt.order_by(MoodPin.id.desc()).limit(MAX_COMPARE)).all()
    scored = []
    for pin_id, raw, dim in rows:
        vec = semantic._unpack(raw, dim)
        if vec is None:
            continue
        scored.append((sum(a * b for a, b in zip(query, vec)) / (qn * semantic._norm(vec)), pin_id))
    scored.sort(reverse=True)
    return [(pin_id, round(score, 4)) for score, pin_id in scored[:max(1, min(limit, 100))]]


def search_response(q: str, pid: int, board_id: int | None, limit: int) -> dict:
    from .moodboards import pin_json
    info = status(pid)
    base = {'available': info['available'], 'reason': info['reason'], 'indexed': info['indexed'],
            'pins_total': info['pins'], 'pins': []}
    if not info['available']:
        return base
    try:
        ranked = search(q, pid, board_id, limit)
    except ValueError as e:
        return {**base, 'available': False, 'reason': str(e)}
    with session() as s:
        pins = {p.id: p for p in s.exec(select(MoodPin).where(MoodPin.id.in_([i for i, _ in ranked] or [0]))).all()}
        from .moodboards import accessible
        from ..access import is_child
        names = dict(s.exec(select(MoodBoard.id, MoodBoard.name).where(accessible(pid))).all())
        grown_up = not is_child(s, pid)
        from .moodboards import note_map
        notes = note_map(s, list(pins))
    out = []
    for pin_id, score in ranked:
        pin = pins.get(pin_id)
        if pin:
            out.append({**pin_json(pin, note=notes.get(pin_id), grown_up=grown_up), 'board_name': names.get(pin.board_id, ''), 'similarity': score})
    return {**base, 'pins': out}
