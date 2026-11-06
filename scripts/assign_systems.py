#!/usr/bin/env python3
"""Assign each evaluation study to exactly one candidate system, balanced -- any dataset.

    python scripts/assign_systems.py --references refs.csv --studies eval_ids.txt \\
        --systems modelA modelB modelC injected retrieved --out data/raw/us/assignment.csv

One candidate per reference, as in the chest-CT, chest X-ray and brain-MRI arms (5 systems x 40
studies = 200). Run it BEFORE generating, then run each model only on its own studies.

If the references carry a `normal` column (0/1), every system gets the same normal/abnormal mix.
That matters more than it looks: normal studies are easy for some metrics and hard for others, and
in the chest X-ray arm normal-vs-abnormal explained about half the variance in reader scores, so a
system handed a normal-heavy slice would look better for reasons that have nothing to do with it.

Writes study_id,system,normal -- the file scripts/build_pool.py --assignment reads.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from reporttools.reports import load_reports  # noqa: E402


def deal(study_ids: list[str], systems: list[str], normal: dict[str, bool | None], seed: int) -> dict[str, str]:
    """Round-robin within each normal/abnormal stratum, from a seeded shuffle."""
    rng = random.Random(seed)
    out: dict[str, str] = {}
    offset = 0
    for flag in (True, False, None):
        stratum = sorted(s for s in study_ids if normal.get(s) is flag)
        rng.shuffle(stratum)
        for i, sid in enumerate(stratum):
            out[sid] = systems[(i + offset) % len(systems)]
        # carry the rotation across strata so the totals stay as even as possible
        offset = (offset + len(stratum)) % len(systems)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--references", nargs="+", type=Path, required=True)
    ap.add_argument("--studies", type=Path, help="evaluation study ids, one per line (default: all references)")
    ap.add_argument("--id-col")
    ap.add_argument("--systems", nargs="+", required=True)
    ap.add_argument("--seed", type=int, default=20251102)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    refs = {r["study_id"]: r for r in load_reports(args.references, args.id_col)}
    ids = ([l.strip() for l in args.studies.read_text().splitlines() if l.strip() and not l.startswith("#")]
           if args.studies else list(refs))
    absent = [s for s in ids if s not in refs]
    if absent:
        sys.exit(f"{len(absent)} studies have no reference, e.g. {absent[:3]}")

    def flag(r):
        v = (r.get("normal") or "").strip().lower()
        return True if v in ("1", "true", "yes") else False if v in ("0", "false", "no") else None

    normal = {s: flag(refs[s]) for s in ids}
    got = deal(ids, args.systems, normal, args.seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["study_id", "system", "normal"])
        for s in ids:
            w.writerow([s, got[s], "" if normal[s] is None else int(normal[s])])

    per = Counter(got.values())
    print(f"{len(ids)} studies -> {dict(sorted(per.items()))}")
    if any(v is not None for v in normal.values()):
        for sysname in args.systems:
            mine = [s for s, v in got.items() if v == sysname and normal[s] is not None]
            share = sum(normal[s] for s in mine) / len(mine) if mine else 0
            print(f"  {sysname:20s} normal share {share:.0%} of {len(mine)}")
    else:
        print("  (no `normal` column: assignment is balanced in count only)")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
