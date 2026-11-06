"""Reading reference reports, in one schema whatever the source.

Input is a CSV or JSONL with one row per study:

    study id     `study_id` (or `study_uid` / `id`, or name it with --id-col)       required
    report text  `findings` + optional `impression`, or a single `report` / `text`  required
    context      `indication`, `clinical_information`, `history`, `technique`, ...  optional,
                 used by retrieval to find a donor with the same clinical picture

Column names are matched case-insensitively. Rows come back as plain dicts with the id under
`study_id` and every other column kept as a string.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ID_COLUMNS = ("study_id", "study_uid", "id", "case_id", "accession")
CONTEXT_COLUMNS = ("indication", "clinical_information", "clinical_history", "history",
                   "clinical_indication", "reason_for_exam", "technique", "examination", "exam",
                   "procedure", "specimen", "specimen_type", "site")
TEXT_COLUMNS = ("report", "text", "report_text")


def _read_one(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        df = pd.DataFrame(rows)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        df = pd.DataFrame(data["cases"] if isinstance(data, dict) and "cases" in data else data)
    else:
        df = pd.read_csv(path, dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df.astype(object).where(df.notna(), "").astype(str)


def load_reports(paths: list[Path], id_col: str | None = None) -> list[dict]:
    """Every row of every file, de-duplicated by study id (first occurrence wins)."""
    frames = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise FileNotFoundError(p)
        frames.append(_read_one(p))
    df = pd.concat(frames, ignore_index=True)
    col = (id_col or "").lower() or next((c for c in ID_COLUMNS if c in df.columns), None)
    if not col or col not in df.columns:
        raise ValueError(f"no study id column; expected one of {ID_COLUMNS} or --id-col "
                         f"(columns: {list(df.columns)})")
    if not any(c in df.columns for c in ("findings",) + TEXT_COLUMNS):
        raise ValueError(f"no report text; expected 'findings' (+ 'impression') or one of "
                         f"{TEXT_COLUMNS} (columns: {list(df.columns)})")
    df = df.rename(columns={col: "study_id"}) if col != "study_id" else df
    df["study_id"] = df["study_id"].str.strip()
    df = df[df["study_id"] != ""].drop_duplicates("study_id")
    return df.to_dict("records")


def is_sectioned(row: dict) -> bool:
    """True if the row gives findings separately (and maybe an impression)."""
    return bool((row.get("findings") or "").strip())


def reference_text(row: dict) -> str:
    """The text a candidate is compared against: findings + impression, or the whole report."""
    f, i = (row.get("findings") or "").strip(), (row.get("impression") or "").strip()
    if f or i:
        return (f"FINDINGS: {f}\nIMPRESSION: {i}" if i else f"FINDINGS: {f}").strip()
    for c in TEXT_COLUMNS:
        if (row.get(c) or "").strip():
            return row[c].strip()
    return ""


def body_text(row: dict) -> str:
    """The part errors are injected into: the findings if given separately, else the report."""
    if is_sectioned(row):
        return row["findings"].strip()
    for c in TEXT_COLUMNS:
        if (row.get(c) or "").strip():
            return row[c].strip()
    return (row.get("impression") or "").strip()


def context_text(row: dict, columns: tuple[str, ...]) -> str:
    return " ".join((row.get(c) or "").strip() for c in columns if (row.get(c) or "").strip())


def present_context_columns(rows: list[dict], wanted: tuple[str, ...] = CONTEXT_COLUMNS) -> tuple[str, ...]:
    """The context columns that actually carry text in these rows."""
    return tuple(c for c in wanted if any((r.get(c) or "").strip() for r in rows))


def candidate_fields(row: dict) -> dict:
    """findings / impression / text for a candidate made from `row`'s report, in the app's schema.

    A sectioned report keeps its sections (the app shows them as separate blocks); an unsectioned
    one -- typical of pathology -- becomes a single `text` so no heading is invented that the
    reference does not have.
    """
    if is_sectioned(row):
        f, i = row["findings"].strip(), (row.get("impression") or "").strip()
        return {"findings": f, "impression": i, "text": reference_text(row)}
    return {"findings": "", "impression": "", "text": reference_text(row)}


def load_labels(path: Path | None, id_col: str | None = None) -> dict[str, frozenset]:
    """study id -> set of positive labels, from a CSV of 0/1 columns. Empty set == normal."""
    if not path:
        return {}
    df = _read_one(Path(path))
    col = (id_col or "").lower() or next((c for c in ID_COLUMNS if c in df.columns), None)
    if not col or col not in df.columns:
        raise ValueError(f"labels file {path} has no study id column")
    out: dict[str, frozenset] = {}
    label_cols = []
    for c in df.columns:
        if c == col:
            continue
        v = pd.to_numeric(df[c], errors="coerce")
        if v.notna().mean() > 0.95 and set(v.dropna().unique()) <= {0, 1}:
            label_cols.append(c)
    if not label_cols:
        raise ValueError(f"labels file {path} has no 0/1 label columns")
    vals = df[label_cols].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    for sid, r in zip(df[col].str.strip(), vals.itertuples(index=False)):
        out[sid] = frozenset(c for c, v in zip(label_cols, r) if v)
    return out


def flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
