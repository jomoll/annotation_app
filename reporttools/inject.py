"""Controlled error injection over a set of reference reports.

Each report gets 1-3 labelled errors. The count is what spreads this stratum; the category mix is
set by CATEGORY_WEIGHTS below. Every applied error is recorded with category, span and before/after
value in `injected_errors`. That record is the ground truth -- keep it out of anything readers see
(scripts/build_pool.py strips it).
"""
from __future__ import annotations

import random
import time

from . import injection as inj_mod
from .layout import restore_checked
from .reports import body_text, is_sectioned

# Omission picks the sentence to drop from an extracted claim, so it cannot fire without the LLM
# parser. Negation uses claims only to INSERT a "no" (removing an existing "no"/"without" needs
# none), and location never reads them -- both run with --no-llm, just with less variety.
NEEDS_CLAIMS = {"omission_of_finding"}
USES_CLAIMS = {"omission_of_finding", "negation_error"}

# Weights, set by how well each category ISOLATES the clinical error.
#
# The value of an injected candidate is that it differs from the reference by a known error and
# nothing else: a surface metric scores it ~0.98 because it cannot see the error, a clinical metric
# should drop sharply. A substitution preserves that exactly. An APPENDED sentence (out_of_field,
# comparison_error, modality_unobservable) also adds text, so BLEU/ROUGE fall for the extra words as
# well as for the error and a low surface score becomes ambiguous; those are kept as a minority
# because they cover error types no substitution can express. An omission removes rather than adds.
CATEGORY_WEIGHTS = {
    "laterality_error": 6,
    "severity_error": 6,
    "size_measurement_error": 6,
    "morphology_error": 6,
    "location_error": 6,
    "negation_error": 6,
    "omission_of_finding": 3,
    "out_of_field": 1,
    "comparison_error": 1,
    "modality_unobservable": 1,
}

# Lexicon each appended-sentence category depends on (see injection.lexicon_coverage).
_LEXICON_OF = {"out_of_field": "out_of_field", "comparison_error": "comparison_error",
               "modality_unobservable": "modality_unobservable"}


def plan_categories(modality: str, acquisition: str, *, use_llm: bool,
                    only: list[str] | None = None, exclude: list[str] | None = None
                    ) -> tuple[list[str], dict[str, str]]:
    """(categories that can fire, {category: why it cannot}) for this setup."""
    cov = inj_mod.lexicon_coverage(modality, acquisition)
    usable, off = [], {}
    for cat in (only or list(CATEGORY_WEIGHTS)):
        if cat not in CATEGORY_WEIGHTS:
            raise ValueError(f"unknown category {cat!r}; choose from {list(CATEGORY_WEIGHTS)}")
        if exclude and cat in exclude:
            off[cat] = "excluded"
        elif cat in NEEDS_CLAIMS and not use_llm:
            off[cat] = "needs claim extraction (drop --no-llm and set ANNOTATION_LLM_*)"
        elif cat in _LEXICON_OF and not cov[_LEXICON_OF[cat]]:
            key = "modality" if cat == "modality_unobservable" else "acquisition"
            val = modality if key == "modality" else acquisition
            off[cat] = f"no lexicon for {key} {val!r} (add one with --lexicon)"
        else:
            usable.append(cat)
    return usable, off


def _weighted_order(cats: list[str], rng: random.Random) -> list[str]:
    """Shuffle so heavier categories tend to come first.

    Most categories are inapplicable to a given report (no laterality word, no measurement), and the
    injector walks this list until enough errors land, so weighting the ORDER is what shifts the mix.
    """
    pool, out = list(cats), []
    while pool:
        pick = rng.choices(range(len(pool)), weights=[CATEGORY_WEIGHTS[c] for c in pool], k=1)[0]
        out.append(pool.pop(pick))
    return out


def inject_reports(rows: list[dict], *, modality: str, acquisition: str, categories: list[str],
                   use_llm: bool, seed: int = 0, significant_rate: float = 0.5,
                   log=print) -> list[dict]:
    rng = random.Random(seed)
    bodies = [body_text(r) for r in rows]

    if use_llm and any(c in USES_CLAIMS for c in categories):
        from . import llm
        from .extract import LLMReportParser
        llm.require_model()
        log(f"extracting claims for {len(bodies)} reports via {llm.MODEL} at {llm.BASE_URL} ...")
        t0 = time.time()
        parsed = LLMReportParser().parse_many(bodies)
        s = llm.STATS.as_dict()
        log(f"  done in {time.time() - t0:.0f}s · {s}")
        if s["errors"] or s["empty_responses"]:
            log(f"  WARNING: {s['errors']} failed and {s['empty_responses']} empty LLM responses; "
                f"those sentences contribute no claims, so omission and negation fire less often. "
                f"Empty answers from a reasoning model usually mean it ran out of tokens while "
                f"thinking: raise ANNOTATION_LLM_MAX_TOKENS (e.g. 4096).")
    else:
        parsed = [([], b) for b in bodies]

    # Fabrication needs donor sentences from OTHER reports, never the study's own.
    donor_pool = inj_mod.build_donor_pool(bodies)

    out = []
    for row, body, (claims, aligned) in zip(rows, bodies, parsed):
        t0 = time.time()
        sid = row["study_id"]
        n_errors = rng.choice([1, 1, 2, 2, 3])
        text = aligned or body
        applied: list[dict] = []
        order = _weighted_order([c for c in categories if c not in NEEDS_CLAIMS or claims], rng)
        # Claim spans are token indices into `text` as extracted. An edit that keeps the token count
        # (laterality, severity, size, most location swaps) leaves them valid; one that changes it
        # (omission, an inserted or removed "no", "kidney" -> "adrenal gland", an appended sentence)
        # shifts every later span, and a stale span makes omission drop the wrong sentence or puts
        # an inserted "no" in front of the wrong word -- while the record still names the intended
        # finding, i.e. corrupted ground truth. So claims are dropped the moment the count changes.
        basis = len(text.split())
        for cat in order:
            if len(applied) >= n_errors:
                break
            try:
                res = inj_mod.inject(
                    text, claims, cat, seed=rng.randrange(1 << 30),
                    significant=rng.random() < significant_rate,
                    modality=modality, acquisition=acquisition,
                    donor_sentence=(inj_mod.pick_donor_sentence(donor_pool, rng)
                                    if cat == "fabrication_of_finding" else ""))
            except Exception as e:  # noqa: BLE001 - one bad category must not lose the study
                log(f"    {sid} {cat}: {type(e).__name__}: {e}")
                continue
            if res is None:                    # inapplicable to this report
                continue
            text = res.text
            applied.extend(i.to_dict() for i in res.injections)
            if claims and len(text.split()) != basis:
                claims = []

        text = restore_checked(body, text)
        if is_sectioned(row):
            # Errors go into the findings only; the impression is re-attached unchanged. An injected
            # finding is therefore not propagated into the impression -- a report disagreeing with
            # its own impression is a realistic failure, and the lesser problem than appended
            # sentences landing after the impression, which no generator would produce.
            impression = (row.get("impression") or "").strip()
            rec_text = f"FINDINGS: {text}\nIMPRESSION: {impression}" if impression else f"FINDINGS: {text}"
            fields = {"findings": text, "impression": impression, "text": rec_text}
        else:
            fields = {"findings": "", "impression": "", "text": text}

        out.append({"study_id": sid, "system": "injected", "strategy": "error_injection",
                    "derived_from_reference": True, **fields,
                    "modality": modality, "acquisition": acquisition,
                    "prompt_version": f"inject-seed{seed}", "n_errors_requested": n_errors,
                    "injected_errors": applied, "seconds": round(time.time() - t0, 1),
                    "error": None if applied else "no category applied"})
        log(f"  {sid} · {len(applied)}/{n_errors} errors · "
            f"{', '.join(sorted({a['category'] for a in applied})) or 'NONE'}")
    return out
