#!/usr/bin/env python3
"""Generate text-derived fallback candidates for a new modality: error injection and retrieval.

Use these only to fill a part of the quality range your models leave empty (see step 4 of
docs/new_modality_guide.pdf), and always keep them as their own stratum: they come from the
reference TEXT, not from images, and every record says so (`strategy`, `derived_from_reference`).

    # which error categories can fire for your modality, before spending anything
    python scripts/generate_fallbacks.py coverage --modality us --acquisition abdomen_us \\
        --lexicon reporttools/lexicons/abdomen_us.draft.json

    # error injection: the reference with 1-3 labelled errors (the TOP of the range)
    python scripts/generate_fallbacks.py inject --reports refs.csv --studies eval_ids.txt \\
        --modality us --acquisition abdomen_us --lexicon my_lexicon.json \\
        --out data/raw/us/candidates/injected.jsonl

    # similar-case retrieval: another patient's real report (near-miss or floor)
    python scripts/generate_fallbacks.py retrieve --reports refs.csv --studies eval_ids.txt \\
        --donor-reports train_refs.csv [--labels labels.csv --label-mode similar] \\
        --out data/raw/us/candidates/retrieved.jsonl

Input: CSV or JSONL with a study id column (`study_id`, `study_uid` or `id`; or --id-col), the
report as `findings` (+ `impression`) or as one `report`/`text` column, and optionally context
columns such as `indication`, `clinical_information`, `technique` or `specimen` (used by retrieval).
See fallbacks/reports.py.

Injection uses an OpenAI-compatible LLM endpoint (ANNOTATION_LLM_URL / ANNOTATION_LLM_MODEL /
ANNOTATION_LLM_KEY) only to extract claims, which the omission, negation and location categories
need. --no-llm skips it and runs the remaining categories offline. Report text goes to that endpoint:
use one you are permitted to send your reports to.

Built-in lexicons: modalities ct, cxr, mr, pathology; acquisitions chest_ct, abdomen_ct, chest_xray,
brain_mr, spine_mr, surgical_pathology. Anything else needs --lexicon for the appended-sentence
categories (the substitution categories need none); see reporttools/lexicons/README.md.

Output is one JSON line per study, in the candidate schema scripts/build_pool.py reads. Re-running
appends only studies not yet in --out (use --force to regenerate).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reporttools import injection as inj_mod  # noqa: E402
from reporttools.inject import CATEGORY_WEIGHTS, inject_reports, plan_categories  # noqa: E402
from reporttools.reports import (CONTEXT_COLUMNS, load_labels, load_reports,  # noqa: E402
                               present_context_columns, reference_text)
from reporttools.retrieval import retrieve  # noqa: E402

# Caveats for built-in lexicons, printed when they apply.
CAVEATS = {
    ("surgical_pathology", "out_of_field"):
        "out_of_field on pathology inserts a statement about tissue that was not submitted. Whether "
        "a tissue is out of scope depends on each report's specimens, so check a sample by hand or "
        "pass --exclude-categories out_of_field.",
}


def _ids(path: Path | None) -> list[str] | None:
    if not path:
        return None
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


def _register(lexicons: list[Path]) -> None:
    for p in lexicons or []:
        added = inj_mod.register_lexicon(p)
        print(f"lexicon {p}: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in added.items()))


def cmd_coverage(args) -> int:
    _register(args.lexicon)
    usable, off = plan_categories(args.modality, args.acquisition, use_llm=not args.no_llm,
                                  only=args.categories, exclude=args.exclude_categories)
    print(f"modality={args.modality!r} acquisition={args.acquisition!r}")
    for cat in CATEGORY_WEIGHTS:
        state = "ON " if cat in usable else "off"
        print(f"  [{state}] {cat:24s} {off.get(cat, '')}")
    print(f"built-in modalities:   {', '.join(sorted(inj_mod.known_modalities()))}")
    print(f"built-in acquisitions: {', '.join(sorted(inj_mod.known_acquisitions()))}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--reports", nargs="+", type=Path, required=True,
                       help="reference reports (CSV/JSONL) containing the evaluation studies")
        p.add_argument("--studies", type=Path, help="evaluation study ids, one per line (default: all)")
        p.add_argument("--id-col", help="study id column, if not study_id/study_uid/id")
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--force", action="store_true", help="regenerate studies already in --out")

    def inj_opts(p):
        p.add_argument("--modality", required=True,
                       help="modality key for physically impossible claims (ct, mr, cxr, pathology, or your lexicon's)")
        p.add_argument("--acquisition", required=True,
                       help="acquisition key for field-of-view, comparison and location lexicons")
        p.add_argument("--lexicon", nargs="*", type=Path, default=[], help="extra lexicon JSON file(s)")
        p.add_argument("--no-llm", action="store_true",
                       help="skip claim extraction; omission, negation and location are then disabled")
        p.add_argument("--categories", nargs="+", help=f"restrict to these ({', '.join(CATEGORY_WEIGHTS)})")
        p.add_argument("--exclude-categories", nargs="+", default=[])

    p = sub.add_parser("coverage", help="show which error categories can fire")
    inj_opts(p)

    p = sub.add_parser("inject", help="reference + 1-3 labelled errors")
    common(p)
    inj_opts(p)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--significant-rate", type=float, default=0.5,
                   help="share of errors drawn at the clinically significant magnitude")

    p = sub.add_parser("retrieve", help="another patient's report, same context, different findings")
    common(p)
    p.add_argument("--donor-reports", nargs="+", type=Path,
                   help="reports donors may come from -- ideally the TRAIN split, filtered like the "
                        "evaluation studies. Default: --reports minus the evaluation studies.")
    p.add_argument("--context-cols", nargs="+",
                   help=f"columns describing the clinical context (default: any of {', '.join(CONTEXT_COLUMNS)})")
    p.add_argument("--labels", type=Path, help="CSV: study id + 0/1 label columns, to choose donors by diagnosis")
    p.add_argument("--label-mode", choices=["similar", "divergent"], default="similar",
                   help="similar: donor shares the labels (near-miss). divergent: shares none (floor).")
    p.add_argument("--match-col",
                   help="column the donor must share exactly, e.g. examination type or specimen organ")
    p.add_argument("--max-findings-similarity", type=float, default=0.55,
                   help="reject donors whose findings are closer than this TF-IDF cosine (a copy, not a candidate)")

    args = ap.parse_args(argv)
    if args.cmd == "coverage":
        return cmd_coverage(args)

    rows = load_reports(args.reports, args.id_col)
    want = _ids(args.studies)
    if want is not None:
        have = {r["study_id"] for r in rows}
        missing = [s for s in want if s not in have]
        if missing:
            print(f"WARNING: {len(missing)} requested studies not in --reports, e.g. {missing[:3]}")
        keep = set(want)
        rows = [r for r in rows if r["study_id"] in keep]
    rows = [r for r in rows if reference_text(r)]
    eval_ids = {r["study_id"] for r in rows}

    done = set()
    if args.out.exists() and not args.force:
        done = {json.loads(l)["study_id"] for l in args.out.read_text().splitlines() if l.strip()}
    todo = [r for r in rows if r["study_id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{args.cmd}: {len(todo)} to generate ({len(done)} already in {args.out})")
    if not todo:
        return 0

    if args.cmd == "inject":
        _register(args.lexicon)
        cats, off = plan_categories(args.modality, args.acquisition, use_llm=not args.no_llm,
                                    only=args.categories, exclude=args.exclude_categories)
        print(f"categories ON:  {', '.join(cats) or 'NONE'}")
        for c, why in off.items():
            print(f"categories off: {c} -- {why}")
        for (acq, cat), note in CAVEATS.items():
            if acq == args.acquisition and cat in cats:
                print(f"NOTE: {note}")
        if not cats:
            print("ERROR: no category can fire")
            return 1
        recs = inject_reports(todo, modality=args.modality, acquisition=args.acquisition,
                              categories=cats, use_llm=not args.no_llm, seed=args.seed,
                              significant_rate=args.significant_rate)
    else:
        donors = load_reports(args.donor_reports, args.id_col) if args.donor_reports else load_reports(args.reports, args.id_col)
        if not args.donor_reports:
            print("WARNING: no --donor-reports; drawing donors from --reports minus the evaluation "
                  "studies. Prefer the training split.")
        before = len(donors)
        # A donor must never be an evaluation case: that would put one case's reference into
        # another case's candidate.
        donors = [d for d in donors if d["study_id"] not in eval_ids and reference_text(d)]
        print(f"donor pool: {len(donors)} reports ({before - len(donors)} removed as evaluation cases or empty)")
        if not donors:
            print("ERROR: donor pool is empty")
            return 1
        ctx = tuple(c.lower() for c in args.context_cols) if args.context_cols else present_context_columns(todo + donors)
        print(f"context columns: {', '.join(ctx) or 'none'}")
        labels = load_labels(args.labels, args.id_col)
        if labels:
            print(f"labels: {len(labels)} studies")
        recs = retrieve(todo, donors, context_cols=ctx, labels=labels, label_mode=args.label_mode,
                        max_findings_similarity=args.max_findings_similarity,
                        match_col=args.match_col.lower() if args.match_col else None)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w" if args.force else "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = sum(1 for r in recs if not r["error"])
    print(f"wrote {ok}/{len(recs)} usable candidates to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
