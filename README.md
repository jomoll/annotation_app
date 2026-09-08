# Radiology report annotation app

A small Streamlit reader-study app for rating **model-generated radiology
reports against the original (reference) report**. Built for chest CT
(CT-RATE / RadGenome-ChestCT candidates from Dia-LLaMA, Reg2RG, M3D and RadFM),
but the modality, questions and data source are all pluggable — the point of
this repo is that collaborators can drop in their own reference/candidate pairs.

<p align="center">
reference report (left) · candidate report (right) · six questions below
</p>

## What a rater sees

1. **Overview** — a short explanation of the interface and the six questions.
2. **Cases** — for every case the reference report on the left, one candidate
   on the right, and the questionnaire underneath:

   | # | Question | Scale |
   |---|---|---|
   | 1 | Completeness — is all necessary information for the clinical question present? | 1–5 |
   | 2 | Correctness — how medically accurate is it, regardless of completeness? | 1–5 |
   | 3 | Same diagnosis — does it conclude the same main diagnosis as the reference? | yes / no |
   | 4 | Clinical safety — does any statement lead to a serious safety risk? | yes / no |
   | 5 | Overall score — subjective, excluding style, not derived from the other scores | 1–5 |
   | 6 | Presentation quality — clarity, prioritisation, conciseness (not part of the overall score) | 1–3 |

   The per-score definitions are in `app/questions.py` and shown to raters on the
   Overview tab and next to every question.

   Answers autosave on every navigation and are prefilled when the rater comes
   back. Completed cases get a ✓ in the case selector.

Each rater is assigned a fixed set of **studies**; for every study they rate all
its candidates back to back (reference read once), in a rater-specific shuffled
order, with the model identity hidden. Studies are handed out least-covered-first
so every study reaches the target number of raters before any gets more.

Admins additionally get **Admin: Ratings** — progress per rater, mean scores per
(unblinded) candidate system, study coverage, and CSV export.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app/app.py --server.address 127.0.0.1 --server.port 8750
```

Without a case pool the app runs on the bundled `data/cases.example.json`
(one CT-RATE study with its four candidates; a banner says so).

On first start the app seeds a host account **`admin` / `admin`** (role admin).
Change that password before anyone else can reach the app:

```bash
python -m app.manage set-password admin <new password>
```

The admin banner keeps nagging until you do. To make a regular rater account an
admin instead:

```bash
python -m app.manage set-role you@example.org admin
# or: export ANNOTATION_ADMIN_EMAILS=you@example.org before starting the app
```

The `admin` account can rate cases for testing; its assignments never count
toward study coverage.

If the report text is sensitive, bind to localhost and reach the app through an
SSH tunnel (`ssh -L 8750:127.0.0.1:8750 host`) rather than exposing the port.

## Build the CT-RATE pool

```bash
python scripts/build_pool_ctrate.py --n-studies 100 --seed 17
```

Inputs (CSV, text only): the CT-RATE validation reports and abnormality labels
(`ibrahimhamamci/CT-RATE` on Hugging Face, gated) and the generated reports of
Dia-LLaMA, Reg2RG, M3D and RadFM on the RadGenome-ChestCT test split
(`cngvng/3D-CT-report-generation`). By default they are expected under
`data/raw/` in the Hugging Face layout; pass `--reports`, `--labels` and
`--candidate NAME=PATH` to point elsewhere.

The script pairs each reference (Findings + Impression) with the four
candidates, keeps one volume per patient, stratifies by the number of positive
CT-RATE abnormality labels (0 / 1–2 / 3–4 / 5+) and writes `data/cases.json`.
The same `--seed` gives the same pool on every machine. Reg2RG's per-region
output is flattened to plain prose (the region scaffolding is prompt template,
not report).

`data/raw/` and `data/cases.json` are git-ignored on purpose: CT-RATE is gated
(CC BY-NC-SA 4.0) and must not be redistributed. The same goes for accounts,
assignments and ratings.

## Adapting it to your own data

* **Cases** — write a `data/cases.json` in the schema shown in
  `data/cases.example.json` (see the docstring in `app/cases_data.py`), or copy
  `scripts/build_pool_ctrate.py` and change the loaders. A candidate can be a
  single `text` or split into `findings` / `impression`.
* **Questions** — edit `app/questions.py`. Each question is a dict (key, prompt,
  type `likert` or `binary`, per-score anchors; the anchor keys define the scale,
  so 1–3 and 1–5 mix freely). Stored values are the score or option label, so
  rewording anchors never invalidates saved ratings.
* **Study size / coverage / title** — `app/config.py` (`STUDIES_PER_RATER`,
  `TARGET_COVERAGE`, `APP_TITLE`, `MODALITY_LABEL`, …).
* **Look** — `app/theme.py` and `app/.streamlit/config.toml`.

## Accounts and data

* Raters sign up themselves (name, email, password, optional position and
  experience). Passwords are stored **only as salted PBKDF2-SHA256 hashes**;
  nobody can look them up. Reset one with
  `python -m app.manage reset-password <email>` (prints a new random password)
  or set one explicitly with `python -m app.manage set-password <email> <pw>`.
* Ratings: one append-only CSV per rater in `data/annotations/`; the latest row
  per case wins. `python -m app.manage export` dumps the full history.
* Assignments: `data/assignments.json` (`ANNOTATION_TEST_EMAILS` lists test
  logins whose assignments should not count toward coverage).
* Everything under `data/` except the example pool is git-ignored.

## Layout

```
app/
  app.py          UI: auth screen, overview, cases, admin page
  questions.py    the questionnaire (edit this)
  config.py       titles, assignment size, paths, admin emails
  auth.py         signup / login, hashed passwords
  assignment.py   per-rater study assignment (least-covered-first)
  storage.py      append-only rating CSVs
  progress.py     resume position per rater
  cases_data.py   loads data/cases.json (falls back to the example)
  theme.py        CSS
  manage.py       CLI: list-users, set-role, reset-password, export
scripts/
  build_pool_ctrate.py   CT-RATE + Dia-LLaMA/Reg2RG/M3D/RadFM pool builder
data/
  cases.example.json     example pool: one CT-RATE study with its four candidates (schema reference)
```

## License

MIT — see `LICENSE`. Note that the report text you load into the app carries its
own license (CT-RATE: CC BY-NC-SA 4.0, gated access).
