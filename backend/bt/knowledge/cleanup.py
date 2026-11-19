"""Review Mood Board teachings learned while the curator let weak pictures through, and retire the harsh ones.

Owners rejected hundreds of stock photos from sources the board no longer uses, often with the canned note
"Off-topic for this board". Teachings drawn from those reviews ("avoid generic portraits", "exclude denim jackets")
make the improved judge stricter than the owner. A teaching is proposed for retirement when most of its evidence is
such a source reject with few owner notes, or when it is a negative rule backed mostly by canned/no-note rejects
(title-only inference). Preferences (positive teachings) are always kept: they do not make the judge harsher.

Retiring marks the teaching `superseded` (restorable from Knowledge, unlike `rejected` it blocks nothing) and drops
its statements from the current knowledge in a new `cleanup` version. User-authored, locked or explicit statements
and corrections are never touched.

    python -m bt.knowledge.cleanup                 # dry run: every profile's mood teachings with a verdict
    python -m bt.knowledge.cleanup --apply 141 206 # retire exactly these teaching ids
"""
import argparse
import time

from sqlmodel import select

from ..db import session
from .models import Teaching

CANNED = {'', 'off-topic for this board'}
SOURCE_SHARE = 0.5


def assess(s, t: Teaching) -> dict:
    """Evidence behind one mood teaching and a proposed action ('retire' or 'keep') with the reason."""
    from ..hub.board_quality import source_rejects
    from ..hub.models import MoodFeedback
    pins = [int(sig['ref_id']) for sig in t.signals or [] if isinstance(sig, dict) and sig.get('ref_type') == 'pin'
            and str(sig.get('ref_id', '')).isdigit()]
    rows = s.exec(select(MoodFeedback).where(MoodFeedback.pin_id.in_(pins))).all() if pins else []
    covered = source_rejects(s, t.board_id, rows) if t.board_id and rows else set()
    bad = [f for f in rows if f.verdict == 'bad']
    canned = [f for f in bad if ' '.join(f.note.lower().split()).strip('. ') in CANNED]
    share = len(covered) / len(rows) if rows else 0.0
    out = {'id': t.id, 'profile': t.profile_id, 'board': t.board_id, 'status': t.status, 'polarity': t.polarity,
           'text': t.text, 'evidence': len(rows), 'source_rejects': len(covered), 'canned_rejects': len(canned)}
    if t.user_authored or t.origin != 'mood':
        return {**out, 'action': 'keep', 'why': 'written by the owner'}
    if t.polarity > 0:
        return {**out, 'action': 'keep', 'why': 'preference, not a restriction: it does not make the judge harsher'}
    if not bad:
        return {**out, 'action': 'keep', 'why': 'no rejected evidence left; left for the owner to decide'}
    explicit = len(bad) - len(canned)
    if len(canned) >= 0.75 * len(bad):
        return {**out, 'action': 'retire', 'why': f'{len(canned)}/{len(bad)} rejects had no or the canned note: inferred from titles'}
    if len(covered) >= SOURCE_SHARE * len(rows) and explicit < len(bad) / 2:
        return {**out, 'action': 'retire', 'why': f'{len(covered)}/{len(rows)} reviews were of sources now held back, few owner notes'}
    return {**out, 'action': 'keep', 'why': f'{explicit} of {len(bad)} rejects carry the owner\'s own words'}


def review(profile_id: int | None = None) -> list[dict]:
    with session() as s:
        q = select(Teaching).where(Teaching.origin == 'mood', Teaching.status.in_(['pending', 'merged']))
        if profile_id:
            q = q.where(Teaching.profile_id == profile_id)
        return [assess(s, t) for t in s.exec(q.order_by(Teaching.profile_id, Teaching.board_id, Teaching.id)).all()]


def retire(ids: list[int]) -> dict:
    """Supersede these mood teachings and drop statements that rest only on retired teachings."""
    from . import render, weave
    done, versions = [], {}
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        rows = s.exec(select(Teaching).where(Teaching.id.in_(ids), Teaching.origin == 'mood',
                                             Teaching.user_authored == False)).all()  # noqa: E712
        now = time.time()
        by_profile: dict[int, set[int]] = {}
        for t in rows:
            if t.status not in ('pending', 'merged'):
                continue
            t.status, t.updated_at = 'superseded', now
            t.note = ('Retired by the 2026-11 Mood Board cleanup: learned from pictures the old curator let through. '
                      + (t.note or ''))[:600]
            s.add(t)
            by_profile.setdefault(t.profile_id, set()).add(t.id)
            done.append(t.id)
        for pid, retired in by_profile.items():
            current = render.current(s, pid)
            if current is None:
                continue
            sections = weave._normalized(current.sections)
            removed = 0
            for sec in sections:
                keep = []
                for st in sec['statements']:
                    tids = set(st.get('teaching_ids') or [])
                    protected = st.get('locked') or st.get('confidence') == 'explicit'
                    if tids and tids <= retired and not protected:
                        removed += 1
                        continue
                    if tids & retired and not protected:
                        st['teaching_ids'] = sorted(tids - retired)
                    keep.append(st)
                sec['statements'] = keep
            if removed:
                pk = weave.write_version(s, pid, sections, 'cleanup', current, extra={'retired': sorted(retired)})
                versions[pid] = {'version': pk.version, 'statements_removed': removed}
        s.commit()
    for pid in versions:
        try:
            from .api import _changed
            _changed(pid)
        except Exception:  # noqa: BLE001 - consumers re-read on their next run anyway
            pass
    return {'retired': done, 'versions': versions}


def main():
    ap = argparse.ArgumentParser(description='Review or retire Mood Board teachings (dry run by default).')
    ap.add_argument('--profile', type=int)
    ap.add_argument('--apply', type=int, nargs='+', metavar='TEACHING_ID')
    args = ap.parse_args()
    if args.apply:
        print(retire(args.apply))
        return
    for r in review(args.profile):
        print(f"{r['action']:6s} #{r['id']:<4d} p{r['profile']} b{r['board']} {r['status']:7s} "
              f"{'+' if r['polarity'] > 0 else '-'} ev={r['evidence']:<2d} src={r['source_rejects']:<2d} "
              f"canned={r['canned_rejects']:<2d} | {r['text'][:95]} || {r['why']}")


if __name__ == '__main__':
    main()
