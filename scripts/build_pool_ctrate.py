#!/usr/bin/env python3
"""Build data/cases.json for the CT-RATE / RadGenome-ChestCT reader study.

Pairs the original CT-RATE validation reports (reference) with the generated
reports of several models (candidates) on the shared RadGenome-ChestCT test
split, and picks a stratified subset of studies for annotation.

Inputs (all CSV):
  --reports      CT-RATE  validation_reports.csv
                 (VolumeName, ClinicalInformation_EN, Technique_EN, Findings_EN, Impressions_EN)
  --labels       CT-RATE  valid_predicted_labels.csv (VolumeName + 18 binary abnormality columns);
                 optional, only used for stratification
  --candidate    NAME=PATH, repeatable. PATH is a CSV with a volume column and a prediction column
                 (auto-detected: volumename/AccNum, prediction/Pred_combined_report). Reg2RG's
                 per-region "The region N is X: ..." format is flattened automatically.

Selection:
  * only volumes for which EVERY candidate set has a non-empty prediction
  * one volume per patient (CT-RATE volume ids are <split>_<patient>_<study>_<recon>; different
    reconstructions of the same study carry the same report)
  * stratified by number of positive abnormality labels into bins 0 / 1-2 / 3-4 / 5+, with the
    quotas in --quotas (fractions of --n-studies), seeded

Inputs default to data/raw/ (see DEFAULT_* below for the expected layout):
  python scripts/build_pool_ctrate.py --n-studies 100 --seed 17
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"  # default location for the input CSVs (git-ignored)
DEFAULT_REPORTS = RAW / "ct-rate/dataset/radiology_text_reports/validation_reports.csv"
DEFAULT_LABELS = RAW / "ct-rate/dataset/multi_abnormality_labels/valid_predicted_labels.csv"
GEN = RAW / "cngvng"
DEFAULT_CANDIDATES = [
    f"dia_llama={GEN / 'dia_llama_finetuned/test_predictions.csv'}",
    f"reg2rg={GEN / 'reg2rg_finetuned/radgenome_combined_reports.csv'}",
    f"m3d={GEN / 'm3d_finetuned/test_predictions.csv'}",
    f"radfm={GEN / 'radfm_finetuned/test_predictions.csv'}",
]

_REGION_RE = re.compile(r"\s*The region \d+ is [^:]+:\s*", flags=re.IGNORECASE)


def flatten_reg2rg(text: str, dedupe: bool = True) -> str:
    """Strip the 'The region N is <anatomy>:' scaffolding of Reg2RG's combined report (it is part of
    the prompt template, not of the report) and optionally drop verbatim repeated sentences, which
    appear when the model loops past the ten real regions."""
    body = _REGION_RE.sub(" ", str(text)).strip()
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    if dedupe:
        seen, kept = set(), []
        for s in sents:
            k = s.lower()
            if k not in seen:
                seen.add(k)
                kept.append(s)
        sents = kept
    return " ".join(sents)


def load_candidate(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    vol_col = next(c for c in df.columns if c.lower() in {"volumename", "accnum", "volume", "id"})
    pred_col = next(c for c in df.columns if c.lower() in {"prediction", "pred_combined_report", "pred", "generated_text"})
    s = df.set_index(vol_col)[pred_col].fillna("").astype(str).str.strip()
    if s.str.contains(r"The region \d+ is", regex=True).mean() > 0.5:
        s = s.map(flatten_reg2rg)
    return s


def patient_of(volume: str) -> str:
    # valid_123_a_1.nii.gz -> valid_123
    return "_".join(volume.split("_")[:2])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--candidate", action="append", metavar="NAME=PATH", help="repeatable; defaults to the four project sets")
    ap.add_argument("--n-studies", type=int, default=100)
    ap.add_argument("--quotas", default="0.15,0.30,0.30,0.25", help="fractions for label-count bins 0 / 1-2 / 3-4 / 5+")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "cases.json")
    args = ap.parse_args(argv)

    cand_specs = args.candidate or DEFAULT_CANDIDATES
    candidates: dict[str, pd.Series] = {}
    for spec in cand_specs:
        name, _, path = spec.partition("=")
        candidates[name] = load_candidate(Path(path))
        print(f"{name:10s} {len(candidates[name]):5d} rows  {candidates[name].nunique():5d} unique predictions  <- {path}")

    reports = pd.read_csv(args.reports).set_index("VolumeName")
    common = set(reports.index)
    for s in candidates.values():
        common &= set(s[s.str.len() > 0].index)
    print(f"{len(common)} volumes with a reference and a non-empty prediction from every candidate set")

    # one volume per patient, deterministic
    by_patient: dict[str, str] = {}
    for v in sorted(common):
        by_patient.setdefault(patient_of(v), v)
    volumes = sorted(by_patient.values())
    print(f"{len(volumes)} after keeping one volume per patient")

    # stratify by abnormality-label count
    if args.labels and Path(args.labels).exists():
        lab = pd.read_csv(args.labels).set_index("VolumeName")
        n_pos = lab.drop(columns=[c for c in lab.columns if lab[c].dtype == object]).sum(axis=1)
        label_names = list(lab.columns)
    else:
        n_pos, label_names = pd.Series(dtype=int), []
        print("no labels file — sampling uniformly")

    def bin_of(v):
        n = int(n_pos.get(v, 0))
        return 0 if n == 0 else 1 if n <= 2 else 2 if n <= 4 else 3

    rng = random.Random(args.seed)
    bins: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
    for v in volumes:
        bins[bin_of(v)].append(v)
    for b in bins.values():
        rng.shuffle(b)
    quotas = [float(x) for x in args.quotas.split(",")]
    want = [round(q * args.n_studies) for q in quotas]
    picked: list[str] = []
    for b, n in enumerate(want):
        picked += bins[b][:n]
        bins[b] = bins[b][n:]
    leftovers = [v for b in bins.values() for v in b]
    rng.shuffle(leftovers)
    picked += leftovers[: max(0, args.n_studies - len(picked))]
    picked = sorted(picked[: args.n_studies])
    print(f"picked {len(picked)} studies; label-count bins: "
          + ", ".join(f"{k}:{sum(1 for v in picked if bin_of(v) == k)}" for k in range(4)))

    cases = []
    for v in picked:
        r = reports.loc[v]
        ref = {
            "clinical_information": str(r.get("ClinicalInformation_EN", "") or "").strip(),
            "technique": str(r.get("Technique_EN", "") or "").strip(),
            "findings": str(r.get("Findings_EN", "") or "").strip(),
            "impression": str(r.get("Impressions_EN", "") or "").strip(),
        }
        study_id = v.replace(".nii.gz", "")
        pos_labels = [c for c in label_names if v in n_pos.index and int(lab.loc[v, c]) == 1] if label_names else []
        for name, s in candidates.items():
            cases.append({
                "id": f"{study_id}__{name}",
                "study_id": study_id,
                "candidate_id": name,
                "reference": ref,
                "candidate": {"text": s[v]},
                "meta": {"volume": v, "abnormality_labels": pos_labels},
            })

    out = {
        "meta": {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "dataset": "CT-RATE validation split (RadGenome-ChestCT test split)",
            "reference": str(args.reports),
            "candidates": {name: spec.partition("=")[2] for name, spec in zip(candidates, cand_specs)},
            "n_studies": len(picked),
            "n_cases": len(cases),
            "seed": args.seed,
            "quotas": quotas,
            "reg2rg_flattened": True,
        },
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(cases)} cases ({len(picked)} studies x {len(candidates)} candidates) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
