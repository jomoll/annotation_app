#!/usr/bin/env python3
"""Add a candidate system to an existing one-candidate-per-study pool, rebalancing so that every
model ends up with an equal share — without touching cases that already have ratings.

    python scripts/add_candidate.py adaragct=path/to/predictions.jsonl [--seed 17]

The prediction file is a CSV (volumename/prediction-style columns) or a JSONL with
`image` + `generated_text` (AdaRAG-CT format). Reg2RG-style region scaffolding is flattened.

Balancing is done within groups: every distinct set of studies assigned to a real rater
(data/assignments.json, test/admin logins excluded) forms a group, and the studies assigned to
nobody form one more. Within each group the models are equalised (a 100-study group with five
models gets 20 of each), so a rater who is halfway through keeps a balanced mix in what remains.
Cases with a saved rating are never replaced; studies the new set does not cover are never switched.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "app"))
from build_pool_ctrate import flatten_reg2rg  # noqa: E402


def load_predictions(path: Path) -> dict[str, str]:
    if path.suffix == ".jsonl":
        out = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            vol = str(r.get("image") or r.get("volume") or r.get("VolumeName") or "").split("/")[-1]
            txt = str(r.get("generated_text") or r.get("prediction") or "").strip()
            if vol and txt:
                out[vol if vol.endswith(".nii.gz") else vol + ".nii.gz"] = txt
        return out
    df = pd.read_csv(path)
    vc = next(c for c in df.columns if c.lower() in {"volumename", "accnum", "volume", "image", "id"})
    pc = next(c for c in df.columns if c.lower() in {"prediction", "pred_combined_report", "generated_report", "generated_text", "pred"})
    s = df.set_index(vc)[pc].fillna("").astype(str).str.strip()
    if s.str.contains(r"The region \d+ is", regex=True).mean() > 0.5:
        s = s.map(flatten_reg2rg)
    return {(v if str(v).endswith(".nii.gz") else f"{v}.nii.gz"): t for v, t in s.items() if t}


def rated_case_ids(data_dir: Path) -> set[str]:
    ids = set()
    for f in (data_dir / "annotations").glob("*_annotations.csv"):
        try:
            ids |= set(pd.read_csv(f, dtype=str)["case_id"].dropna())
        except Exception:
            pass
    return ids


def study_groups(studies: list[str], data_dir: Path) -> list[tuple[str, list[str]]]:
    from config import NON_RATER_EMAILS  # noqa: E402
    import auth  # noqa: E402

    skip = {auth.user_slug(e) for e in NON_RATER_EMAILS} | {auth.user_slug(auth.DEFAULT_ADMIN_USER)}
    assign_path = data_dir / "assignments.json"
    assigned = json.loads(assign_path.read_text()) if assign_path.exists() else {}
    groups: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for slug, ids in assigned.items():
        if slug in skip or slug.startswith("_"):
            continue
        ids = [s for s in ids if s in studies and s not in seen]
        if ids:
            groups.append((f"assigned to {slug}", ids))
            seen |= set(ids)
    rest = [s for s in studies if s not in seen]
    if rest:
        groups.append(("not yet assigned", rest))
    return groups


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate", metavar="NAME=PATH")
    ap.add_argument("--cases", type=Path, default=ROOT / "data" / "cases.json")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    name, _, path = args.candidate.partition("=")
    preds = load_predictions(Path(path))
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = data["cases"]
    by_study: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_study[c["study_id"]].append(c)
    if any(len(v) != 1 for v in by_study.values()):
        sys.exit("this script expects exactly one candidate per study")
    studies = sorted(by_study)
    models = sorted({c["candidate_id"] for c in cases} | {name})
    rated = rated_case_ids(args.data_dir)
    rng = random.Random(args.seed)
    print(f"{len(preds)} predictions for {name}; pool: {len(studies)} studies, models {models}; {len(rated)} rated cases protected")

    switched: list[dict] = []
    for label, group in study_groups(studies, args.data_dir):
        counts = Counter(by_study[s][0]["candidate_id"] for s in group)
        base, extra = divmod(len(group), len(models))
        target = {m: base + (1 if i < extra else 0) for i, m in enumerate(models)}
        need = target[name] - counts.get(name, 0)
        donors = {m: counts.get(m, 0) - target[m] for m in models if m != name}
        print(f"\n[{label}] {len(group)} studies · current {dict(counts)} · target {target}")
        for m, k in sorted(donors.items(), key=lambda x: -x[1]):
            if k <= 0 or need <= 0:
                continue
            pool = [s for s in group if by_study[s][0]["candidate_id"] == m
                    and by_study[s][0]["id"] not in rated and by_study[s][0]["meta"].get("volume") in preds]
            rng.shuffle(pool)
            take = pool[: min(k, need)]
            if len(take) < min(k, need):
                print(f"   warning: only {len(take)} switchable {m} studies (wanted {min(k, need)})")
            for s in take:
                old = by_study[s][0]
                new = {**old, "id": f"{s}__{name}", "candidate_id": name, "candidate": {"text": preds[old["meta"]["volume"]]}}
                new.pop("links", None)
                by_study[s] = [new]
                switched.append(new)
            need -= len(take)
            print(f"   {m} -> {name}: {len(take)}")
        print("   result:", dict(Counter(by_study[s][0]["candidate_id"] for s in group)))

    new_cases = [by_study[s][0] for s in studies]
    print(f"\nswitched {len(switched)} studies; overall: {dict(Counter(c['candidate_id'] for c in new_cases))}")
    if args.dry_run:
        return 0
    data["cases"] = new_cases
    meta = data.setdefault("meta", {})
    meta.setdefault("candidates", {})
    if isinstance(meta["candidates"], dict):
        meta["candidates"][name] = str(path)
    meta.setdefault("rebalanced", []).append({"at": datetime.now(timezone.utc).isoformat(), "added": name,
                                              "switched_studies": [c["study_id"] for c in switched], "seed": args.seed})
    args.cases.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.cases} — now run scripts/precompute_links.py for the {len(switched)} new cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
