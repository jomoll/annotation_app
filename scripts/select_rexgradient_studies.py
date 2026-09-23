#!/usr/bin/env python3
"""Pick the ReXGradient public-test studies for the chest X-ray arm.

Inputs: the test split metadata (metadata/test_metadata.csv + .json from the HF dataset).
Filters: adults (>= 18 y), plain chest studies (no abdomen / neonate / rib / other body parts),
one or two views, at most one study per patient. Stratified by whether the reference impression
reads as a normal study (regex over stock "no acute cardiopulmonary ..." phrasings) and by number
of views, so the pool mirrors the split's natural mix but with a fixed normal share.

    python scripts/select_rexgradient_studies.py --n-studies 200 --normal-share 0.3 --seed 17
writes data/raw/rexgradient/selected_studies.csv (study id, image paths, views, strata) — no report text.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_META = Path("/data2/moll/datasets/rexgradient/metadata")

NORMAL = re.compile(
    r"^(no (acute|active) (cardiopulmonary|intrathoracic|pulmonary|thoracic)[^.]*\.?"
    r"|normal (chest|study|exam|examination|radiograph)s?[^.]*\.?|unremarkable[^.]*\.?"
    r"|no acute (abnormalit|findings?|disease|process)[^.]*\.?|(the )?lungs are clear\.?|clear lungs\.?"
    r"|no (radiographic )?evidence of (acute )?(pneumonia|disease|abnormality|cardiopulmonary)[^.]*\.?"
    r"|no significant (abnormalit|change)[^.]*\.?)\s*$", re.I)
EXCLUDE_DESC = re.compile(r"abd|neonat|infant|rib|shoulder|clavic|stern|spine|babygram|decub|oblique", re.I)


def age_years(a: str):
    m = re.match(r"(\d+)([DMY])", a or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return n if u == "Y" else n / 12 if u == "M" else n / 365


def impression_is_normal(imp: str) -> bool:
    s = re.sub(r"^\d+\.\s*", "", (imp or "").strip())
    return bool(NORMAL.match(s))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--n-studies", type=int, default=200)
    ap.add_argument("--normal-share", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "raw" / "rexgradient" / "selected_studies.csv")
    args = ap.parse_args(argv)

    m = pd.read_csv(args.meta / "test_metadata.csv", dtype=str).fillna("")
    js = json.loads((args.meta / "test_metadata.json").read_text())
    m["patient"] = m.id.str.split("_").str[0].str.lstrip("p")
    m["age_y"] = m.PatientAge.map(age_years)
    m["n_views"] = m.id.map(lambda i: len(js[i]["ImagePath"]) if i in js else 0)
    m["normal"] = m.Impression.map(impression_is_normal)
    n0 = len(m)
    m = m[(m.age_y >= 18) & ~m.StudyDescription.str.contains(EXCLUDE_DESC) & m.n_views.between(1, 2)
          & (m.Findings.str.len() > 40) & (m.Impression.str.len() > 5)]
    print(f"{n0} test studies -> {len(m)} adult plain chest studies with 1-2 images")
    rng = random.Random(args.seed)
    m = m.sample(frac=1, random_state=args.seed).drop_duplicates("patient")
    print(f"{len(m)} after one study per patient; normal impressions: {m.normal.mean():.0%}; views: {m.n_views.value_counts().to_dict()}")

    n_norm = round(args.n_studies * args.normal_share)
    picked = []
    for is_norm, n in ((True, n_norm), (False, args.n_studies - n_norm)):
        pool = m[m.normal == is_norm]
        # keep the split's own 1-view / 2-view ratio within each stratum
        share2 = (pool.n_views == 2).mean()
        n2 = round(n * share2)
        picked += pool[pool.n_views == 2].head(n2).id.tolist()
        picked += pool[pool.n_views == 1].head(n - n2).id.tolist()
    sel = m[m.id.isin(picked)].copy()
    sel["image_paths"] = sel.id.map(lambda i: "|".join(p.replace("../", "", 1) for p in js[i]["ImagePath"]))
    sel["views"] = sel.id.map(lambda i: "|".join(v or "?" for v in js[i]["ImageViewPosition"]))
    out = sel[["id", "AccessionNumber", "StudyInstanceUid", "patient", "PatientSex", "age_y", "StudyDescription",
               "n_views", "views", "normal", "image_paths"]].sort_values("id")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"picked {len(out)} studies -> {args.out}")
    print("  normal:", int(out.normal.sum()), "| views:", out.n_views.value_counts().to_dict(),
          "| sex:", out.PatientSex.value_counts().to_dict(), "| median age:", int(out.age_y.median()))
    print("  view labels:", Counter(v for vs in out.views for v in vs.split("|")).most_common(6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
