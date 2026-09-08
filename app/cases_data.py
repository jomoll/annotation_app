"""Loads the case pool.

data/cases.json is produced by scripts/build_pool_*.py and is deliberately NOT
committed (report text from CT-RATE etc. is gated). If it is missing, the small
synthetic data/cases.example.json is loaded instead so the app can be tried out
immediately — the UI shows a banner in that case.

Case schema (one entry per reference/candidate pair):
    id            unique, e.g. "valid_12_a_1__dia_llama"
    study_id      groups all candidates of one reference report
    candidate_id  which model produced the candidate (hidden from raters)
    reference     {"clinical_information": str, "technique": str, "findings": str, "impression": str}
    candidate     {"text": str}
    meta          free-form, optional (e.g. abnormality labels)
"""
from __future__ import annotations

import json

from config import CASES_PATH, EXAMPLE_CASES_PATH


def _load(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw.get("meta", {}), raw.get("cases", [])
    return {}, raw


if CASES_PATH.exists():
    POOL_META, CASES = _load(CASES_PATH)
    USING_EXAMPLE = False
elif EXAMPLE_CASES_PATH.exists():
    POOL_META, CASES = _load(EXAMPLE_CASES_PATH)
    USING_EXAMPLE = True
else:
    POOL_META, CASES, USING_EXAMPLE = {}, [], True

CASES_BY_ID = {c["id"]: c for c in CASES}
STUDY_IDS = sorted({c["study_id"] for c in CASES})
CANDIDATE_IDS = sorted({c["candidate_id"] for c in CASES})
