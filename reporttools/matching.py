# Vendored from radmetric (https://github.com/jomoll/radmetric, MIT); see reporttools/__init__.py.
"""Stage 1b: match claim pairs between a reference and a candidate report.

The taxonomy dictates the matching design. Categories 3-8 (negation, laterality,
location, severity, size, morphology) are all *attribute* errors, which only exist
if the two claims are paired in the first place. So:

    MATCH ON OBSERVATION IDENTITY, THEN DIFF ATTRIBUTES.

Matching on (observation, anatomy) would be wrong: "effusion@left" vs
"effusion@right" would fail to pair and be scored as one omission plus one
fabrication, when it is a single laterality error. Anatomy therefore contributes
only a small disambiguation bonus -- enough to pair the right two claims when a
report mentions the same finding in several places -- never a hard key. Status is
excluded from matching entirely, since a present/absent flip is error #3.

Unmatched claims are the two highest-value categories and fall straight out:
    reference claim with no partner  -> #1 omission
    candidate claim with no partner  -> #2 fabrication

Similarity is a cascade, most auditable first:
    1. exact      normalized string equality                       -> 1.00
    2. alias      radiologist-editable synonym dictionary          -> 0.95
    3. lexical    token-overlap / sequence ratio                   -> graded
    4. (hook)     embedding or LLM backend for the residual tail

Assignment is globally optimal (Hungarian), not greedy, so pairing does not depend
on claim order in the report.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .claims import Claim

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")

# Match acceptance threshold. Below this, claims are left unpaired (omission /
# fabrication) rather than forced into a low-confidence pair -- a wrong pairing
# invents an attribute error, which is worse than reporting the omission.
DEFAULT_THRESHOLD = 0.62

# Weight of anatomy agreement in the pairing score. Deliberately small: it only
# disambiguates between same-observation candidates, and must never prevent a
# location/laterality error from pairing.
ANATOMY_BONUS = 0.20

# Below this length, character-level SequenceMatcher similarity is not trusted on
# its own (see the lexical tier). Short clinical terms share letters by accident:
# "normal"/"unremarkable" scores 0.74 with zero shared tokens.
MIN_CHARS_FOR_SEQ = 12

# Seed synonym groups observed in CT-RATE / MR-RATE. Radiologist-editable, same
# spirit as radlabels' alias dictionary. Groups are symmetric sets.
# Synonym groups. LOAD-BEARING: 7 of 12 genuine radiological synonym pairs match
# ONLY via this tier -- "pleural effusion"/"hydrothorax", "atelectasis"/"collapse",
# "steatosis"/"fatty liver" all score 0.00 without it, each producing a phantom
# omission plus a phantom fabrication. Radiology carries Greek/Latin and plain
# English registers for the same finding, so token overlap is a poor proxy for
# synonymy. (An ablation initially showed no effect, but its test substitutions were
# all lexically similar and never exercised this tier.)
#
# CONSERVATIVE SET: only equivalence that loses no clinical information. Groups that
# merged a qualifier away were removed, because a wrong merge HIDES a real error --
# the same failure that got the destructive canonicaliser rejected. Specifically
# dropped:
#   "acute infarct" -> "infarct"          acuity is clinically load-bearing
#   "ischemic lesion" -> "infarct"        lesion is not yet an infarct
#   "encephalomalacia" -> "gliosis"       related but distinct findings
#   "preserved"/"natural" -> "normal"     "preserved" qualifies a structure
#   "clear" -> "patent"                   different assertions
#   "hematoma"/"bleed" -> "hemorrhage"    a hematoma is a collection, not the act
#   "opacity" group                       opacity/density-increase are not equivalent
#   "nodular opacity"/"nodular lesion"    specificity differs from "nodule"
SYNONYM_GROUPS: list[set[str]] = [
    {"ground glass", "ground-glass", "frosted glass", "frosted glass-like density",
     "ground glass opacity", "ground-glass opacity", "frosted glass like density",
     "ground glass density", "ground glass attenuation"},
    {"effusion", "pleural effusion", "fluid in the pleural space", "hydrothorax",
     "pleural fluid"},
    {"nodule", "nodular density", "nodular image", "nodular images"},
    {"emphysema", "emphysematous change", "emphysematous changes"},
    {"atelectasis", "collapse", "band atelectasis", "subsegmental atelectasis",
     "linear atelectasis"},
    {"lymphadenopathy", "enlarged lymph node", "enlarged lymph nodes",
     "lymph node enlargement", "lymphadenopathies"},
    {"cardiomegaly", "enlarged heart", "increased heart size", "heart size increased",
     "cardiac enlargement"},
    {"bronchiectasis", "traction bronchiectasis"},
    {"fibrosis", "fibrotic change", "fibrotic changes", "fibrotic sequela",
     "fibrotic sequelae"},
    {"steatosis", "hepatosteatosis", "fatty liver", "hepatic steatosis"},
    {"hiatal hernia", "sliding hiatal hernia", "sliding type hiatal hernia"},
    {"infarct", "infarction"},
    {"hemorrhage", "haemorrhage"},
    {"edema", "oedema"},
    {"dilatation", "dilation"},
    {"atrophy", "volume loss", "parenchymal atrophy"},
    {"gliosis", "gliotic change", "gliotic changes", "gliotic sequelae"},
    {"stenosis", "narrowing", "narrowed", "narrows", "luminal narrowing"},
    {"disc herniation", "herniation of nucleus pulposus", "disc protrusion"},
    {"normal", "unremarkable", "within normal limits", "normal limits"},
    {"patent", "open", "widely open", "unobstructed"},
    {"calcification", "calcifications"},
    {"metastasis", "metastases"},
]


def _norm(s: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", (s or "").lower())).strip()


def _build_synonym_index(groups: Iterable[set[str]]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, g in enumerate(groups):
        for p in g:
            idx[_norm(p)] = i
    return idx


@dataclass
class Pair:
    """One matched or unmatched claim pair."""
    reference: Claim | None
    candidate: Claim | None
    score: float = 0.0
    method: str = ""          # exact | alias | lexical | none

    @property
    def kind(self) -> str:
        if self.reference is not None and self.candidate is not None:
            return "matched"
        return "omission" if self.candidate is None else "fabrication"


@dataclass
class Matcher:
    threshold: float = DEFAULT_THRESHOLD
    anatomy_bonus: float = ANATOMY_BONUS
    synonym_groups: list[set[str]] = field(default_factory=lambda: SYNONYM_GROUPS)
    # Optional backend for the residual tail: fn(str, str) -> float in [0,1].
    # Slot an embedding model or LLM in here; keep it last so the auditable
    # tiers resolve the majority of pairs.
    extra_scorer: Callable[[str, str], float] | None = None

    def __post_init__(self):
        self._syn = _build_synonym_index(self.synonym_groups)

    # ---- observation similarity cascade -------------------------------------

    def observation_similarity(self, a: str, b: str) -> tuple[float, str]:
        na, nb = _norm(a), _norm(b)
        if not na or not nb:
            return 0.0, "none"
        if na == nb:
            return 1.0, "exact"

        ga, gb = self._syn.get(na), self._syn.get(nb)
        if ga is not None and ga == gb:
            return 0.95, "alias"

        # containment: "lymph nodes" vs "Multiple lymph nodes"
        if na in nb or nb in na:
            shorter, longer = sorted((na, nb), key=len)
            ratio = len(shorter) / max(1, len(longer))
            return 0.80 + 0.15 * ratio, "lexical"

        ta, tb = set(na.split()), set(nb.split())
        if ta and tb:
            jac = len(ta & tb) / len(ta | tb)
            # Character-level similarity is unreliable on short strings: "normal"
            # vs "unremarkable" scores 0.74 despite sharing no token, purely from
            # incidental letter runs. A false match INVENTS an attribute error, so
            # the character signal is only trusted when there is also some token
            # overlap, or when both strings are long enough for the ratio to mean
            # something. True token-disjoint synonyms belong in SYNONYM_GROUPS.
            seq = difflib.SequenceMatcher(None, na, nb).ratio()
            shortest = min(len(na), len(nb))
            seq_trusted = seq if (jac > 0 or shortest >= MIN_CHARS_FOR_SEQ) else 0.0
            lex = max(jac, seq_trusted)
            if lex > 0.55:
                return 0.55 + 0.35 * lex, "lexical"

        if self.extra_scorer is not None:
            s = float(self.extra_scorer(a, b))
            if s > 0:
                return s, "extra"
        return 0.0, "none"

    # ---- anatomy agreement (disambiguation only) ----------------------------

    def anatomy_agreement(self, a: Claim, b: Claim) -> float:
        sa = {_norm(x) for x in a.anatomy if _norm(x)}
        sb = {_norm(x) for x in b.anatomy if _norm(x)}
        if not sa or not sb:
            return 0.0          # ~half of claims lack anatomy; absence is not evidence
        inter = len(sa & sb)
        if inter:
            return inter / max(len(sa), len(sb))
        # partial string overlap ("upper lobe" vs "lobe")
        for x in sa:
            for y in sb:
                if x in y or y in x:
                    return 0.5
        return 0.0

    # ---- pair scoring --------------------------------------------------------

    def pair_score(self, ref: Claim, cand: Claim) -> tuple[float, str]:
        obs, method = self.observation_similarity(ref.observation, cand.observation)
        if obs <= 0:
            return 0.0, "none"
        # Normality and abnormality claims should not pair with each other: a
        # "normal" statement matching a pathological finding would be scored as an
        # attribute error rather than the omission/fabrication pair it really is.
        if ref.is_normality != cand.is_normality and method != "exact":
            obs *= 0.5
        score = obs + self.anatomy_bonus * self.anatomy_agreement(ref, cand)
        return min(score, 1.0 + self.anatomy_bonus), method

    # ---- assignment ----------------------------------------------------------

    def match(self, reference: list[Claim], candidate: list[Claim]) -> list[Pair]:
        """Globally optimal pairing. Returns matched pairs plus every unmatched claim."""
        from scipy.optimize import linear_sum_assignment
        import numpy as np

        if not reference and not candidate:
            return []
        if not reference:
            return [Pair(None, c) for c in candidate]
        if not candidate:
            return [Pair(r, None) for r in reference]

        n, m = len(reference), len(candidate)
        scores = np.zeros((n, m))
        methods: dict[tuple[int, int], str] = {}
        for i, r in enumerate(reference):
            for j, c in enumerate(candidate):
                s, meth = self.pair_score(r, c)
                scores[i, j] = s
                methods[(i, j)] = meth

        rows, cols = linear_sum_assignment(-scores)

        pairs: list[Pair] = []
        used_r: set[int] = set()
        used_c: set[int] = set()
        for i, j in zip(rows, cols):
            if scores[i, j] >= self.threshold:
                pairs.append(Pair(reference[i], candidate[j],
                                  float(scores[i, j]), methods[(i, j)]))
                used_r.add(i)
                used_c.add(j)
        for i, r in enumerate(reference):
            if i not in used_r:
                pairs.append(Pair(r, None))
        for j, c in enumerate(candidate):
            if j not in used_c:
                pairs.append(Pair(None, c))
        return pairs


def summarize(pairs: list[Pair]) -> dict:
    from collections import Counter
    kinds = Counter(p.kind for p in pairs)
    meth = Counter(p.method for p in pairs if p.kind == "matched")
    matched = [p for p in pairs if p.kind == "matched"]
    return {
        "matched": kinds["matched"],
        "omission": kinds["omission"],
        "fabrication": kinds["fabrication"],
        "by_method": dict(meth),
        "mean_score": round(sum(p.score for p in matched) / len(matched), 3) if matched else 0.0,
    }
