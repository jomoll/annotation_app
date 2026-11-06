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
2. **Cases** — for every case the reference report, one candidate and the
   questionnaire side by side, each in its own scrollable box, so the reports
   stay in view while answering (`QUESTIONS_BESIDE_REPORTS = False` in
   `app/config.py` puts the questions underneath instead):

   | # | Question | Scale |
   |---|---|---|
   | 1 | Completeness — what share of the reference's findings does the candidate reproduce? | 1–5 |
   | 2 | Correctness — how medically accurate is it, regardless of completeness? | 1–5 |
   | 3 | Same diagnosis — does it conclude the same main diagnosis as the reference? | yes / no |
   | 4 | Clinical safety — does any statement lead to a serious safety risk? | yes / no |
   | 5 | Overall score — subjective, excluding style, not derived from the other scores | 1–5 |
   | 6 | Presentation quality — clarity, prioritisation, conciseness (not part of the overall score) | 1–3 |

   The per-score definitions are in `app/questions.py` and shown to raters on the
   Overview tab and next to every question.

   Answers autosave on every navigation and are prefilled when the rater comes
   back. Completed cases get a ✓ in the case selector.

   **Hover highlighting**: if the cases carry precomputed sentence links (see
   below), hovering a sentence in one report lights up the sentence(s) describing
   the same finding in the other report and scrolls them into view. Raters can
   switch this off under the Logout button (default on, `HIGHLIGHT_DEFAULT` in
   `app/config.py`); the state is stored with every rating (`highlights_on`), so
   assisted and unassisted ratings can be compared.

Pools can hold several **modalities** (`MODALITIES` in `app/config.py`, one
`modality` key per case). A rater is bound to one modality: new raters are
routed to the first modality that still has fewer than `RATERS_PER_MODALITY`
raters, so with two per modality the first two sign-ups rate CT, the next two
X-ray and the next two brain MRI. Each rater is assigned a fixed set of **studies**; for every study
they rate its candidate(s), with the model identity hidden. Studies are picked
least-covered-first, and within a coverage level round-robin over the candidate
systems, so the shared block is balanced across models. A new rater's list
*starts* with the studies another rater already has (`OVERLAP_FIRST` in
`app/config.py`), so inter-rater agreement can be computed early instead of only
once the second rater is nearly finished. A list is ordered once, at first
assignment, and never reordered afterwards: a rater's open session tracks a
position, not a case, so reordering mid-study silently moves them elsewhere. If a study has several
candidates they come back to back in a rater-specific shuffled order. Studies are handed out least-covered-first
so every study reaches the target number of raters before any gets more.

Admins additionally get **Admin: Ratings** — progress per rater, mean scores per
(unblinded) candidate system, study coverage, and CSV export.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app/app.py --server.address 127.0.0.1 --server.port 8750
```

Without a case pool the app runs on the bundled `data/cases.example.json`
(two invented chest-CT studies, one with three candidates and hover links; no
patient data; a banner says so).

On first start the app seeds a host account **`admin` / `admin`** (role admin).
Change that password before anyone else can reach the app:

```bash
python -m app.manage set-password admin <new password>
```

To make a regular rater account an admin instead:

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
python scripts/build_pool_ctrate.py --n-studies 200 --candidates-per-study 1 --seed 17
```

Inputs (CSV, text only): the CT-RATE validation reports and abnormality labels
(`ibrahimhamamci/CT-RATE` on Hugging Face, gated) and the generated reports of
Dia-LLaMA, Reg2RG, M3D and RadFM on the RadGenome-ChestCT test split
(`cngvng/3D-CT-report-generation`). By default they are expected under
`data/raw/` in the Hugging Face layout; pass `--reports`, `--labels` and
`--candidate NAME=PATH` to point elsewhere.

The script pairs each reference (Findings + Impression) with candidate reports,
keeps one volume per patient, stratifies by the number of positive CT-RATE
abnormality labels (0 / 1–2 / 3–4 / 5+) and writes `data/cases.json`.
`--candidates-per-study 1` gives every reference exactly one candidate, with the
models spread equally and randomly over the studies (200 studies = 50 per model).
That is the design for validating a metric: independent, diverse pairs. Omit the
flag to get all candidates for every study, which suits paired model comparison
instead.
The same `--seed` gives the same pool on every machine. Reg2RG's per-region
output is flattened to plain prose (the region scaffolding is prompt template,
not report).

`data/raw/` and `data/cases.json` are git-ignored on purpose: CT-RATE is gated
(CC BY-NC-SA 4.0) and must not be redistributed. The same goes for accounts,
assignments and ratings.

### Adding a candidate system later

```bash
python scripts/add_candidate.py NAME=path/to/predictions.jsonl   # or .csv
python scripts/precompute_links.py data/cases.json               # links for the new cases
```

Rebalances a one-candidate-per-study pool so every model has an equal share,
separately within each rater's assigned studies and within the unassigned rest,
so a rater halfway through keeps a balanced mix. Cases that already have ratings
are never replaced. The switched studies are logged in the pool's `rebalanced`
metadata.

## Chest X-ray arm (ReXGradient)

```bash
python scripts/select_rexgradient_studies.py --n-studies 200 --normal-share 0.3 --seed 17
/data2/moll/envs/chexagent/bin/python scripts/generate_cxr_candidates.py views     # frontal/lateral
/data2/moll/envs/chexagent/bin/python scripts/generate_cxr_candidates.py chexagent
/data2/moll/envs/cxrgen/bin/python    scripts/generate_cxr_candidates.py maira2
/data2/moll/envs/llavarad/bin/python  scripts/generate_cxr_candidates.py llavarad
python scripts/build_pool_rexgradient.py --candidates-per-study 1 --seed 17 --out data/cases_cxr.json
python scripts/merge_pools.py ct=data/cases_ct.json cxr=data/cases_cxr.json --out data/cases.json
```

References are the ReXGradient-160K public-test reports (indication, comparison,
findings, impression; Hugging Face, non-commercial agreement, must not be
redistributed). Candidates: CXRMate-2's released outputs plus CheXagent-2,
MAIRA-2 and LLaVA-Rad run locally on the test images with the authors' own
prompts (`generate_cxr_candidates.py` documents inputs and environments; the
models pin conflicting transformers versions, hence one env each). Two-view
studies need the frontal image identified: CheXagent-2's view prompt is used,
validated at 97.7% on the labelled test images.

## Precompute sentence links (optional)

```bash
pip install -r requirements-tools.txt
export ANNOTATION_LLM_URL=http://localhost:8000/v1 ANNOTATION_LLM_MODEL=<served model>
python scripts/precompute_links.py data/cases.json
```

Uses the claim extraction and matching in `reporttools/` (vendored from
[radmetric](https://github.com/jomoll/radmetric), no install needed): an LLM extracts clinical
claims from every sentence of both reports, claims are paired one-to-one across
the reports, and each pair becomes a link between the two sentences. Only
matches are stored (character offsets per section), never error categories, so
the app shows correspondence without leaking the metric's verdicts. Links below a
matcher score of 0.85 and normality claims about different anatomy are dropped as
navigation noise. `--no-think` is needed for reasoning models such as Qwen 3.x;
if your server ignores it, raise `ANNOTATION_LLM_MAX_TOKENS` (e.g. 4096) instead.
Report text is sent to the endpoint: use one you are permitted to send it to.
Which model produced the links is recorded in the pool's `links_meta`.

## A new modality, any dataset

The full protocol — choosing the dataset and split, capping the normal share,
checking the quality spread before booking clinicians, reader allocation — is in
[`docs/new_modality_guide.pdf`](docs/new_modality_guide.pdf). The tools it uses
are dataset-independent and need nothing beyond `requirements-tools.txt`
(the spread check in step 3 also needs `requirements-spread.txt` and one GPU):

```bash
pip install -r requirements-tools.txt

# 1. one candidate system per study, normal/abnormal mix equal per system
python scripts/assign_systems.py --references refs.csv --studies eval_ids.txt \
    --systems modelA modelB injected retrieved --out data/raw/us/assignment.csv

# 2. run your models on their assigned studies; write one JSON line per study:
#    {"study_id": ..., "text": ...}  or  {"study_id": ..., "findings": ..., "impression": ...}
#    (scripts/normalize_candidate_format.py strips Markdown, so no system is identifiable by layout)

# 3. do you need fallbacks? score the model candidates on one local GPU (no server, no endpoint)
pip install -r requirements-spread.txt
python scripts/check_spread.py --references refs.csv --studies eval_ids.txt \
    --candidates modelA=a.jsonl modelB=b.jsonl --assignment data/raw/us/assignment.csv \
    --out data/raw/us/spread.csv                       # add --green-model Qwen/Qwen3-4B-Instruct-2507 for the general judge

# 4. optional text-derived strata, ONLY to fill a quality band the models leave empty
python scripts/generate_fallbacks.py coverage --modality us --acquisition abdomen_us \
    --lexicon my_us_lexicon.json
python scripts/generate_fallbacks.py inject --reports refs.csv --studies eval_ids.txt \
    --modality us --acquisition abdomen_us --lexicon my_us_lexicon.json \
    --out data/raw/us/candidates/injected.jsonl
python scripts/generate_fallbacks.py retrieve --reports refs.csv --studies eval_ids.txt \
    --donor-reports train_refs.csv --match-col exam_type \
    --out data/raw/us/candidates/retrieved.jsonl

# 5. pool -> merge -> register the modality in app/config.py MODALITIES
python scripts/build_pool.py --modality us --references refs.csv \
    --candidates modelA=a.jsonl modelB=b.jsonl injected=data/raw/us/candidates/injected.jsonl \
                 retrieved=data/raw/us/candidates/retrieved.jsonl \
    --assignment data/raw/us/assignment.csv --out data/cases_us.json
python scripts/merge_pools.py us=data/cases_us.json --out data/cases.json
python scripts/precompute_links.py data/cases.json     # optional hover links
```

**References** are a CSV or JSONL with a study id, the report as `findings`
(+ `impression`) or as a single `report` column (pathology reports usually are;
the app then shows them as one block), and optionally `indication` /
`clinical_information`, `technique` / `specimen` and `normal` (0/1). See
`reporttools/reports.py`.

**Spread check** (`check_spread.py`) scores every model candidate against its
reference with two metrics, both run in-process with `transformers`: GREEN's
error-counting prompt answered by a local judge, and BERTScore. It prints the
distribution per system with our three arms alongside. Two GREEN judges are
offered: the published `StanfordAIMI/GREEN-RadLlama2-7b` (default, ~16 GB GPU)
and any instruction-tuned model via `--green-model`, e.g.
`Qwen/Qwen3-4B-Instruct-2507` (~10 GB). Validated against our reader ratings
(Spearman, 200 candidates per arm):

| | GREEN-RadLlama2-7b | GREEN with Qwen3-4B | BERTScore |
|---|---|---|---|
| chest CT | +0.21 | +0.02 | +0.36 |
| chest X-ray | +0.50 | +0.55 | +0.44 |
| brain MRI (35 rated) | +0.73 | +0.76 | +0.78 |

Both judges reach the decision we reached by hand: CT and X-ray models cover the
range (9-12% of candidates in the top GREEN band), brain-MRI models do not (0-2%
at the top, ~70% at the bottom). Neither is a reader substitute, and both judges
were built for chest reports, so on a new modality run both and see whether they
agree; read a dozen reports yourself before deciding.

**Error injection** writes the reference with 1-3 labelled errors — laterality,
severity, size, morphology, location, negation, omission, plus out-of-field,
comparison and modality-impossible statements — and records each one (category,
span, before/after) in `injected_errors`. That record is ground truth for
validating a metric; `build_pool.py` keeps it, and retrieval's donor ids, out of
the pool raters' browsers receive. Lexicons are built in for CT, chest X-ray, brain
and spine MR and surgical pathology; any other modality adds its own in a JSON file
([`reporttools/lexicons/`](reporttools/lexicons/README.md), with an unreviewed
abdominal-ultrasound draft to start from). Without one, the categories that need a
lexicon are switched off rather than borrowing another modality's list. Omission
needs an LLM for claim extraction (`ANNOTATION_LLM_URL` / `_MODEL` / `_KEY`);
`--no-llm` runs everything else offline.

**Similar-case retrieval** gives each study another patient's real report: same
clinical context, different findings (`--match-col` pins the exam type or
specimen organ; `--labels` with `--label-mode similar|divergent` steers by
diagnosis). Donors must come from outside the evaluation set, ideally the
training split.

Both strata come from reference **text**, not images, and every record says so
(`strategy`, `derived_from_reference`). Analyse them as their own stratum, never
as model output.

## Adapting it to your own data

* **Cases** — build them with `scripts/build_pool.py` (see above), or write a
  `data/cases.json` in the schema shown in `data/cases.example.json` (see the
  docstring in `app/cases_data.py`). A reference or candidate can be a single
  `text` or split into `findings` / `impression`. `links` are optional.
* **Questions** — edit `app/questions.py`. Each question is a dict (key, prompt,
  type `likert` or `binary`, per-score anchors; the anchor keys define the scale,
  so 1–3 and 1–5 mix freely). Stored values are the score or option label, so
  rewording anchors never invalidates saved ratings.
* **Modalities** — `MODALITIES` in `app/config.py`, one key per pool you merge.
* **Study size / coverage / title / layout** — `app/config.py` (`STUDIES_PER_RATER`,
  `TARGET_COVERAGE`, `APP_TITLE`, `MODALITY_LABEL`, `QUESTIONS_BESIDE_REPORTS`,
  `PANEL_HEIGHT_PX`, …).
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
  assign_systems.py      one candidate system per study, balanced (any dataset)
  generate_fallbacks.py  error injection + similar-case retrieval (any dataset)
  check_spread.py        quality spread of the model candidates: do you need fallbacks? (local GPU)
  build_pool.py          references + candidate files -> one modality's pool (any dataset)
  normalize_candidate_format.py  strip Markdown from candidate reports
  build_pool_ctrate.py   CT-RATE + Dia-LLaMA/Reg2RG/M3D/RadFM pool builder
  precompute_links.py    sentence links between reference and candidate
  add_candidate.py       add a model to an existing pool, rebalanced, rated cases untouched
  select_rexgradient_studies.py, generate_cxr_candidates.py, build_pool_rexgradient.py
                         chest X-ray arm (ReXGradient references + 4 candidate systems)
  merge_pools.py         merge per-modality pools into data/cases.json
reporttools/             claim extraction, matching, injection, retrieval (from radmetric, MIT),
                         GREEN prompt + parser (from Stanford-AIMI/GREEN, MIT)
  lexicons/              injection lexicons for new modalities (README + ultrasound draft)
docs/
  new_modality_guide.pdf how to run an arm for a new modality, step by step
data/
  cases.example.json     synthetic example pool with precomputed links (schema reference)
```

## License

MIT — see `LICENSE`. Note that the report text you load into the app carries its
own license (CT-RATE: CC BY-NC-SA 4.0, gated access).
