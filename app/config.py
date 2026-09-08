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

# ---- cases page layout -----------------------------------------------------
# True: reference | candidate | questions side by side, each in its own scrollable box, so the
# reports stay in view while scrolling through the questions. False: reports on top, questions
# below (the page scrolls as a whole).
QUESTIONS_BESIDE_REPORTS = True
# Height of the three boxes in pixels (capped to the viewport by theme.py).
PANEL_HEIGHT_PX = 720

# ---- hover highlighting ----------------------------------------------------
# Cases may carry precomputed "links" (scripts/precompute_links.py): sentence pairs that
# describe the same finding in reference and candidate. With highlighting on, hovering a
# linked sentence lights up its counterpart(s) in the other report and scrolls them into
# view. Raters can toggle it under the Logout button; the state is stored with every rating.
HIGHLIGHT_DEFAULT = True

# ---- assignment ------------------------------------------------------------
# Each rater is assigned a fixed set of studies (volumes); for every assigned
# study they rate ALL candidate reports of that study, one after the other, so
# the reference only has to be read once. The candidates of a study appear in
# a rater-specific random order and the model behind each candidate is hidden.
STUDIES_PER_RATER = 100
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
