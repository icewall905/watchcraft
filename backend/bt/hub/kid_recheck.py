"""Re-run the kid-safety picture check over every visible image on child profiles' Mood Boards.

    python -m bt.hub.kid_recheck            # report only
    python -m bt.hub.kid_recheck --apply    # hide images that fail

For boards filled before every child add was checked. Failing images are hidden (as if the child removed
them), not deleted. Images the model could not judge (errors) are left alone and counted; run it again.
"""
import argparse
import sys

from fastapi import HTTPException
from sqlmodel import select

from ..db import session
from ..models import Profile
from .models import MoodAsset, MoodPin
from .moodboard_share import _kid_check

BATCH = 40


def recheck(profile_ids: list[int] | None = None, apply: bool = False) -> dict:
    with session() as s:
        q = select(Profile.id).where(Profile.role == 'child')
        if profile_ids:
            q = q.where(Profile.id.in_(profile_ids))
        kids = list(s.exec(q).all())
        ids = list(s.exec(select(MoodPin.id).join(MoodAsset, MoodAsset.pin_id == MoodPin.id)
                          .where(MoodPin.profile_id.in_(kids), MoodPin.hidden == False)  # noqa: E712
                          .order_by(MoodPin.id)).all()) if kids else []
    out = {'profiles': len(kids), 'checked': 0, 'passed': 0, 'failed': [], 'unchecked': 0}
    for start in range(0, len(ids), BATCH):
        with session() as s:
            pictures = [(i, a.data) for i in ids[start:start + BATCH] if (a := s.get(MoodAsset, i))]
        unchecked: set[int] = set()
        ok = _kid_check(pictures, unchecked)
        bad = [i for i, _ in pictures if i not in ok and i not in unchecked]
        out['checked'] += len(pictures) - len(unchecked)
        out['passed'] += len(ok)
        out['unchecked'] += len(unchecked)
        out['failed'] += bad
        if apply and bad:
            with session() as s:
                for pin in s.exec(select(MoodPin).where(MoodPin.id.in_(bad))).all():
                    pin.hidden = True
                    s.add(pin)
                s.commit()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='hide images that fail (default: report only)')
    ap.add_argument('--profile', type=int, action='append', help='child profile id (repeatable; default: all)')
    args = ap.parse_args(argv)
    try:
        r = recheck(args.profile, args.apply)
    except HTTPException as e:  # vision model off: nothing can be judged
        print(e.detail, file=sys.stderr)
        return 2
    verb = 'hidden' if args.apply else 'would be hidden (use --apply)'
    print(f"{r['profiles']} child profile(s), {r['checked']} image(s) judged, {r['passed']} passed, "
          f"{len(r['failed'])} failed → {verb}, {r['unchecked']} not judged (model errors; run again)")
    if r['failed']:
        print('failed pin ids:', ', '.join(map(str, r['failed'])))
    return 1 if r['unchecked'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
