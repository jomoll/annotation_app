"""Per-rater case assignment.

The unit of assignment is a *study* (one reference report). A rater who is
assigned a study rates every candidate report of that study, back to back, in
a rater-specific shuffled order — the reference is read once, the candidates
are compared against it one after another, and the model behind each
candidate stays hidden.

Studies are handed out greedily, least-covered-first (ties broken by study id),
so every study converges toward TARGET_COVERAGE raters before any study gets
more. An assignment is made once, on the rater's first visit to the Cases
page, persisted, and never reshuffled afterwards; logging back in resumes the
exact same list.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from collections import Counter, defaultdict

import auth
from config import DATA_DIR, NON_RATER_EMAILS, STUDIES_PER_RATER

ASSIGN_PATH = DATA_DIR / "assignments.json"
NON_RATER_SLUGS = {auth.user_slug(e) for e in NON_RATER_EMAILS}


def load() -> dict:
    if not ASSIGN_PATH.exists():
        return {}
    try:
        return json.loads(ASSIGN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    ASSIGN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ASSIGN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ASSIGN_PATH)


def _by_study(cases: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        groups[c["study_id"]].append(c)
    return groups


def studies_for(rater_slug: str, cases: list[dict]) -> list[str]:
    """This rater's assigned study ids, extending a prior assignment if it is short."""
    data = load()
    groups = _by_study(cases)
    available = set(groups)

    existing = [s for s in data.get(rater_slug, []) if s in available]
    target = min(STUDIES_PER_RATER, len(available))
    if len(existing) >= target:
        return existing

    coverage: Counter = Counter()
    for slug, ids in data.items():
        if slug == rater_slug or slug in NON_RATER_SLUGS or slug.startswith("_"):
            continue
        coverage.update(s for s in ids if s in available)

    have = set(existing)
    pool = sorted((s for s in available if s not in have), key=lambda s: (coverage.get(s, 0), s))
    picked = existing + pool[: target - len(existing)]
    if picked != existing:
        data[rater_slug] = picked
        _save(data)
    return picked


def cases_for(rater_slug: str, cases: list[dict]) -> list[dict]:
    """Ordered case list for this rater: assigned studies in assignment order, each study's
    candidates in a per-rater deterministic shuffle."""
    groups = _by_study(cases)
    out: list[dict] = []
    for study_id in studies_for(rater_slug, cases):
        cands = list(groups[study_id])
        seed = int(hashlib.sha256(f"{rater_slug}|{study_id}".encode()).hexdigest(), 16) % (2**32)
        random.Random(seed).shuffle(cands)
        out.extend(cands)
    return out


def coverage_table(cases: list[dict]) -> dict[str, int]:
    """{study_id: number of real raters assigned} — for the admin page."""
    data = load()
    cov: Counter = Counter()
    for slug, ids in data.items():
        if slug in NON_RATER_SLUGS or slug.startswith("_"):
            continue
        cov.update(ids)
    return {s: cov.get(s, 0) for s in _by_study(cases)}
