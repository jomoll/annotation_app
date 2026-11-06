"""Controlled error injection into reference reports.

Produces the large training set: corrupted reports whose errors are known by
construction, so the metric's stage-2 output can be scored against ground truth
without human annotation.

Design points:

* **Injection edits TEXT, not claims.** The metric consumes text (parse -> claims),
  and readers annotate text. Editing claims directly would bypass the very
  extraction step we need to test. Claims supply the token spans to edit.
* **Every injection is labelled with its category, span, and before/after value**,
  so a detected error can be checked against what was actually done.
* **Significance is controllable where it is meaningful.** A 3cm -> 3.3cm size
  change is clinically insignificant; 3cm -> 8cm is not. Same category, different
  severity — which is exactly what the significant/insignificant axis needs.
* **Category 12 (stylistic variation) is the inverse test**: it perturbs wording
  without changing meaning, and a valid metric must score it at zero penalty.

Categories follow radmetric's fixed 12-category taxonomy.

Vendored from radmetric (MIT, same author) so this repository runs on its own; see
reporttools/__init__.py for the provenance and the list of local changes. The changes that matter:

* **No silent chest-CT fallback.** Upstream, an acquisition or modality missing from a lexicon
  quietly borrowed the chest-CT list, so an ultrasound report could receive "The prostate is mildly
  enlarged" as an out-of-field error -- which on a pelvic ultrasound is in-field and correct. Here the
  category is reported inapplicable instead (returns None), exactly as when a report has no
  laterality word to flip.
* **Extensible lexicons.** `register_lexicon(path)` adds a modality without editing this file; see
  reporttools/lexicons/.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, asdict

from .claims import Claim, split_sentences

# ---------------------------------------------------------------- lexicons ---

LATERALITY = {
    "left": "right", "right": "left",
    "Left": "Right", "Right": "Left",
    "bilateral": "left", "Bilateral": "Left",
    "bilaterally": "on the left",
}

# ordered weakest -> strongest; distance in the list controls significance
SEVERITY_SCALE = ["minimal", "mild", "moderate", "marked", "severe"]
SEVERITY_ALIASES = {
    "minimally": "minimal", "mildly": "mild", "moderately": "moderate",
    "markedly": "marked", "severely": "severe", "slight": "minimal",
    "slightly": "minimal", "prominent": "marked", "extensive": "severe",
}

MORPHOLOGY_SWAP = {
    "irregular": "smooth", "smooth": "irregular",
    "spiculated": "well-defined", "well-defined": "spiculated",
    "ill-defined": "well-defined", "well defined": "ill defined",
    "hypodense": "hyperdense", "hyperdense": "hypodense",
    "hypointense": "hyperintense", "hyperintense": "hypointense",
    "homogeneous": "heterogeneous", "heterogeneous": "homogeneous",
    "solid": "cystic", "cystic": "solid",
    "round": "lobulated", "lobulated": "round",
    "diffuse": "focal", "focal": "diffuse",
    "enhancing": "non-enhancing", "non-enhancing": "enhancing",
}

# Findings the modality physically cannot show (category 9).
MODALITY_IMPOSSIBLE = {
    # MR-only physics asserted on a CT study
    "ct": [
        "There is restricted diffusion on DWI sequences.",
        "The lesion is T2 hyperintense with low T1 signal.",
        "Susceptibility artifact is seen on gradient echo sequences.",
        "The lesion shows low signal intensity on FLAIR sequences.",
        "Blooming artifact is present on susceptibility-weighted imaging.",
        "The ADC map shows corresponding low signal.",
        "There is no abnormal signal on the STIR sequence.",
        "Fat suppression demonstrates loss of signal within the lesion.",
        "Time-of-flight angiography shows normal flow-related enhancement.",
    ],
    # CT-only physics asserted on an MR study
    "mr": [
        "The lesion measures 45 Hounsfield units.",
        "Calcification density is measured at 320 HU.",
        "Attenuation values are consistent with simple fluid at 8 HU.",
        "The lesion is hypodense relative to adjacent parenchyma.",
        "Bone window images demonstrate no cortical erosion.",
        "The mass shows soft tissue attenuation of approximately 40 HU.",
        # NOT "ground glass in the lung parenchyma": on a brain MR the defect a
        # reader sees first is the anatomy, not the physics, so it was classified
        # out-of-field and scored as a miss. Every entry here must be CT physics
        # asserted on anatomy the study actually covers.
        "Beam hardening artifact obscures the adjacent structures.",
        "There is no hyperdense material within the collection.",
    ],
    # Cross-sectional physics asserted on a plain radiograph. A chest film has no
    # attenuation values, no sequences and no reformats, and "ground glass" is a
    # CT descriptor -- the radiographic equivalent is a hazy or veil-like opacity.
    "cxr": [
        "The lesion measures 45 Hounsfield units.",
        "There is restricted diffusion on DWI sequences.",
        "The lesion is T2 hyperintense with low T1 signal.",
        "Ground-glass attenuation is present in the right upper lobe.",
        "Axial images demonstrate the lesion to be centrally necrotic.",
        "Coronal reformats confirm the extent of the abnormality.",
        "The mass demonstrates homogeneous post-contrast enhancement.",
        "Attenuation values are consistent with simple fluid at 8 HU.",
    ],
    # A surgical pathology report can only assert what the ancillary studies
    # actually performed can show. Claiming an immunostain, molecular result or
    # flow cytometry that was never run is the pathology analogue of asserting MR
    # signal on a CT -- the report describes a result the workup cannot produce.
    "pathology": [
        "Immunohistochemistry for CK7 is diffusely and strongly positive.",
        "Next-generation sequencing identifies an EGFR exon 19 deletion.",
        "Flow cytometry demonstrates a clonal B-cell population.",
        "Electron microscopy shows effacement of the podocyte foot processes.",
        "Fluorescence in situ hybridisation confirms HER2 amplification.",
        "The Ki-67 proliferation index is 40% by immunostain.",
        "Direct immunofluorescence shows granular IgA deposition.",
        "Cytogenetic analysis reveals a t(11;14) translocation.",
    ],
    "noncontrast": [
        "The kidneys enhance symmetrically after contrast administration.",
        "There is avid peripheral contrast enhancement of the lesion.",
        "The mass demonstrates homogeneous post-contrast enhancement.",
        "There is peripheral rim enhancement following gadolinium.",
        "No pathological contrast enhancement is identified.",
    ],
}

# Anatomy outside the acquisition's field of view (category 10).
OUT_OF_FIELD = {
    "chest_ct": [
        "The prostate is mildly enlarged measuring 5 cm in transverse dimension.",
        "The urinary bladder is unremarkable and well distended.",
        "No free fluid is seen in the pelvis.",
        "The uterus and adnexa are within normal limits.",
    ],
    "brain_mr": [
        "The visualized lung apices are clear.",
        "The liver demonstrates normal signal characteristics.",
        "No renal calculi are identified.",
        "The visualized cervical spinal cord is normal in calibre.",
    ],
    "spine_mr": [
        "The cerebral hemispheres show no acute infarct.",
        "The lateral ventricles are of normal size.",
        "The visualized lung parenchyma is clear.",
        "The kidneys are unremarkable.",
    ],
    # A pathology report describes the specimens submitted, and nothing else.
    # Organs that would not plausibly accompany an arbitrary resection are the
    # analogue of anatomy outside an acquisition's field of view.
    "surgical_pathology": [
        "The brain parenchyma shows no histological abnormality.",
        "Bone marrow sections show normal trilineage haematopoiesis.",
        "The thyroid gland is unremarkable with normal follicular architecture.",
        "Cardiac muscle shows no evidence of myocyte hypertrophy.",
        "The ocular globe is intact with an unremarkable retina.",
    ],
    # A frontal chest film covers lung apices to costophrenic angles and the
    # upper abdomen only incidentally. Pelvis, cranium and distal limbs are not
    # on the image at all.
    "chest_xray": [
        "The prostate is mildly enlarged measuring 5 cm in transverse dimension.",
        "The urinary bladder is unremarkable and well distended.",
        "The cerebral hemispheres show no acute infarct.",
        "No free fluid is seen in the pelvis.",
        "The lumbar vertebral bodies are normal in height and alignment.",
    ],
}

# Anatomically *confusable* location pairs. An arbitrary swap ("tumoral wall" ->
# "heart") produces nonsense that no model would generate and no annotator would
# recognise as a location error; the injected error must be plausible enough to
# resemble a real model mistake. Groups are mutually swappable.
#
# Each group carries the acquisitions it is valid for; None means "anywhere".
# A word only denotes anatomy in the right context: "frontal" on a chest film is
# the projection, so swapping it for "temporal" corrupts the view line rather
# than a finding. Measured on MIMIC-CXR, "frontal" appears in 192 of 2,000
# reports and NEVER as a lobe, yet it accounted for a third of all location
# injections until this was keyed.
LOCATION_CONFUSIONS: list[tuple[list[str], set[str] | None]] = [
    (["upper lobe", "middle lobe", "lower lobe"], None),
    (["upper zone", "mid zone", "lower zone"], None),
    (["anterior segment", "posterior segment", "apical segment", "basal segment"], None),
    (["cervical", "thoracic", "lumbar"], None),
    (["liver", "spleen", "pancreas"], None),
    (["kidney", "adrenal gland"], None),
    (["mediastinum", "hilum"], None),
    (["pleura", "pericardium"], None),
    (["trachea", "bronchus", "oesophagus", "esophagus"], None),
    # Surgical margins and specimen orientation. Confusing which margin is
    # involved is a consequential, and realistic, pathology error.
    (["proximal margin", "distal margin", "radial margin"], {"surgical_pathology"}),
    (["superficial margin", "deep margin", "lateral margin"], {"surgical_pathology"}),
    (["mucosa", "submucosa", "muscularis propria", "serosa"], {"surgical_pathology"}),
    # Intracranial anatomy. On a chest study these words either do not occur or
    # mean something else entirely.
    (["frontal", "parietal", "temporal", "occipital"], {"brain_mr"}),
    (["thalamus", "basal ganglia", "internal capsule", "corpus callosum"], {"brain_mr"}),
    (["cerebellum", "brainstem", "pons", "medulla"], {"brain_mr"}),
    (["lateral ventricle", "third ventricle", "fourth ventricle"], {"brain_mr"}),
    (["subdural", "epidural", "subarachnoid"], {"brain_mr", "spine_mr"}),
]

# Comparison inserts must create a DISTINCT claim. Earlier phrasings
# ("This appearance is unchanged since the prior study") produced observations
# like `unchanged` / `stable`, which are in NORMALITY_TERMS and therefore matched
# existing normality claims instead of registering as fabrications -- the weakest
# category at 70%. These name a specific finding plus a temporal change, so the
# resulting claim has no counterpart in the reference.
#
# Keyed by acquisition, for the same reason OUT_OF_FIELD is. A flat list of chest
# findings inserted into a brain MRI is not read as a comparison error at all --
# the dominant defect becomes the anatomy, and a correct classifier reports
# out-of-field. That mislabelling cost comparison_error 81 points of recall on
# MR-RATE while CT-RATE, where the same anatomy is in-field, scored 97%.
COMPARISON_INSERTS = {
    "chest_ct": [
        "Compared to the prior examination, the pulmonary nodule has enlarged from 4 mm to 9 mm.",
        "The previously described pleural effusion has resolved in the interval.",
        "New consolidation has developed since the prior study.",
        "Interval increase in the size of the mediastinal lymphadenopathy is noted.",
        "Interval development of a new lytic osseous lesion is seen compared to the prior study.",
    ],
    "abdomen_ct": [
        "The previously noted hepatic lesion has progressed since the earlier examination.",
        "Compared to the prior examination, the renal cyst has enlarged from 12 mm to 21 mm.",
        "New free fluid has developed since the prior study.",
        "Interval increase in the size of the retroperitoneal lymphadenopathy is noted.",
        "The previously described splenic lesion has resolved in the interval.",
    ],
    "brain_mr": [
        "Compared to the prior examination, the left frontal lesion has enlarged from 4 mm to 9 mm.",
        "The previously described subdural collection has resolved in the interval.",
        "New restricted diffusion has developed since the prior study.",
        "Interval increase in the size of the right cerebellar metastasis is noted.",
        "The previously noted periventricular white matter hyperintensities have progressed "
        "since the earlier examination.",
        "Interval development of a new lacunar infarct is seen compared to the prior study.",
    ],
    "surgical_pathology": [
        "Compared with the prior biopsy, the tumour now shows a higher histologic grade.",
        "The dysplasia reported in the previous specimen is no longer identified.",
        "These findings represent progression from the earlier resection.",
        "Interval increase in tumour size is noted relative to the prior specimen.",
        "The appearances are unchanged from the previous excision.",
    ],
    "chest_xray": [
        "Compared to the prior radiograph, the right pleural effusion has increased.",
        "The previously described right upper lobe opacity has resolved in the interval.",
        "New left basilar consolidation has developed since the prior study.",
        "Interval enlargement of the cardiac silhouette is noted.",
        "The previously noted right lower lobe atelectasis has improved since the "
        "earlier examination.",
    ],
    "spine_mr": [
        "Compared to the prior examination, the L4-L5 disc protrusion has increased.",
        "The previously described epidural collection has resolved in the interval.",
        "New cord signal abnormality has developed since the prior study.",
        "Interval increase in the size of the vertebral body lesion is noted.",
        "The previously noted neural foraminal stenosis has progressed since the "
        "earlier examination.",
        "Interval development of a new compression fracture is seen compared to the prior study.",
    ],
}

NEGATION_PREFIXES = ["no", "No", "without", "absence of"]


# ------------------------------------------------------- lexicon registry ---

def known_acquisitions() -> set[str]:
    """Acquisitions with at least one acquisition-keyed lexicon."""
    keys = set(OUT_OF_FIELD) | set(COMPARISON_INSERTS) | set(Injector.BENIGN_INSERTS)
    for _, valid in LOCATION_CONFUSIONS:
        keys |= set(valid or ())
    return keys


def known_modalities() -> set[str]:
    return set(MODALITY_IMPOSSIBLE)


def lexicon_coverage(modality: str, acquisition: str) -> dict[str, bool]:
    """Which lexicon-dependent categories can fire for this modality/acquisition."""
    return {
        "modality_unobservable": bool(MODALITY_IMPOSSIBLE.get(modality)),
        "out_of_field": bool(OUT_OF_FIELD.get(acquisition)),
        "comparison_error": bool(COMPARISON_INSERTS.get(acquisition)),
        "benign_fabrication": bool(Injector.BENIGN_INSERTS.get(acquisition)),
        "acquisition_specific_locations": any(
            valid and acquisition in valid for _, valid in LOCATION_CONFUSIONS),
    }


def register_lexicon(path) -> dict:
    """Add (or override) lexicon entries from a JSON file. Returns what was registered.

    Format (every key optional; keys starting with "_" are comments):

        {
          "modality_impossible": {"<modality>":    ["sentence.", ...]},
          "out_of_field":        {"<acquisition>": ["sentence.", ...]},
          "comparison":          {"<acquisition>": ["sentence.", ...]},
          "benign_inserts":      {"<acquisition>": ["sentence.", ...]},
          "location_confusions": [{"terms": ["a", "b", ...], "acquisitions": ["<acquisition>"]}],
          "morphology_swap":     {"hypoechoic": "hyperechoic", ...}
        }

    `morphology_swap` is one flat word -> word map shared by every modality (a descriptor only
    occurs in reports of the modality that uses it); keys are single lower-case tokens.

    A location group with no "acquisitions" applies everywhere -- only do that for terms that name
    the same anatomy in every study type.
    """
    import json
    from pathlib import Path

    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    added: dict[str, list[str]] = {}
    for key, target in (("modality_impossible", MODALITY_IMPOSSIBLE),
                        ("out_of_field", OUT_OF_FIELD),
                        ("comparison", COMPARISON_INSERTS),
                        ("benign_inserts", Injector.BENIGN_INSERTS)):
        block = spec.get(key) or {}
        if not isinstance(block, dict):
            raise ValueError(f"{path}: '{key}' must map a modality/acquisition to a list of sentences")
        for name, sents in block.items():
            if name.startswith("_"):
                continue
            if not (isinstance(sents, list) and all(isinstance(x, str) and x.strip() for x in sents)):
                raise ValueError(f"{path}: '{key}.{name}' must be a list of non-empty sentences")
            target[name] = [x.strip() for x in sents]
            added.setdefault(key, []).append(name)
    for k, v in (spec.get("morphology_swap") or {}).items():
        if k.startswith("_"):
            continue
        if not (isinstance(v, str) and len(k.split()) == 1 and len(v.split()) == 1):
            raise ValueError(f"{path}: morphology_swap entries must map one word to one word: {k!r}")
        MORPHOLOGY_SWAP[k.strip().lower()] = v.strip()
        added.setdefault("morphology_swap", []).append(k)
    for g in spec.get("location_confusions") or []:
        terms = [t.strip().lower() for t in g.get("terms", []) if t.strip()]
        if len(terms) < 2:
            raise ValueError(f"{path}: a location group needs at least two terms: {g}")
        acq = g.get("acquisitions")
        LOCATION_CONFUSIONS.append((terms, set(acq) if acq else None))
        added.setdefault("location_confusions", []).append("/".join(terms))
    return added


# ------------------------------------------------------------------ record ---

@dataclass
class Injection:
    category: str                 # taxonomy id, e.g. "laterality_error"
    category_id: int
    significant: bool
    original: str = ""
    replacement: str = ""
    token_span: tuple[int, int] = (-1, -1)
    detail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["token_span"] = list(self.token_span)
        return d


@dataclass
class InjectionResult:
    text: str
    injections: list[Injection] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"text": self.text, "injections": [i.to_dict() for i in self.injections]}


# ------------------------------------------------------------------- utils ---

def _apply_edits(tokens: list[str], edits: list[tuple[int, int, list[str]]]) -> list[str]:
    """Apply (start, end_exclusive, replacement) edits right-to-left.

    NOTE the bounds condition allows `start == end == len(out)`: that is a pure
    append, which every insertion category (fabrication, modality-unobservable,
    out-of-field, comparison) relies on. An earlier `start < len(out)` guard
    silently dropped those edits, leaving the text unmodified while still
    reporting a successful injection.
    """
    out = list(tokens)
    for start, end, repl in sorted(edits, key=lambda e: -e[0]):
        if 0 <= start <= len(out) and start <= end <= len(out):
            out[start:end] = repl
    return out


def _sentence_bounds(tokens: list[str], ix: int) -> tuple[int, int]:
    # ix is clamped: RadGraph re-tokenizes its input (it splits punctuation, so a
    # 250-word report becomes ~290 tokens), which means claim start_ix/end_ix are
    # only valid against `record["text"]`. Callers must pass that text -- see
    # `aligned_text()`. The clamp keeps a mismatch from raising IndexError, but it
    # will produce a misplaced edit, so alignment is the real fix.
    ix = max(0, min(ix, len(tokens) - 1)) if tokens else 0
    start = 0
    for i in range(ix - 1, -1, -1):
        if re.search(r"[.;]$", tokens[i]):
            start = i + 1
            break
    end = len(tokens)
    for i in range(ix, len(tokens)):
        if re.search(r"[.;]$", tokens[i]):
            end = i + 1
            break
    return start, end


def aligned_text(record: dict) -> str:
    """The text that claim token indices refer to.

    ALWAYS use this as the injection basis, never the original report string:
    RadGraph re-tokenizes, so `record["text"]` and the input differ in token
    count and every claim span is expressed in the former's basis.
    """
    return record.get("text") or ""


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s-]", "", (s or "").lower()).strip()


# -------------------------------------------------------------- injectors ---


def _is_label_token(tokens: list[str], i: int, n: int) -> bool:
    """True if tokens[i:i+n] is a section label rather than a finding.

    Reports carry headings -- `Pleura:`, `MARGINS:`, `Tumor Size:` -- whose words
    are anatomy words sitting outside any claim. Swapping one corrupts a heading
    and leaves the finding untouched, so the corpus asserts a location error the
    text does not contain. On the restructured MIMIC-CXR mirror that was 83% of
    all location injections and dragged the category to 17% recall.

    A label is a token run terminated by a colon, so that is what we test.
    """
    end = i + n - 1
    if end >= len(tokens):
        return False
    return tokens[end].endswith(":") or (
        end + 1 < len(tokens) and tokens[end + 1] == ":")


class Injector:
    """Applies one or more labelled errors to a report."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    # -- 1. omission ---------------------------------------------------------
    def omit_finding(self, tokens: list[str], claims: list[Claim],
                     significant: bool = True) -> tuple[list[str], Injection] | None:
        """Drop a claim's sentence.

        Significance is a real distinction here, not a label: omitting a positive
        finding can change management, whereas omitting a routine normality
        statement ("the trachea is patent") usually cannot. Targeting normality
        claims is therefore how an *insignificant* omission is produced.
        """
        pool = [c for c in claims
                if c.is_normality != significant and c.start_ix >= 0]
        if not pool:                       # fall back to any claim
            pool = [c for c in claims if c.start_ix >= 0]
        if not pool:
            return None
        c = self.rng.choice(pool)
        s, e = _sentence_bounds(tokens, c.start_ix)
        removed = " ".join(tokens[s:e])
        return _apply_edits(tokens, [(s, e, [])]), Injection(
            category="omission_of_finding", category_id=1,
            significant=not c.is_normality,
            original=removed, replacement="", token_span=(s, e),
            detail=f"removed sentence containing "
                   f"{'normality' if c.is_normality else 'finding'} '{c.observation}'")

    # -- 2. fabrication ------------------------------------------------------
    # Benign normality statements, for an *insignificant* fabrication: asserting
    # an unremarkable structure the reference never mentions is a fabrication, but
    # rarely one that changes management -- unlike inventing pathology.
    # Keyed by acquisition: a benign statement about anatomy the study does not
    # cover is an out-of-field error, not an insignificant fabrication. The chest
    # list applied to brain MR produced 18 of 35 spurious out-of-field reports.
    BENIGN_INSERTS = {
        "chest_ct": [
            "The visualized thyroid gland is unremarkable.",
            "The trachea is patent.",
            "The visualized bony structures are within normal limits.",
            "Soft tissues of the chest wall are unremarkable.",
            "The esophagus is unremarkable in the visualized portion.",
        ],
        "abdomen_ct": [
            "The visualized bowel loops are unremarkable.",
            "No free intraperitoneal air is identified.",
            "The abdominal wall soft tissues are unremarkable.",
            "The visualized bony structures are within normal limits.",
        ],
        "brain_mr": [
            "The visualized paranasal sinuses are clear.",
            "The orbits are unremarkable.",
            "The mastoid air cells are well aerated.",
            "The scalp soft tissues are unremarkable.",
            "The visualized skull base is intact.",
        ],
        "surgical_pathology": [
            "The background parenchyma is histologically unremarkable.",
            "No significant acute inflammation is identified.",
            "The uninvolved mucosa shows no diagnostic abnormality.",
            "Sections show no additional pathology.",
        ],
        "chest_xray": [
            "The visualized bony structures are within normal limits.",
            "The trachea is midline.",
            "Soft tissues of the chest wall are unremarkable.",
            "The visualized upper abdomen is unremarkable.",
        ],
        "spine_mr": [
            "The paraspinal soft tissues are unremarkable.",
            "The visualized vertebral body heights are maintained.",
            "The posterior elements are intact.",
            "No paraspinal collection is identified.",
        ],
    }

    def fabricate_finding(self, tokens: list[str], donor_sentence: str,
                          significant: bool = True,
                          acquisition: str = "chest_ct") -> tuple[list[str], Injection]:
        pool = self.BENIGN_INSERTS.get(acquisition)
        if not significant and not pool:
            return None                    # no benign statements known for this acquisition
        sent = donor_sentence if significant else self.rng.choice(pool)
        pos = len(tokens)
        return _apply_edits(tokens, [(pos, pos, sent.split())]), Injection(
            category="fabrication_of_finding", category_id=2, significant=significant,
            original="", replacement=sent, token_span=(pos, pos),
            detail=("inserted a finding from a different report" if significant
                    else "inserted a benign normality statement absent from the reference"))

    # -- 3. negation ---------------------------------------------------------
    def flip_negation(self, tokens: list[str], claims: list[Claim]) -> tuple[list[str], Injection] | None:
        for i, t in enumerate(tokens):
            if _norm(t) in ("no", "without"):
                nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
                # A sentence-initial "No" removed leaves the sentence starting in lower case
                # ("calculus is identified"), which no generator writes and which marks the
                # candidate as injected. Carry the capital over to the next word.
                if t[:1].isupper() and nxt[:1].islower():
                    return _apply_edits(tokens, [(i, i + 2, [nxt[:1].upper() + nxt[1:]])]), Injection(
                        category="negation_error", category_id=3, significant=True,
                        original=t, replacement="", token_span=(i, i + 1),
                        detail=f"removed negation before '{nxt}' (absent -> present)")
                return _apply_edits(tokens, [(i, i + 1, [])]), Injection(
                    category="negation_error", category_id=3, significant=True,
                    original=t, replacement="", token_span=(i, i + 1),
                    detail=f"removed negation before '{nxt}' (absent -> present)")
        cands = [c for c in claims if c.status == "present" and not c.is_normality and c.start_ix >= 0]
        if not cands:
            return None
        c = self.rng.choice(cands)
        return _apply_edits(tokens, [(c.start_ix, c.start_ix, ["no"])]), Injection(
            category="negation_error", category_id=3, significant=True,
            original=c.observation, replacement=f"no {c.observation}",
            token_span=(c.start_ix, c.start_ix),
            detail="inserted negation (present -> absent)")

    # -- 4. laterality -------------------------------------------------------
    def swap_laterality(self, tokens: list[str]) -> tuple[list[str], Injection] | None:
        idxs = [i for i, t in enumerate(tokens) if _norm(t) in
                {k.lower() for k in LATERALITY}]
        if not idxs:
            return None
        i = self.rng.choice(idxs)
        orig = tokens[i]
        key = _norm(orig)
        repl = LATERALITY.get(orig) or LATERALITY.get(key) or LATERALITY.get(key.capitalize())
        if not repl:
            return None
        return _apply_edits(tokens, [(i, i + 1, [repl])]), Injection(
            category="laterality_error", category_id=4, significant=True,
            original=orig, replacement=repl, token_span=(i, i + 1),
            detail="laterality swapped")

    # -- 5. location ---------------------------------------------------------
    def swap_location(self, tokens: list[str], claims: list[Claim],
                      acquisition: str = "chest_ct") -> tuple[list[str], Injection] | None:
        """Swap an anatomical site for a *confusable* alternative.

        Restricted to LOCATION_CONFUSIONS groups: an arbitrary swap produces
        nonsense that resembles no real model error, so it would train the metric
        on an unrealistic distribution. Groups are filtered by acquisition for the
        same reason -- a word that names anatomy in one study names something else
        in another.
        """
        lowered = [_norm(t) for t in tokens]
        joined = " ".join(lowered)
        options: list[tuple[int, int, str, str]] = []   # start, n, original, replacement
        for group, valid_for in LOCATION_CONFUSIONS:
            if valid_for is not None and acquisition not in valid_for:
                continue
            for phrase in group:
                p = phrase.split()
                n = len(p)
                if phrase not in joined:
                    continue
                for i in range(len(lowered) - n + 1):
                    if lowered[i:i + n] != p:
                        continue
                    if _is_label_token(tokens, i, n):
                        continue          # a heading, not a finding
                    alts = [g for g in group if g != phrase]
                    if alts:
                        options.append((i, n, phrase, self.rng.choice(alts)))
        if not options:
            return None
        i, n, orig, repl = self.rng.choice(options)
        return _apply_edits(tokens, [(i, i + n, repl.split())]), Injection(
            category="location_error", category_id=5, significant=True,
            original=orig, replacement=repl, token_span=(i, i + n),
            detail=f"anatomy '{orig}' -> '{repl}' (confusable site)")

    # -- 6. severity ---------------------------------------------------------
    def shift_severity(self, tokens: list[str], significant: bool = True
                       ) -> tuple[list[str], Injection] | None:
        for i, t in enumerate(tokens):
            base = SEVERITY_ALIASES.get(_norm(t), _norm(t))
            if base in SEVERITY_SCALE:
                pos = SEVERITY_SCALE.index(base)
                step = 3 if significant else 1
                new = SEVERITY_SCALE[min(len(SEVERITY_SCALE) - 1, pos + step)] \
                    if pos < len(SEVERITY_SCALE) // 2 else \
                    SEVERITY_SCALE[max(0, pos - step)]
                if new == base:
                    continue
                return _apply_edits(tokens, [(i, i + 1, [new])]), Injection(
                    category="severity_error", category_id=6, significant=significant,
                    original=t, replacement=new, token_span=(i, i + 1),
                    detail=f"severity {base} -> {new} ({'major' if significant else 'minor'} shift)")
        return None

    # -- 7. size / measurement ----------------------------------------------
    def perturb_measurement(self, tokens: list[str], significant: bool = True
                            ) -> tuple[list[str], Injection] | None:
        num = re.compile(r"^(\d+(?:[.,]\d+)?)$")
        for i, t in enumerate(tokens):
            m = num.match(t)
            if not m:
                continue
            unit = tokens[i + 1] if i + 1 < len(tokens) else ""
            if _norm(unit) not in ("mm", "cm"):
                continue
            val = float(m.group(1).replace(",", "."))
            new = round(val * (2.5 if significant else 1.1), 1)
            new_s = str(int(new)) if new == int(new) else str(new)
            return _apply_edits(tokens, [(i, i + 1, [new_s])]), Injection(
                category="size_measurement_error", category_id=7, significant=significant,
                original=f"{t} {unit}", replacement=f"{new_s} {unit}", token_span=(i, i + 1),
                detail=f"measurement {val} -> {new} {unit}")
        return None

    # -- 8. morphology -------------------------------------------------------
    def swap_morphology(self, tokens: list[str]) -> tuple[list[str], Injection] | None:
        for i, t in enumerate(tokens):
            k = _norm(t)
            if k in MORPHOLOGY_SWAP:
                return _apply_edits(tokens, [(i, i + 1, [MORPHOLOGY_SWAP[k]])]), Injection(
                    category="morphology_error", category_id=8, significant=True,
                    original=t, replacement=MORPHOLOGY_SWAP[k], token_span=(i, i + 1),
                    detail=f"morphology '{k}' -> '{MORPHOLOGY_SWAP[k]}'")
        return None

    # -- 9. modality-unobservable -------------------------------------------
    def modality_unobservable(self, tokens: list[str], modality: str) -> tuple[list[str], Injection]:
        pool = MODALITY_IMPOSSIBLE.get(modality)
        if not pool:
            return None                    # modality has no lexicon: inapplicable, not chest CT
        s = self.rng.choice(pool)
        pos = len(tokens)
        return _apply_edits(tokens, [(pos, pos, s.split())]), Injection(
            category="modality_unobservable", category_id=9, significant=True,
            original="", replacement=s, token_span=(pos, pos),
            detail=f"claim not observable on {modality}")

    # -- 10. out-of-field ----------------------------------------------------
    def out_of_field(self, tokens: list[str], acquisition: str) -> tuple[list[str], Injection]:
        pool = OUT_OF_FIELD.get(acquisition)
        if not pool:
            return None                    # acquisition has no lexicon: inapplicable, not chest CT
        s = self.rng.choice(pool)
        pos = len(tokens)
        return _apply_edits(tokens, [(pos, pos, s.split())]), Injection(
            category="out_of_field", category_id=10, significant=True,
            original="", replacement=s, token_span=(pos, pos),
            detail=f"anatomy outside {acquisition} field of view")

    # -- 11. comparison ------------------------------------------------------
    def fabricate_comparison(self, tokens: list[str],
                             acquisition: str = "chest_ct") -> tuple[list[str], Injection]:
        pool = COMPARISON_INSERTS.get(acquisition)
        if not pool:
            return None                    # acquisition has no lexicon: inapplicable, not chest CT
        s = self.rng.choice(pool)
        pos = len(tokens)
        return _apply_edits(tokens, [(pos, pos, s.split())]), Injection(
            category="comparison_error", category_id=11, significant=False,
            original="", replacement=s, token_span=(pos, pos),
            detail="comparison to a prior study that the reference does not mention")

    # -- 12. stylistic variation (INVERSE TEST — must score zero) ------------
    def stylistic_variation(self, tokens: list[str]) -> tuple[list[str], Injection]:
        text = " ".join(tokens)
        # canonical splitter -- must match the parser's, or a reorder can move
        # text across a boundary the parser does not see, perturbing the parse
        # during what is meant to be a meaning-preserving inverse test
        sents = split_sentences(text, min_tokens=1)
        if len(sents) > 2:
            i = self.rng.randrange(len(sents) - 1)
            sents[i], sents[i + 1] = sents[i + 1], sents[i]
            new = " ".join(sents)
            detail = "adjacent sentences reordered"
        else:
            new = text.replace(" is seen", " is noted").replace(" was detected", " was identified")
            detail = "meaning-preserving paraphrase"
        return new.split(), Injection(
            category="stylistic_variation", category_id=12, significant=False,
            original="(whole report)", replacement="(reordered/paraphrased)",
            token_span=(0, len(tokens)), detail=detail + " — metric must score ZERO penalty")


# --------------------------------------------------------------- dispatcher ---

MECHANICAL = [
    "negation_error", "laterality_error", "location_error", "severity_error",
    "size_measurement_error", "morphology_error", "omission_of_finding",
]
INSERTIONS = [
    "modality_unobservable", "out_of_field", "comparison_error",
]


def inject(text: str, claims: list[Claim], category: str, *,
           seed: int | None = None, significant: bool = True,
           modality: str = "ct", acquisition: str = "chest_ct",
           donor_sentence: str = "") -> InjectionResult | None:
    """Apply one labelled error of `category` to `text`. None if inapplicable."""
    inj = Injector(seed)
    tokens = text.split()

    fn = {
        "omission_of_finding": lambda: inj.omit_finding(tokens, claims),
        "fabrication_of_finding": lambda: inj.fabricate_finding(
            tokens, donor_sentence, acquisition=acquisition) if donor_sentence else None,
        "negation_error": lambda: inj.flip_negation(tokens, claims),
        "laterality_error": lambda: inj.swap_laterality(tokens),
        "location_error": lambda: inj.swap_location(tokens, claims, acquisition),
        "severity_error": lambda: inj.shift_severity(tokens, significant),
        "size_measurement_error": lambda: inj.perturb_measurement(tokens, significant),
        "morphology_error": lambda: inj.swap_morphology(tokens),
        "modality_unobservable": lambda: inj.modality_unobservable(tokens, modality),
        "out_of_field": lambda: inj.out_of_field(tokens, acquisition),
        "comparison_error": lambda: inj.fabricate_comparison(tokens, acquisition),
        "stylistic_variation": lambda: inj.stylistic_variation(tokens),
    }.get(category)

    if fn is None:
        raise ValueError(f"unknown category: {category}")
    res = fn()
    if res is None:
        return None
    new_tokens, injection = res
    return InjectionResult(" ".join(new_tokens), [injection])


ALL_CATEGORIES = MECHANICAL + INSERTIONS + ["fabrication_of_finding", "stylistic_variation"]


# Normality cues, for picking donor sentences that assert an actual abnormality.
# Vocabulary is shared with claims.NORMALITY_TERMS so the two stay in step -- a
# separate list drifted immediately (it lacked "open", so "Trachea and both main
# bronchi are open" was selected as an abnormal finding).
from .claims import NORMALITY_TERMS as _NORM_TERMS  # noqa: E402

_NEG_CUE = r"\bno\b|\bnot\b|\bwithout\b|\bnone\b|\bnegative for\b|\bfree of\b"
_NORMAL_CUE = re.compile(
    "|".join([rf"\b{re.escape(t)}\b" for t in sorted(_NORM_TERMS)] +
             [_NEG_CUE]), re.I)

# Temporal cues. A donor carrying these is not just a finding -- it invokes a
# prior study, which is category 11, and inserting it produces a comparison error
# labelled as a fabrication. The classifier then names it correctly and is scored
# wrong twice, costing comparison precision on one side and fabrication recall on
# the other. Chest radiographs reference priors constantly, which is why this cost
# 54 points of comparison precision there and far less on CT.
_TEMPORAL_CUE = re.compile(
    r"\b(compared|comparison|prior|previous|interval|since|unchanged|stable|"
    r"resolved|improved|worsened|progress\w*|persistent|again|re-?demonstrat\w*|"
    r"earlier|increas\w*|decreas\w*|now|new)\b", re.I)


def build_donor_pool(reports: list[str], min_words: int = 6,
                     max_words: int = 30) -> list[str]:
    """Sentences asserting a positive abnormal finding, for fabrication.

    Fabrication must insert a *finding*, and nothing else. Two kinds of sentence
    are excluded for the same reason. A normality claim ("Trachea and both main
    bronchi are open.") is a weaker error than fabricated pathology. A sentence
    invoking a prior study ("Small effusions appear unchanged.") is a comparison
    error, category 11, and inserting it as a fabrication mislabels the example.

    Build once and reuse. The pool does not depend on the report being corrupted,
    so rebuilding it per report costs a full pass over every donor and made corpus
    generation quadratic: 2000 reports against 2000 donors is four million
    sentence splits for a list that never changes.
    """
    pool: list[str] = []
    for rep in reports:
        for s in split_sentences(rep, min_tokens=1):
            s = s.strip()
            # count content words only -- punctuation-split fragments such as
            # "As far as can be seen ; ." otherwise pass a raw token minimum
            w = [t for t in s.split() if re.search(r"[A-Za-z]{2,}", t)]
            if not (min_words <= len(w) <= max_words):
                continue
            if _NORMAL_CUE.search(s) or _TEMPORAL_CUE.search(s):
                continue
            pool.append(s if s.endswith(".") else s + " .")
    return pool


def pick_donor_sentence(pool: list[str], rng: random.Random | None = None) -> str:
    """One donor sentence from a pool built by `build_donor_pool`."""
    if not pool:
        return ""
    return (rng or random.Random()).choice(pool)
