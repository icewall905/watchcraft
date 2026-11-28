"""Shared Mood Boards from the command line (admin tasks on the server).

    python -m bt.hub.board_members list <board>
    python -m bt.hub.board_members add <board> <profile>          # same as Share → Work together
    python -m bt.hub.board_members merge <from board> <into board>  # move pictures over, then delete <from board>

`merge` is for folding a child's old copy of a board into the shared one: its visible pictures are copied (picture
bytes included, duplicates by source skipped) and keep who added them and whether they were saved. Pictures from a
child's own board already passed the child rule, so they count as kid-checked; anything else waits for the kid sweep.
"""
import argparse
import sys

from fastapi import HTTPException
from sqlmodel import select

from ..access import is_child
from ..db import session
from .models import MoodAsset, MoodBoard, MoodPin
from . import moodboards
from .moodboard_share import add_member, members_json


def merge(src_id: int, dst_id: int) -> dict:
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        src, dst = s.get(MoodBoard, src_id), s.get(MoodBoard, dst_id)
        if not src or not dst or src_id == dst_id:
            raise SystemExit('Both boards must exist and differ.')
        trusted = is_child(s, src.profile_id)
        have = set(s.exec(select(MoodPin.source_url).where(MoodPin.board_id == dst_id)).all())
        copied = skipped = 0
        for pin in s.exec(select(MoodPin).join(MoodAsset, MoodAsset.pin_id == MoodPin.id).where(
                MoodPin.board_id == src_id, MoodPin.hidden == False).order_by(MoodPin.id)).all():  # noqa: E712
            if pin.source_url in have or moodboards.stored(s, dst_id) >= moodboards.MAX_PINS:
                skipped += 1
                continue
            asset = s.get(MoodAsset, pin.id)
            copy = MoodPin(board_id=dst_id, profile_id=pin.profile_id, title=pin.title, source_url=pin.source_url,
                           image_url=pin.image_url, media_type=pin.media_type, width=pin.width, height=pin.height,
                           attribution=pin.attribution, license=pin.license, saved=pin.saved, ai_flag=pin.ai_flag,
                           kid_state='ok' if trusted else '', created_at=pin.created_at)
            s.add(copy)
            s.flush()
            s.add(MoodAsset(pin_id=copy.id, mime=asset.mime, data=asset.data))
            have.add(pin.source_url)
            copied += 1
        s.commit()
        owner = src.profile_id
    moodboards.remove(src_id, pid=owner)
    return {'copied': copied, 'skipped': skipped, 'deleted': src_id}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    ls = sub.add_parser('list', help='owner and members of a board')
    ls.add_argument('board', type=int)
    add = sub.add_parser('add', help='add a profile to a board')
    add.add_argument('board', type=int)
    add.add_argument('profile', type=int)
    mg = sub.add_parser('merge', help='copy a board\'s pictures into another board, then delete it')
    mg.add_argument('source', type=int)
    mg.add_argument('into', type=int)
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'merge':
            print(merge(args.source, args.into))
            return 0
        with session() as s:
            board = s.get(MoodBoard, args.board)
            if not board:
                print('No such board.', file=sys.stderr)
                return 1
            if args.cmd == 'add':
                s.connection().exec_driver_sql('BEGIN IMMEDIATE')
                add_member(s, board, args.profile, board.profile_id)
                s.commit()
            print(members_json(s, board))
    except HTTPException as e:
        print(e.detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
