#!/usr/bin/env python3
"""Merge per-modality pools into the single data/cases.json the app serves.

    python scripts/merge_pools.py ct=data/cases_ct.json cxr=data/cases_cxr.json --out data/cases.json

Each input is NAME=PATH where NAME is a key of config.MODALITIES; every case gets "modality": NAME
(existing values are overwritten). Case ids must be unique across pools. Pool-level metadata is kept
per modality under meta.pools. Links, if present, are carried over untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from config import MODALITIES  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pools", nargs="+", metavar="MODALITY=PATH")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "cases.json")
    args = ap.parse_args(argv)

    cases, pools_meta, seen = [], {}, set()
    for spec in args.pools:
        name, _, path = spec.partition("=")
        if name not in MODALITIES:
            sys.exit(f"unknown modality {name!r}; add it to config.MODALITIES")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pool_cases = data["cases"] if isinstance(data, dict) else data
        for c in pool_cases:
            if c["id"] in seen:
                sys.exit(f"duplicate case id across pools: {c['id']}")
            seen.add(c["id"])
            c["modality"] = name
            cases.append(c)
        pools_meta[name] = (data.get("meta", {}) if isinstance(data, dict) else {}) | {"source_file": str(path), "n_cases": len(pool_cases)}
        print(f"{name:5s} {len(pool_cases):4d} cases from {path}")
    out = {"meta": {"merged_at": datetime.now(timezone.utc).isoformat(), "pools": pools_meta, "n_cases": len(cases)}, "cases": cases}
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(cases)} cases -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
