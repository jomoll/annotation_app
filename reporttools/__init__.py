"""Text-derived fallback candidates: controlled error injection and similar-case retrieval.

A reader study needs candidates that span the whole quality range. Image models often leave one
end empty -- nothing near-perfect, or a floor made only of degenerate output. These two strategies
fill the gap from the REFERENCE TEXT, so they must always be labelled and analysed as their own
stratum, never as model output (every record carries `strategy` and `derived_from_reference`).

  injection   The reference with 1-3 labelled errors (laterality, severity, size, morphology,
              location, negation, omission, and a few appended-sentence categories). Fluent and
              nearly correct: the TOP of the range. Every error is recorded with category, span and
              before/after value, so the stratum doubles as ground truth for a metric.
  retrieval   Another study's real report, chosen to share this patient's clinical context but to
              differ in findings. Fluent, correctly styled, about the wrong patient: a principled
              near-miss or floor, wrong the way a real report is wrong rather than by being empty.

Run it through scripts/generate_fallbacks.py; docs/new_modality_guide.pdf (step 4) says when to.

Provenance
----------
claims.py, injection.py, extract.py and llm.py are vendored from radmetric
(https://github.com/jomoll/radmetric, MIT, same author) at commit 1e39b4b, so this repository
needs no radmetric install. Local changes:

* injection.py -- an acquisition/modality without a lexicon makes the lexicon-dependent categories
  inapplicable instead of silently borrowing chest CT's; `register_lexicon()` adds modalities from
  JSON (see reporttools/lexicons/); `lexicon_coverage()` reports what can fire.
* llm.py -- configured by ANNOTATION_LLM_* variables, no default model, cache opened lazily inside
  data/, null `content` from reasoning models handled.
* extract.py -- the claim-extraction token budget is read from ANNOTATION_LLM_MAX_TOKENS.
* claims.py, matching.py -- unchanged apart from a provenance line.

retrieval.py and layout.py come from this repository's brain-MRI arm, generalised.
"""
