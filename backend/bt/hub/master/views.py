"""Read-side helpers: what the curated feeds show, shared by the hub endpoints."""
from sqlalchemy import or_
from sqlmodel import Session, select

from ..models import CuratorDiscovery, ItemState, ItemVerdict

HIDDEN = ('set_aside', 'reject')


def has_verdicts(s: Session, pid: int) -> bool:
    """Whether the curator has judged anything for this profile yet."""
    return s.exec(select(ItemVerdict.item_id).where(ItemVerdict.profile_id == pid).limit(1)).first() is not None


def curated(pid: int, col):
    """Everything except what the curator set aside or rejected for this profile. Items it has
    not judged yet stay visible, so a feed never empties while a pass is pending."""
    return col.not_in(select(ItemVerdict.item_id).where(ItemVerdict.profile_id == pid, ItemVerdict.tier.in_(HIDDEN)))


def visible(pid: int, col):
    """Items this profile may see: found by its curators, or touched by it. Verdicts can outlive
    a deleted curator, so every verdict query joins this."""
    return or_(col.in_(select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid)),
               col.in_(select(ItemState.item_id).where(ItemState.profile_id == pid)))


def visible_set(s: Session, pid: int, ids) -> set[int]:
    ids = list(ids)
    if not ids:
        return set()
    found = set(s.exec(select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id.in_(ids))).all())
    return found | set(s.exec(select(ItemState.item_id).where(ItemState.profile_id == pid, ItemState.item_id.in_(ids))).all())
