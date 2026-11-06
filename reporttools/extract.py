# Vendored from radmetric (https://github.com/jomoll/radmetric, MIT); see reporttools/__init__.py.
"""LLM claim extraction, as a drop-in alternative to RadGraph.

Three independent measurements point at extraction as the binding constraint:

  * re-parsing identical text changes ~1% of claims (GPU/batching non-determinism)
  * benign paraphrase changes WHICH claims are extracted (18 -> 17, different
    observations), giving a 100% false-positive rate and ~3 phantom errors/report
  * spurious omission/fabrication counts are identical across every classifier
    arm -- ~85 of ~93 spurious errors are claims that never paired

So the question is whether a different extractor is more stable. Same schema, same
matcher, same classifier downstream -- only extraction differs.

Sentence-level, matching `pipeline.ReportParser`, so order-invariance is preserved
by construction and the comparison is like-for-like.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .claims import Claim, NORMALITY_TERMS, split_sentences
from . import llm

SYSTEM = (
    "You extract structured clinical claims from single sentences of radiology "
    "reports. You are consistent: the same clinical content always yields the same "
    "claims, regardless of how it is worded. You answer only with JSON."
)

PROMPT = """Extract every distinct clinical claim from this radiology sentence.

SENTENCE: "{sent}"

A claim is one finding or one assertion of normality, with its attributes.

Rules:
- observation: the finding itself, as a short canonical noun phrase. Use the
  STANDARD term, not the sentence's wording: "unremarkable"/"within normal
  limits"/"natural" all become "normal"; "frosted glass density" becomes "ground
  glass". This canonicalisation is important - the same finding must always
  produce the same observation string.
- status: "present", "absent" (explicitly negated) or "uncertain" (hedged).
- anatomy: anatomical site(s), lower case, no laterality words.
- modifiers: laterality (left/right/bilateral), severity (mild/moderate/severe),
  and descriptors (irregular/smooth/hypodense...).
- measurements: sizes as written, e.g. "3 cm", "22 mm".
- Do NOT emit a claim for a measurement or a modifier on its own; attach them.

Answer with JSON only:
{{"claims": [{{"observation": "...", "status": "present|absent|uncertain",
"anatomy": [...], "modifiers": [...], "measurements": [...]}}]}}
Use {{"claims": []}} for a sentence with no clinical content."""


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s-]", " ", (s or "").lower()).strip()


def _parse(raw: str, sentence: str, offset: int) -> list[Claim]:
    if not raw:
        return []
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []

    out: list[Claim] = []
    toks = sentence.split()
    for c in (data.get("claims") or []):
        obs = str(c.get("observation", "")).strip()
        if not obs:
            continue
        status = str(c.get("status", "present")).strip().lower()
        if status not in ("present", "absent", "uncertain"):
            status = "present"

        def _list(key):
            v = c.get(key) or []
            if isinstance(v, str):
                v = [v]
            return [str(x).strip() for x in v if str(x).strip()]

        # locate the observation in the sentence for an evidence span; fall back
        # to the sentence start so ordering stays stable
        start = offset
        low = [_norm(t) for t in toks]
        head = _norm(obs).split()
        if head:
            for i in range(len(low)):
                if low[i:i + len(head)] == head:
                    start = offset + i
                    break

        out.append(Claim(
            observation=obs,
            status=status,
            anatomy=_list("anatomy"),
            modifiers=_list("modifiers"),
            measurements=_list("measurements"),
            is_normality=_norm(obs) in NORMALITY_TERMS,
            entity_id="",
            start_ix=start,
            end_ix=start,
            sentence=sentence,
        ))
    return out


@dataclass
class LLMReportParser:
    """Same interface as pipeline.ReportParser, LLM extraction instead of RadGraph."""
    # 700 suffices for a non-reasoning model. A reasoning model spends the budget thinking first and
    # returns no answer when it runs out (finish_reason=length): measured on glm-5.3-flash, 5 of 28
    # sentences at 700 -- and a gateway may ignore requests to disable thinking. Raise it there.
    max_tokens: int = int(os.environ.get("ANNOTATION_LLM_MAX_TOKENS", "700"))

    def parse(self, text: str) -> tuple[list[Claim], str]:
        sents = split_sentences(text)
        if not sents:
            return [], ""
        raws = llm.chat_many([PROMPT.format(sent=s) for s in sents],
                             system=SYSTEM, max_tokens=self.max_tokens)
        claims: list[Claim] = []
        offset = 0
        for sent, raw in zip(sents, raws):
            claims.extend(_parse(raw, sent, offset))
            offset += len(sent.split())
        claims.sort(key=lambda c: c.start_ix)
        # aligned text is just the sentences rejoined: LLM extraction does not
        # retokenize, so spans index the input directly
        return claims, " ".join(sents)

    def parse_many(self, texts: list[str]) -> list[tuple[list[Claim], str]]:
        """Parse several reports, batching sentences ACROSS them.

        `parse()` batches only the ~12-17 sentences of a single report, so it can
        never use more than that many concurrent slots however many workers are
        configured, and every report waits on its slowest sentence. Flattening all
        sentences into one batch keeps the pool saturated -- roughly a 2x speedup
        at 32 workers on corpus-scale runs.
        """
        per_report = [split_sentences(t) for t in texts]
        flat: list[str] = []
        spans: list[tuple[int, int]] = []
        for sents in per_report:
            spans.append((len(flat), len(flat) + len(sents)))
            flat.extend(sents)

        if not flat:
            return [([], "") for _ in texts]

        raws = llm.chat_many([PROMPT.format(sent=s) for s in flat],
                             system=SYSTEM, max_tokens=self.max_tokens)

        out: list[tuple[list[Claim], str]] = []
        for (start, end), sents in zip(spans, per_report):
            claims: list[Claim] = []
            offset = 0
            for sent, raw in zip(sents, raws[start:end]):
                claims.extend(_parse(raw, sent, offset))
                offset += len(sent.split())
            claims.sort(key=lambda c: c.start_ix)
            out.append((claims, " ".join(sents)))
        return out
