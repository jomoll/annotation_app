# Vendored from radmetric (https://github.com/jomoll/radmetric, MIT); see reporttools/__init__.py.
"""Stage 1: turn a RadGraph annotation into claim tuples.

A *claim* is the atomic unit the metric compares between reference and
prediction. Anchored on an Observation node:

    Claim(
      observation = "effusion",
      status      = "present" | "absent" | "uncertain",
      anatomy     = ["pericardial"],          # via located_at
      modifiers   = ["left", "mild"],         # via incoming modify edges
      measurements= ["43 mm"],                # Observation::measurement neighbours
      is_normality= False,                    # "normal", "patent", "unremarkable", ...
      evidence    = (start_ix, end_ix, text)  # auditable span
    )

Design decisions this encodes (all measured on CT-RATE / MR-RATE, 2026-08-25):

* **Uncertainty is a claim attribute**, not a separate error dimension. Genuine
  hedging affects well under 1% of claims in these corpora, so a second scoring
  axis would rarely fire and would add annotator burden. `status` carries it,
  symmetric with present/absent. Revisit only if moving to an English-native
  corpus, where hedging is far more common.
* **Normality is extracted like any other claim.** How normal-vs-abnormal matches
  are *weighted* is a stage-3 question. But `is_normality` is flagged here because
  "normal" is the single most frequent observation in CT, MR-brain and MR-spine
  alike -- and it is exactly what mode-collapsed models emit. Stage 3 must not
  reward a constant "all normal" template, which is precisely how BLEU/ROUGE
  were shown to rank a fixed string above published SOTA.
* **Built on the graph, not flattened labels.** radlabels-style alias matching
  discards laterality and severity; the graph keeps them, so claims are formed
  from entities and edges directly. Alias dictionaries remain useful for
  *grouping* claims into named findings at a coarser view.
* `located_at` attaches to only ~43-52% of observations, so `anatomy` is often
  empty. Downstream matching needs a fallback (sentence proximity) rather than
  treating an unattached observation as location-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# Observations that assert normality/absence-of-disease rather than a finding.
# Frequency-driven: these dominate the top-observation lists in all three subsets.
NORMALITY_TERMS = {
    "normal", "normals", "natural", "patent", "open", "preserved", "unremarkable",
    "intact", "regular", "clear", "free", "stable", "symmetric", "symmetrical",
    "normal width", "normal widths", "within normal limits", "no abnormality",
    "specific findings", "physiologic", "physiological",
}

_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")

# THE canonical sentence splitter. Injection, parsing, matching and scoring must
# all agree on what a sentence is: injection previously split on "." while the
# parser split on "." and ";", so a reordering injection could rearrange text
# across a boundary the parser did not recognise -- silently perturbing the parse
# during what is supposed to be a meaning-preserving inverse test.
SENT_SPLIT = re.compile(r"(?<=[.;])\s+")
MIN_SENT_TOKENS = 2


def split_sentences(text: str, min_tokens: int = MIN_SENT_TOKENS) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text or "")
            if len(s.split()) >= min_tokens]


def _norm(s: str) -> str:
    return _PUNCT.sub("", s.strip().lower())


@dataclass
class Claim:
    observation: str
    status: str                       # present | absent | uncertain
    anatomy: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    measurements: list[str] = field(default_factory=list)
    is_normality: bool = False
    is_measurement: bool = False
    entity_id: str = ""
    start_ix: int = -1
    end_ix: int = -1
    sentence: str = ""

    def key(self) -> tuple:
        """Coarse identity for matching: observation + sorted anatomy."""
        return (_norm(self.observation), tuple(sorted(_norm(a) for a in self.anatomy)))

    def to_dict(self) -> dict:
        return asdict(self)


def _status_of(label: str) -> str:
    tail = label.split("::")[-1]
    return {"definitely present": "present",
            "definitely absent": "absent",
            "uncertain": "uncertain"}.get(tail, tail)


def _sentence_for(tokens: list[str], ix: int) -> str:
    start = 0
    for i in range(ix, -1, -1):
        if i < len(tokens) and re.search(r"[.;:]$", tokens[i]) and i != ix:
            start = i + 1
            break
    end = len(tokens)
    for i in range(ix, len(tokens)):
        if re.search(r"[.;:]$", tokens[i]):
            end = i + 1
            break
    return " ".join(tokens[start:end])


def extract_claims(record: dict, include_normality: bool = True) -> list[Claim]:
    """Extract claims from one RadGraph annotation record.

    Apply `hedge_rules.apply_hedge_rules(record)` BEFORE this if you want the
    corpus-specific "evaluated in favor of" hedges recovered.
    """
    ents = record.get("entities") or {}
    tokens = (record.get("text") or "").split()

    # incoming edges: {target_id: [(relation, source_id)]}
    incoming: dict[str, list[tuple[str, str]]] = {}
    for sid, e in ents.items():
        for rel, tgt in e.get("relations", []) or []:
            incoming.setdefault(str(tgt), []).append((rel, sid))

    # An Observation that only serves to qualify ANOTHER Observation is a
    # modifier, not a claim of its own -- e.g. "physiologic cystic" modifying
    # "enlargement". Emitting both double-counts the same span and creates a
    # spurious match target. Likewise a measurement ("43 mm") is an attribute of
    # the finding it sizes, not an independent claim.
    subordinate: set[str] = set()
    for sid, e in ents.items():
        if not e.get("label", "").startswith("Observation"):
            continue
        for rel, tgt in e.get("relations", []) or []:
            t = ents.get(str(tgt))
            if not t or not t.get("label", "").startswith("Observation"):
                continue
            if rel in ("modify", "located_at"):
                subordinate.add(sid)

    claims: list[Claim] = []
    for eid, e in ents.items():
        label = e.get("label", "")
        if not label.startswith("Observation"):
            continue

        obs = e["tokens"]
        is_meas = "measurement" in label
        is_norm = _norm(obs) in NORMALITY_TERMS

        # measurements and pure modifiers are attributes, surfaced on the parent
        if is_meas or eid in subordinate:
            continue
        if is_norm and not include_normality:
            continue

        anatomy, measurements = [], []
        for rel, tgt in e.get("relations", []) or []:
            t = ents.get(str(tgt))
            if not t:
                continue
            if rel == "located_at" and t["label"].startswith("Anatomy"):
                anatomy.append(t["tokens"])
            elif rel == "located_at":
                anatomy.append(t["tokens"])

        modifiers = []
        for rel, sid in incoming.get(eid, []):
            s = ents.get(sid)
            if not s:
                continue
            if rel == "modify":
                if "measurement" in s["label"]:
                    measurements.append(s["tokens"])
                else:
                    modifiers.append(s["tokens"])
            elif rel == "located_at" and "measurement" in s["label"]:
                measurements.append(s["tokens"])

        claims.append(Claim(
            observation=obs,
            status=_status_of(label),
            anatomy=anatomy,
            modifiers=modifiers,
            measurements=measurements,
            is_normality=is_norm,
            is_measurement=is_meas,
            entity_id=eid,
            start_ix=e.get("start_ix", -1),
            end_ix=e.get("end_ix", -1),
            sentence=_sentence_for(tokens, e.get("start_ix", 0)) if tokens else "",
        ))

    claims.sort(key=lambda c: c.start_ix)

    # Measurements are frequently attached to an Anatomy node ("43 mm"
    # --located_at--> "diameter") rather than to the finding they size, so the
    # graph alone does not connect them to a claim. Fall back to proximity:
    # give each measurement to the nearest claim in the same sentence. Same
    # fallback principle needed for anatomy, which `located_at` supplies for
    # only ~43-52% of observations.
    if claims:
        for eid, e in ents.items():
            if "measurement" not in e.get("label", ""):
                continue
            m_ix = e.get("start_ix", -1)
            if m_ix < 0:
                continue
            m_sent = _sentence_for(tokens, m_ix) if tokens else ""
            same_sent = [c for c in claims if c.sentence and c.sentence == m_sent]
            pool = same_sent or claims
            nearest = min(pool, key=lambda c: abs(c.start_ix - m_ix))
            if e["tokens"] not in nearest.measurements:
                nearest.measurements.append(e["tokens"])

    return claims


def summarize(claims: list[Claim]) -> dict:
    from collections import Counter
    return {
        "n_claims": len(claims),
        "status": dict(Counter(c.status for c in claims)),
        "normality": sum(c.is_normality for c in claims),
        "abnormality": sum(not c.is_normality for c in claims),
        "with_anatomy": sum(bool(c.anatomy) for c in claims),
        "with_modifiers": sum(bool(c.modifiers) for c in claims),
        "with_measurements": sum(bool(c.measurements) for c in claims),
    }
