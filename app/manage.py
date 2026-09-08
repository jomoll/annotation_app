"""Small admin CLI (run from the repo root):

    python -m app.manage list-users
    python -m app.manage set-role  <email> admin|rater
    python -m app.manage reset-password <email>        # prints a new random password
    python -m app.manage export  [out.csv]             # all ratings, one row per saved rating
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import storage  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "list-users":
        for email, acc in sorted(auth.load_accounts().items()):
            print(f"{email:40s} {acc.get('role', 'rater'):6s} {acc.get('name', '')}  ({acc.get('position', '')})")
        return 0
    if cmd == "set-role":
        if len(args) != 2 or args[1] not in {"admin", "rater"}:
            print("usage: set-role <email> admin|rater")
            return 2
        ok = auth.set_role(args[0], args[1])
        print("ok" if ok else f"no account for {args[0]}")
        return 0 if ok else 1
    if cmd == "reset-password":
        if len(args) != 1:
            print("usage: reset-password <email>")
            return 2
        new_pw = secrets.token_urlsafe(9)
        ok = auth.set_password(args[0], new_pw)
        print(f"new password for {args[0]}: {new_pw}" if ok else f"no account for {args[0]}")
        return 0 if ok else 1
    if cmd == "export":
        out = Path(args[0]) if args else Path("ratings_export.csv")
        df = storage.all_ratings_history()
        df.to_csv(out, index=False)
        print(f"wrote {len(df)} rows to {out}")
        return 0
    print(f"unknown command: {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
