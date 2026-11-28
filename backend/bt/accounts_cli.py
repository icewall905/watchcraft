"""Account recovery and invites from the command line (docs/ACCOUNTS.md).

    python -m bt.accounts_cli invite <username> [--admin] [--base https://host]
    python -m bt.accounts_cli reset <username> [--base https://host]
    python -m bt.accounts_cli list
    python -m bt.accounts_cli profiles
    python -m bt.accounts_cli assign <profile id or exact name> <username>
"""
import argparse
import os
import sys

from sqlmodel import func, select

from . import accounts as A
from .db import init_db, session
from .models import Account, AccountSession, Profile


def _base(arg: str) -> str:
    return (arg or os.environ.get("BT_PUBLIC_URL", "")).rstrip("/")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bt.accounts_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("invite", help="create an account and print its registration link")
    inv.add_argument("username")
    inv.add_argument("--admin", action="store_true")
    inv.add_argument("--base", default="")
    rst = sub.add_parser("reset", help="print a password reset (or new registration) link")
    rst.add_argument("username")
    rst.add_argument("--base", default="")
    sub.add_parser("list", help="list accounts")
    sub.add_parser("profiles", help="list profiles and their owners")
    asg = sub.add_parser("assign", help="give an unassigned profile to an account")
    asg.add_argument("profile")
    asg.add_argument("username")
    args = ap.parse_args(argv)
    init_db()
    with session() as s:
        if args.cmd == "list":
            for acc in s.exec(select(Account).order_by(Account.id)).all():
                n_prof = s.exec(select(func.count()).select_from(Profile).where(Profile.account_id == acc.id)).one()
                n_sess = s.exec(select(func.count()).select_from(AccountSession).where(
                    AccountSession.account_id == acc.id, AccountSession.revoked_at == None)).one()  # noqa: E711
                state = "disabled" if acc.disabled else "registered" if acc.password_hash else "invited"
                print(f"{acc.id:>4}  {acc.username:<24} {acc.role:<6} {state:<10} profiles={n_prof} sessions={n_sess}")
            unowned = s.exec(select(func.count()).select_from(Profile).where(Profile.account_id == None)).one()  # noqa: E711
            if unowned:
                print(f"\n{unowned} profile(s) from before accounts wait for an administrator (Settings → Accounts).")
            return 0
        if args.cmd == "profiles":
            owners = {a.id: a.username for a in s.exec(select(Account)).all()}
            for p in s.exec(select(Profile).order_by(Profile.id)).all():
                print(f"{p.id:>4}  {p.emoji} {p.name:<24} {p.role:<6} {owners.get(p.account_id, '(unassigned)')}")
            return 0
        name = A.normalize_username(args.username)
        if args.cmd == "assign":
            acc = s.exec(select(Account).where(Account.username == name)).first()
            matches = [p for p in s.exec(select(Profile)).all()
                       if str(p.id) == args.profile or p.name == args.profile]
            if not acc or len(matches) != 1:
                print("Need one matching profile and an existing account.", file=sys.stderr)
                return 1
            p = matches[0]
            if p.account_id is not None:
                print(f"{p.name} already belongs to an account.", file=sys.stderr)
                return 1
            p.account_id, p.role = acc.id, ("adult" if p.role == "admin" else p.role)
            s.add(p)
            s.commit()
            print(f"{p.name} → {acc.username}")
            return 0
        acc = s.exec(select(Account).where(Account.username == name)).first()
        if args.cmd == "invite":
            if acc and acc.password_hash:
                print(f"{name} already exists and is registered; use 'reset'.", file=sys.stderr)
                return 1
            acc = acc or A.new_account(s, name, "admin" if args.admin else "user")
            if args.admin and acc.role != "admin":
                acc.role = "admin"
                s.add(acc)
                s.commit()
            token = A.create_invite(s, acc, "register")
        else:
            if not acc:
                print(f"No account named {name}.", file=sys.stderr)
                return 1
            token = A.create_invite(s, acc, "reset" if acc.password_hash else "register")
        print(f"{acc.username} ({acc.role}): {_base(args.base)}{A.invite_link(token)}  (valid 14 days, single use)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
