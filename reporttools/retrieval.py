"""Similar-case retrieval: another patient's real report as a candidate.

Ranking donors on whole-report similarity does not work, and the failure is instructive. A normal
study's nearest neighbour is another normal study, text-identical bar a word -- on brain MRI the top
hit scored 0.93 cosine. That candidate is not plausibly wrong, it is CORRECT, and it lands at the top
of the range, the opposite of the job.

So donors are ranked on CONTEXT (indication, history, technique, specimen -- whatever the source
has), which is what makes a donor plausible for this patient, and then required to DIFFER in
findings: any donor whose findings resemble the target's above `max_findings_similarity` is a copy,
not a candidate, and is skipped. The result reads as a real report for a patient with this complaint,
describing a different case.

Optional per-study labels (a 0/1 CSV, e.g. pathology or diagnosis labels) steer the donor choice:

  similar    donor shares the target's labels (exact match preferred). Gets the main diagnosis
             right and the specifics wrong: a plausible near-miss, the upper-middle of the range.
  divergent  donor shares none of them. Wrong diagnosis: pushes toward the floor.

Pick by which band your models leave empty (docs/new_modality_guide.pdf, step 4). Without labels
both modes reduce to "most similar context whose findings still diverge".

Donors must come from studies that are NOT in the evaluation set -- ideally the training split,
filtered the same way as the evaluation studies, so a donor is always the same kind of examination.
"""
from __future__ import annotations

import re
import time

from .reports import body_text, candidate_fields, context_text

# Section headings ("DIAGNOSIS:", "MARGINS:", "LYMPH NODES:", "FINDINGS:") are shared by every report
# of a templated modality and say nothing about the case. Left in, they dominate the findings
# similarity of a pathology pool: a colon adenocarcinoma and a skin SCC scored 0.98 cosine on the
# test set, so every donor looked like a copy. Headings are dropped before comparing findings.
_HEADING = re.compile(r"(?m)^\s*[A-Z][A-Z0-9 /&()-]{2,}:\s*|\b[A-Z]{3,}(?: [A-Z]{3,})*:")


def _content(text: str) -> str:
    return _HEADING.sub(" ", text or "")


def _tfidf_sim(queries: list[str], docs: list[str], min_df: int):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import linear_kernel

    for df in (min_df, 1):
        try:
            vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=df, sublinear_tf=True)
            m = vec.fit_transform(docs)
            return linear_kernel(vec.transform(queries), m)
        except ValueError:                      # empty vocabulary on a tiny pool
            continue
    raise ValueError("could not build a TF-IDF vocabulary from the donor pool")


def retrieve(targets: list[dict], donors: list[dict], *, context_cols: tuple[str, ...],
             labels: dict[str, frozenset] | None = None, label_mode: str = "similar",
             max_findings_similarity: float = 0.55, match_col: str | None = None,
             log=print) -> list[dict]:
    """`match_col` makes a column a hard constraint: the donor must carry the same value (case and
    surrounding whitespace ignored). Ranking on context only PREFERS a similar examination; with a
    thin donor pool it will still hand a colon resection a thyroid report, which a reader spots
    without reading a word. Use it for the examination type or specimen organ."""
    labels = labels or {}
    key = (lambda r: (r.get(match_col) or "").strip().lower()) if match_col else None
    donor_ids = [d["study_id"] for d in donors]
    # min_df=2 drops words seen in only one donor -- typos and one-offs in a large corpus, but in a
    # pool of a few dozen reports it drops every distinctive word ("sigmoid", "caecum") and leaves
    # only the template, so unrelated reports look identical (0.98 vs 0.39 on the test pool).
    min_df = 2 if len(donors) >= 500 else 1
    fnd_sim = _tfidf_sim([_content(body_text(t)) for t in targets],
                         [_content(body_text(d)) for d in donors], min_df=min_df)

    if context_cols:
        tgt_ctx = [context_text(t, context_cols) for t in targets]
        don_ctx = [context_text(d, context_cols) for d in donors]
        has_ctx = any(tgt_ctx) and any(don_ctx)
    else:
        has_ctx = False
    if has_ctx:
        ctx_sim = _tfidf_sim(tgt_ctx, don_ctx, min_df=1)
        ranking = "context"
    else:
        # No context columns: rank by findings similarity instead, so the donor is the most similar
        # report that is still NOT a near-copy -- the threshold below does the separating.
        ctx_sim = fnd_sim
        ranking = "findings (no context columns)"
    log(f"retrieval: ranking donors by {ranking}; rejecting findings cosine > {max_findings_similarity}")

    out = []
    for k, tgt in enumerate(targets):
        t0 = time.time()
        sid = tgt["study_id"]
        want = labels.get(sid, frozenset())
        pick = best = None
        for j in ctx_sim[k].argsort()[::-1]:
            if donor_ids[j] == sid or fnd_sim[k][j] > max_findings_similarity:
                continue
            if key and key(donors[j]) != key(tgt):
                continue
            have = labels.get(donor_ids[j], frozenset())
            if not labels:
                pick = j
                break
            if label_mode == "divergent":
                if (want & have) or (not want and not have):
                    continue
                pick = j
                break
            # similar: share the labels, exact match preferred; a normal target wants a normal donor
            if want and not (want & have):
                continue
            if not want and have:
                continue
            if want == have:
                pick = j
                break
            if best is None:
                best = j
        if pick is None:
            pick = best

        rec = {"study_id": sid, "system": "retrieved", "strategy": "similar_case_retrieval",
               "derived_from_reference": True, "prompt_version": f"retrieve-tfidf-{label_mode}",
               "target_labels": sorted(want)}
        if pick is None:
            rec.update({"findings": "", "impression": "", "text": "", "source_study_id": "",
                        "seconds": round(time.time() - t0, 1),
                        "error": f"no donor satisfies --label-mode {label_mode} with findings "
                                 f"cosine <= {max_findings_similarity}"
                                 + (f" and the same {match_col!r}" if match_col else "")})
            log(f"  {sid} · SKIPPED (no suitable donor)")
        else:
            rec.update(candidate_fields(donors[pick]))
            rec.update({"source_study_id": donor_ids[pick],
                        "context_similarity": round(float(ctx_sim[k][pick]), 4) if has_ctx else None,
                        "findings_similarity": round(float(fnd_sim[k][pick]), 4),
                        "donor_labels": sorted(labels.get(donor_ids[pick], frozenset())),
                        "seconds": round(time.time() - t0, 1), "error": None})
            log(f"  {sid} <- {donor_ids[pick]} · findings cosine {fnd_sim[k][pick]:.3f}")
        out.append(rec)
    return out
