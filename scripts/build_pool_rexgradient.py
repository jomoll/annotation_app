#!/usr/bin/env python3
"""Build data/cases.json for the chest X-ray arm from ReXGradient public-test studies.

References: Findings + Impression (plus Indication, Comparison and the study description as context)
from the ReXGradient test metadata. Candidates: CXRMate-2's released outputs (findings + impression) and
the findings generated locally by scripts/generate_cxr_candidates.py (CheXagent-2, MAIRA-2, LLaVA-Rad).
Studies come from scripts/select_rexgradient_studies.py. Same design as the CT arm: with
--candidates-per-study 1 every study gets one candidate and the models are spread equally at random.

    python scripts/build_pool_rexgradient.py --candidates-per-study 1 --seed 17 --out data/cases_cxr.json

Report text is gated (ReXGradient non-commercial agreement): the output is git-ignored, never commit it.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "rexgradient"
META = Path("/data2/moll/datasets/rexgradient/metadata/test_metadata.csv")
CXRMATE2 = Path("/data2/moll/models/cxrmate-2/cxrmate2_generated_reports_rexgradient_test.csv")


def load_generated(name: str) -> dict[str, dict]:
    p = RAW / "candidates" / f"{name}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("text") and not r.get("error"):
                out[r["id"]] = {"text": r["text"].strip()}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meta", type=Path, default=META)
    ap.add_argument("--cxrmate2", type=Path, default=CXRMATE2)
    ap.add_argument("--generated", nargs="*", default=["chexagent", "maira2", "llavarad"],
                    help="names of jsonl files in data/raw/rexgradient/candidates/")
    ap.add_argument("--candidates-per-study", type=int, default=1)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "cases_cxr.json")
    args = ap.parse_args(argv)

    sel = pd.read_csv(RAW / "selected_studies.csv", dtype=str).fillna("")
    meta = pd.read_csv(args.meta, dtype=str).fillna("").set_index("id")

    candidates: dict[str, dict[str, dict]] = {}
    if args.cxrmate2.exists():
        c2 = pd.read_csv(args.cxrmate2, dtype=str).fillna("")
        by_uid = {r.study_id: r for r in c2.itertuples()}
        candidates["cxrmate2"] = {}
        for sid in sel.id:
            r = by_uid.get(meta.loc[sid, "StudyInstanceUid"])
            if r is not None and (r.findings.strip() or r.impression.strip()):
                candidates["cxrmate2"][sid] = {"findings": r.findings.strip(), "impression": r.impression.strip(),
                                               "text": (r.findings.strip() + "\n\n" + r.impression.strip()).strip()}
    for name in args.generated:
        g = load_generated(name)
        if g:
            candidates[name] = g
    for name, d in candidates.items():
        print(f"{name:10s} outputs for {len(d):3d} / {len(sel)} selected studies")
    models = sorted(candidates)
    if not models:
        sys.exit("no candidate outputs found")

    rng = random.Random(args.seed)
    k = min(args.candidates_per_study or len(models), len(models))
    allocation: dict[str, list[str]] = {}
    if k == len(models):
        allocation = {sid: [m for m in models if sid in candidates[m]] for sid in sel.id}
    else:
        # Spread the models equally WITHIN each stratum (normal vs abnormal impression x 1 vs 2 views), so
        # no model gets an easier or harder share of studies by chance; random within a stratum.
        for _, grp in sel.groupby(["normal", "n_views"]):
            order = grp.id.tolist()
            rng.shuffle(order)
            slots: list[str] = []
            while len(slots) < len(order) * k:
                block = list(models)
                rng.shuffle(block)
                slots += block
            for i, sid in enumerate(order):
                chosen = [m for m in slots[i * k:(i + 1) * k] if sid in candidates[m]]
                if len(chosen) < k:  # fall back to any model that has an output for this study
                    chosen += [m for m in models if m not in chosen and sid in candidates[m]][: k - len(chosen)]
                allocation[sid] = chosen
    print("candidates per model:", dict(sorted(Counter(m for v in allocation.values() for m in v).items())))

    cases = []
    for sid in sel.id:
        r = meta.loc[sid]
        ref = {
            "clinical_information": r.Indication.strip(),
            "technique": f"{r.StudyDescription.strip()}" + (f" · Comparison: {r.Comparison.strip()}" if r.Comparison.strip() else ""),
            "findings": r.Findings.strip(),
            "impression": r.Impression.strip(),
        }
        row = sel[sel.id == sid].iloc[0]
        for name in allocation.get(sid, []):
            c = candidates[name][sid]
            cases.append({
                "id": f"{sid}__{name}", "study_id": sid, "candidate_id": name, "reference": ref,
                "modality": "cxr",
                "candidate": {"text": c["text"], **({"findings": c["findings"], "impression": c["impression"]} if "findings" in c else {})},
                "meta": {"accession": r.AccessionNumber, "n_views": int(row.n_views), "normal_impression": row.normal == "True",
                         "age": row.age_y, "sex": r.PatientSex},
            })
    out = {"meta": {"built_at": datetime.now(timezone.utc).isoformat(), "dataset": "ReXGradient-160K public test split",
                    "modality": "Chest X-ray", "candidates": models, "n_studies": len(sel), "n_cases": len(cases),
                    "candidates_per_study": k, "seed": args.seed}, "cases": cases}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(cases)} cases ({len(sel)} studies) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
