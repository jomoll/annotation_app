# Injection lexicons

Most injected errors are **substitutions** that need no lexicon: laterality, severity, size,
morphology and negation work on any report written in English, and omission and negation only need
claim extraction. Three **appended-sentence** categories do need one, because what counts as an
error depends on the study:

| category | lexicon key | keyed by | a valid entry is... |
|---|---|---|---|
| `modality_unobservable` | `modality_impossible` | modality | a claim this modality **physically cannot make** (Hounsfield units on MR, T2 signal on CT, an immunostain that was never run on a pathology case) |
| `out_of_field` | `out_of_field` | acquisition | a statement about anatomy or tissue **outside what the study covers** |
| `comparison_error` | `comparison` | acquisition | a **specific finding plus a temporal change** against a prior the reference never mentions; generic "unchanged since prior" reads as a normality claim, not a comparison error |
| `morphology_error` | `morphology_swap` | (flat, all modalities) | one-word descriptor swaps your modality uses (echogenicity on ultrasound: `hypoechoic` → `hyperechoic`) |
| (insignificant fabrication) | `benign_inserts` | acquisition | a normal statement about **in-field** anatomy the reference does not mention |
| `location_error` | `location_confusions` | acquisition (optional) | groups of **confusable** sites; an arbitrary swap ("liver" → "heart") is nonsense no model writes |

Built in: modalities `ct`, `cxr`, `mr`, `pathology`; acquisitions `chest_ct`, `abdomen_ct`,
`chest_xray`, `brain_mr`, `spine_mr`, `surgical_pathology`. An acquisition or modality **without** a
lexicon makes its categories inapplicable. It never borrows another modality's list: a chest-CT
"out-of-field" sentence about the prostate is *in* field on a pelvic ultrasound.

Add a modality without touching code:

```bash
python scripts/generate_fallbacks.py coverage --modality us --acquisition abdomen_us \
    --lexicon reporttools/lexicons/abdomen_us.draft.json          # check what can fire
python scripts/generate_fallbacks.py inject ... --modality us --acquisition abdomen_us \
    --lexicon my_lexicon.json
```

Format (every key optional; keys starting with `_` are comments):

```json
{
  "modality_impossible": {"us": ["The lesion measures 45 Hounsfield units."]},
  "out_of_field":        {"abdomen_us": ["The cerebral hemispheres show no acute infarct."]},
  "comparison":          {"abdomen_us": ["New ascites has developed since the prior study."]},
  "benign_inserts":      {"abdomen_us": ["The spleen is normal in size."]},
  "location_confusions": [{"terms": ["upper pole", "lower pole"], "acquisitions": ["abdomen_us"]}]
}
```

Write entries in the house style of your references: same spelling, same sentence shape. An
injected sentence that reads differently from the rest of the report is identifiable without
being read, which defeats its purpose. Have a clinician from the modality review every list before
the first run, and inspect a sample of the output: `injected_errors` records exactly what was done.

`abdomen_us.draft.json` is a **starting point for abdominal ultrasound that has not been clinically
reviewed**. Copy it, have it reviewed, and adapt it to your exam types: out-of-field depends on the
examination, so thyroid, obstetric or vascular ultrasound each need their own acquisition key.
