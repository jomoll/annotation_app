"""Per-rater 'last viewed case', so logging back in resumes where the rater left off —
independent of whether that case was actually rated."""
from __future__ import annotations

import json
import os

from config import DATA_DIR

PROGRESS_PATH = DATA_DIR / "progress.json"


def load() -> dict:
    if not PROGRESS_PATH.exists():
        return {}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_last_case(rater_slug: str, case_id: str) -> None:
    data = load()
    if data.get(rater_slug) == case_id:
        return
    data[rater_slug] = case_id
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PROGRESS_PATH)
