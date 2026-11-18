"""Small WebP previews of captured item pictures, made once and kept on disk.

The Front Page shows dozens of pictures; sending each full-size capture made it slow. Previews are
generated ahead of time by the Front Page snapshot (frontpage.py) and on first request otherwise.
"""
import io
import logging
import os
from pathlib import Path

from PIL import Image, ImageOps
from sqlalchemy import func
from sqlmodel import Session, select

from ..config import DATA_DIR
from .models import CapturedAsset

log = logging.getLogger(__name__)
WIDTHS = (240, 480, 800)
ROOT = DATA_DIR / 'thumbs'
Image.MAX_IMAGE_PIXELS = 60_000_000


def width(w: int | None) -> int | None:
    """Nearest supported width at or above the request, or None for the original."""
    if not w:
        return None
    return next((x for x in WIDTHS if x >= w), WIDTHS[-1])


def path(iid: int, w: int, size: int) -> Path:
    """Keyed by the capture's byte size too, so a re-captured picture never shows an old preview."""
    return ROOT / str(w) / f'{iid % 1000:03d}' / f'{iid}-{size}.webp'


def _sizes(s: Session, ids) -> dict[int, int]:
    return dict(s.exec(select(CapturedAsset.item_id, func.length(CapturedAsset.data))
                       .where(CapturedAsset.kind == 'image', CapturedAsset.item_id.in_(list(ids)))).all())


def render(data: bytes, w: int) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)  # first frame of an animation
            im = ImageOps.exif_transpose(im)
            im = im.convert('RGBA' if im.mode in ('RGBA', 'LA', 'P') else 'RGB')
            if im.width > w:
                im.thumbnail((w, w * 4), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, 'WEBP', quality=78, method=4)
            return out.getvalue()
    except Exception:  # noqa: BLE001 — an unreadable capture just has no preview
        log.debug('thumbnail failed', exc_info=True)
        return None


def get(s: Session, iid: int, w: int) -> bytes | None:
    """Preview bytes, made now if missing. None when there is no readable picture."""
    size = _sizes(s, [iid]).get(iid)
    if size is None:
        return None
    p = path(iid, w, size)
    try:
        return p.read_bytes()
    except FileNotFoundError:
        pass
    asset = s.get(CapturedAsset, (iid, 'image'))
    if not asset:
        return None
    data = render(asset.data, w)
    if data is None:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_bytes(data)
    tmp.replace(p)
    return data


def warm(s: Session, ids, w: int = 480) -> int:
    """Make missing previews for these items; returns how many were made."""
    sizes = _sizes(s, {i for i in ids if i})
    made = 0
    for iid, size in sizes.items():
        if not path(iid, w, size).exists() and get(s, iid, w) is not None:
            made += 1
    return made
