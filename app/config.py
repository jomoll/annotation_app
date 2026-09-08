"""Deployment-level settings. Everything a collaborator is likely to change
when adapting the app to their own data lives here or in questions.py."""
from __future__ import annotations

import os
from pathlib import Path

APP_TITLE = "Radiology Report Annotation"
APP_SUBTITLE = "Reader study · reference vs. model-generated reports"
MODALITY_LABEL = "Chest CT"  # shown in the UI header; purely cosmetic

# ---- data locations --------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ANNOTATION_DATA_DIR", ROOT / "data"))
CASES_PATH = Path(os.environ.get("ANNOTATION_CASES", DATA_DIR / "cases.json"))
EXAMPLE_CASES_PATH = ROOT / "data" / "cases.example.json"  # bundled with the repo, independent of DATA_DIR

# ---- assignment ------------------------------------------------------------
# Each rater is assigned a fixed set of studies (volumes); for every assigned
# study they rate ALL candidate reports of that study, one after the other, so
# the reference only has to be read once. The candidates of a study appear in
# a rater-specific random order and the model behind each candidate is hidden.
STUDIES_PER_RATER = 20
# Studies are handed out least-covered-first so that every study converges to
# this many raters before any study gets a third one.
TARGET_COVERAGE = 2
# Logins used for testing / administration. Their assignments do not count
# toward a study's coverage, so they never crowd out real raters.
NON_RATER_EMAILS = {e.strip().lower() for e in os.environ.get("ANNOTATION_TEST_EMAILS", "").split(",") if e.strip()}

# ---- roles -----------------------------------------------------------------
# Comma-separated list of emails that are treated as admins at login (in
# addition to accounts promoted via `python -m app.manage set-role`).
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ANNOTATION_ADMIN_EMAILS", "").split(",") if e.strip()}

# ---- signup form -----------------------------------------------------------
POSITIONS = ["Resident", "Attending / board-certified radiologist", "Other clinician", "Non-clinician"]
