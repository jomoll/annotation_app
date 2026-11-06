"""Small admin CLI (run from the repo root):

    python -m app.manage list-users
    python -m app.manage set-role  <email> admin|rater
    python -m app.manage reset-password <email>        # prints a new random password
    python -m app.manage set-password <email> <pw>     # e.g. change the built-in admin password
    python -m app.manage export  [out.csv]             # all ratings, one row per saved rating
    python -m app.manage modalities <email>            # which arms this rater holds
    python -m app.manage grant-modality <email> <ct|cxr|mri>
    python -m app.manage plan-overlap <ct|cxr|mri> [--apply]   # default: dry run

`grant-modality` lets a rater who has finished one arm also rate another instead of recruiting
someone new. It records the invitation only: the studies themselves are assigned the first time
they open that tab, by the same least-covered-first rule as any other rater, and the two
assignments are kept apart by modality so neither can disturb the other.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assignment  # noqa: E402
import auth  # noqa: E402
import storage  # noqa: E402
from cases_data import CASES  # noqa: E402


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
    if cmd == "set-password":
        if len(args) != 2:
            print("usage: set-password <email> <password>")
            return 2
        if len(args[1]) < auth.MIN_PASSWORD_LEN:
            print(f"password must be at least {auth.MIN_PASSWORD_LEN} characters")
            return 2
        ok = auth.set_password(args[0], args[1])
        print("ok" if ok else f"no account for {args[0]}")
        return 0 if ok else 1
    if cmd in {"modalities", "grant-modality"}:
        if not args or (cmd == "grant-modality" and len(args) != 2):
            print(f"usage: {cmd} <email>" + (" <modality>" if cmd == "grant-modality" else ""))
            return 2
        email = args[0].strip().lower()
        if email not in auth.load_accounts():
            print(f"no account for {email}")
            return 1
        slug = auth.user_slug(email)
        if cmd == "grant-modality":
            if not assignment.grant_modality(slug, args[1]):
                print(f"unknown modality {args[1]!r}")
                return 2
        held = assignment.rater_modalities(slug, CASES)
        mod_of = {c["study_id"]: c["modality"] for c in CASES}
        counts: dict[str, int] = {m: 0 for m in held}
        for s in assignment.load().get(slug, []):
            m = mod_of.get(s)
            if m in counts:
                counts[m] += 1
        print(f"{email}: " + (", ".join(f"{m} ({counts[m]} studies assigned)" for m in held) or "none"))
        return 0
    if cmd == "plan-overlap":
        if not args:
            print("usage: plan-overlap <modality> [--apply]")
            return 2
        apply = "--apply" in args[1:]
        rep = assignment.plan_overlap(CASES, args[0], dry_run=not apply)
        print(("APPLIED" if apply else "DRY RUN (pass --apply to write)") + f": {rep['size']} shared studies")
        print("  per system:", rep["per_system"])
        print("  reordered :", rep["reordered"] or "none")
        if rep["skipped_started"]:
            print("  left alone (already rating):", rep["skipped_started"])
        return 0
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
