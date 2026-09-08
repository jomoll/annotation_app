"""Per-rater rating persistence — one append-only CSV per rater.

Every save appends a row; readers take the latest row per case_id. Nothing is
ever overwritten, so the full history is kept, and concurrent raters can't
clobber each other because each only writes to their own file.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import pandas as pd

from config import DATA_DIR
from questions import QUESTION_KEYS

ANNOT_DIR = DATA_DIR / "annotations"
RATING_FIELDS = [*QUESTION_KEYS, "comment",
                 "highlights_on"]  # "on"/"off": whether hover highlighting was active when saved
FIELDS = ["rater", "saved_at", "case_id", "study_id", "candidate_id", *RATING_FIELDS]


def _path(rater_slug: str) -> Path:
    return ANNOT_DIR / f"{rater_slug}_annotations.csv"


def _migrate_if_needed(path: Path) -> None:
    """If the questionnaire changed since this file was created, rewrite it under the current
    header (old rows keep their values, new columns are empty) so appended rows line up."""
    with path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f), None)
    if header == FIELDS:
        return
    df = pd.read_csv(path, dtype=str).fillna("")
    df = df.reindex(columns=FIELDS, fill_value="")
    df.to_csv(path, index=False)


def save_rating(rater_slug: str, case: dict, rating: Dict[str, str]) -> None:
    ANNOT_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "rater": rater_slug,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case["id"],
        "study_id": case.get("study_id", ""),
        "candidate_id": case.get("candidate_id", ""),
        **{k: rating.get(k, "") for k in RATING_FIELDS},
    }
    path = _path(rater_slug)
    write_header = not path.exists()
    if not write_header:
        _migrate_if_needed(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)


def load_ratings(rater_slug: str) -> Dict[str, dict]:
    """{case_id: {field: value}} — latest saved row per case for this rater."""
    path = _path(rater_slug)
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return {}
    if "saved_at" in df.columns:
        df = df.sort_values("saved_at", kind="stable")
    return {str(r["case_id"]): r.to_dict() for _, r in df.iterrows()}


def all_rater_slugs() -> list[str]:
    if not ANNOT_DIR.exists():
        return []
    return sorted(p.name[: -len("_annotations.csv")] for p in ANNOT_DIR.glob("*_annotations.csv"))


def all_ratings_latest() -> pd.DataFrame:
    """Latest rating per (rater, case) across all raters."""
    rows = [r for slug in all_rater_slugs() for r in load_ratings(slug).values()]
    return pd.DataFrame(rows, columns=FIELDS) if rows else pd.DataFrame(columns=FIELDS)


def all_ratings_history() -> pd.DataFrame:
    """Every saved row from every rater (the raw append-only history)."""
    frames = []
    for slug in all_rater_slugs():
        try:
            frames.append(pd.read_csv(_path(slug), dtype=str).fillna(""))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame(columns=FIELDS)
    return pd.concat(frames, ignore_index=True).reindex(columns=FIELDS, fill_value="")
