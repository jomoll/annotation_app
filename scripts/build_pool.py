#!/usr/bin/env python3
"""Build one modality's case pool from reference reports and candidate files -- any dataset.

    python scripts/build_pool.py --modality us --references refs.csv --studies eval_ids.txt \\
        --candidates modelA=out/modelA.jsonl modelB=out/modelB.jsonl \\
                     injected=data/raw/us/candidates/injected.jsonl \\
                     retrieved=data/raw/us/candidates/retrieved.jsonl \\
        --assignment data/raw/us/assignment.csv --out data/cases_us.json

then  scripts/merge_pools.py us=data/cases_us.json ... --out data/cases.json
and   add the key to MODALITIES in app/config.py (e.g. "us": "Abdominal ultrasound").

References: CSV/JSONL read by reporttools/reports.py -- a study id, the report as `findings`
(+ `impression`) or as one `report`/`text` column, optionally `indication`/`clinical_information`
(shown to raters as clinical information), `technique`/`specimen` (shown as technique) and `normal`
(0/1, used to balance assignment and reported in the summary).

Candidates: NAME=PATH, one JSON line (or CSV row) per study with `study_id` and `text` and/or
`findings` + `impression` -- the format scripts/generate_fallbacks.py writes; write your models'
outputs the same way. Rows with a non-empty `error` or no text are skipped.

Which candidate each study gets:
  --assignment CSV   study_id,system -- one row per (study, candidate) to include. Write it with
                     scripts/assign_systems.py BEFORE generating, so each model only runs on its
                     own studies.
  (neither)          every candidate that exists for a study is included; raters then see several
                     candidates per study back to back, in a rater-specific shuffled order.

Only report text reaches the output. Everything else in a candidate file -- above all
`injected_errors`, the injection ground truth, and retrieval's `source_study_id` -- stays out: the
pool is what raters' browsers receive, and a rater reading it would be reading the answer key.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from reporttools.reports import ID_COLUMNS, TEXT_COLUMNS, load_reports  # noqa: E402

CANDIDATE_FIELDS = ("text", "findings", "impression")
INFO_COLUMNS = ("clinical_information", "indication", "clinical_indication", "clinical_history",
                "history", "reason_for_exam")
TECHNIQUE_COLUMNS = ("technique", "examination", "exam", "procedure", "specimen", "specimen_type")


def first(row: dict, cols: tuple[str, ...]) -> str:
    return next(((row.get(c) or "").strip() for c in cols if (row.get(c) or "").strip()), "")


def is_normal(row: dict) -> bool | None:
    v = (row.get("normal") or "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return None


def reference_block(row: dict) -> dict:
    ref = {"clinical_information": first(row, INFO_COLUMNS), "technique": first(row, TECHNIQUE_COLUMNS)}
    if (row.get("findings") or "").strip() or (row.get("impression") or "").strip():
        ref["findings"] = (row.get("findings") or "").strip()
        ref["impression"] = (row.get("impression") or "").strip()
    else:
        ref["text"] = first(row, TEXT_COLUMNS)          # unsectioned (e.g. pathology)
    return ref


def load_candidates(path: Path) -> dict[str, dict]:
    """study_id -> {text, findings, impression}, report text only."""
    rows = load_reports([path]) if path.suffix.lower() not in (".jsonl", ".ndjson") else [
        json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = {}
    for r in rows:
        sid = str(next((r[c] for c in ID_COLUMNS if r.get(c)), "")).strip()
        if not sid or str(r.get("error") or "").strip() not in ("", "None", "nan"):
            continue
        c = {k: str(r.get(k) or "").strip() for k in CANDIDATE_FIELDS}
        if not c["text"] and (c["findings"] or c["impression"]):
            c["text"] = (f"FINDINGS: {c['findings']}\nIMPRESSION: {c['impression']}"
                         if c["impression"] else c["findings"])
        if not c["text"]:
            continue
        # a structured candidate is shown by section; an unstructured one as one block
        out[sid] = {k: v for k, v in c.items() if v} if (c["findings"] or c["impression"]) else {"text": c["text"]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modality", required=True, help="key you will add to MODALITIES in app/config.py")
    ap.add_argument("--references", nargs="+", type=Path, required=True)
    ap.add_argument("--studies", type=Path, help="evaluation study ids, one per line (default: every "
                                                 "study in --assignment, else every reference)")
    ap.add_argument("--id-col")
    ap.add_argument("--candidates", nargs="+", required=True, metavar="NAME=PATH")
    ap.add_argument("--assignment", type=Path, help="CSV study_id,system (see scripts/assign_systems.py)")
    ap.add_argument("--dataset", default="", help="free-text dataset description for the pool metadata")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--strict", action="store_true", help="fail if any planned case cannot be built")
    args = ap.parse_args(argv)

    refs = {r["study_id"]: r for r in load_reports(args.references, args.id_col)}
    cands: dict[str, dict[str, dict]] = {}
    for spec in args.candidates:
        name, _, path = spec.partition("=")
        if not name or not path:
            sys.exit(f"--candidates expects NAME=PATH, got {spec!r}")
        cands[name] = load_candidates(Path(path))

    if args.assignment:
        import pandas as pd
        a = pd.read_csv(args.assignment, dtype=str).fillna("")
        a.columns = [c.lower() for c in a.columns]
        idc = next((c for c in ID_COLUMNS if c in a.columns), None)
        if not idc or "system" not in a.columns:
            sys.exit("--assignment needs a study id column and a 'system' column")
        plan = [(r[idc].strip(), r["system"].strip()) for r in a.to_dict("records")]
        unknown = sorted({s for _, s in plan} - set(cands))
        if unknown:
            sys.exit(f"assignment names systems with no --candidates file: {unknown}")
    else:
        plan = None

    if args.studies:
        study_ids = [l.strip() for l in args.studies.read_text().splitlines() if l.strip() and not l.startswith("#")]
    elif plan is not None:
        study_ids = list(dict.fromkeys(s for s, _ in plan))
    else:
        study_ids = list(refs)
    keep = set(study_ids)
    if plan is None:
        plan = [(sid, name) for sid in study_ids for name in cands if sid in cands[name]]
    else:
        plan = [(sid, name) for sid, name in plan if sid in keep]

    cases, missing = [], []
    for sid, name in plan:
        ref, cand = refs.get(sid), cands[name].get(sid)
        if ref is None or cand is None:
            missing.append((sid, name, "no reference" if ref is None else "no usable candidate"))
            continue
        normal = is_normal(ref)
        cases.append({"id": f"{sid}__{name}", "study_id": sid, "candidate_id": name,
                      "modality": args.modality, "reference": reference_block(ref), "candidate": cand,
                      "meta": ({"normal_reference": normal} if normal is not None else {})})

    for sid, name, why in missing[:20]:
        print(f"  MISSING {sid} ({name}): {why}", file=sys.stderr)
    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more", file=sys.stderr)
    if missing and args.strict:
        sys.exit(f"{len(missing)} planned cases could not be built")

    per = Counter(c["candidate_id"] for c in cases)
    n_studies = len({c["study_id"] for c in cases})
    print(f"{len(cases)} cases over {n_studies} studies · per system: {dict(sorted(per.items()))}")
    flags = [c["meta"]["normal_reference"] for c in cases if "normal_reference" in c["meta"]]
    if flags:
        print(f"normal references: {sum(flags)}/{len(flags)} ({sum(flags) / len(flags):.0%})")
    derived = {"injected", "retrieved"} & set(per)
    if derived:
        print(f"text-derived strata present: {sorted(derived)} -- analyse them as their own stratum")

    out = {"meta": {"built_at": datetime.now(timezone.utc).isoformat(), "dataset": args.dataset,
                    "modality": args.modality, "candidates": sorted(per), "n_studies": n_studies,
                    "n_cases": len(cases),
                    "candidates_per_study": round(len(cases) / max(n_studies, 1), 2)},
           "cases": cases}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
