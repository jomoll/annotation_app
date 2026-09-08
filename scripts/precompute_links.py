#!/usr/bin/env python3
"""Precompute sentence links between reference and candidate for the hover highlighting.

Uses radmetric's claim extraction + matching (https://github.com/jomoll/radmetric):
every sentence of both reports is turned into clinical claims by an LLM, claims are
paired one-to-one across the reports (Hungarian assignment on observation identity),
and each matched claim pair becomes a link between the two sentences it came from.
Only matches are stored — nothing about error categories — so the app can show
"this sentence corresponds to that one" without leaking the metric's verdicts.

Writes, per case:
    "links": [{"ref": {"section": "findings", "start": 120, "end": 188},
               "cand": {"section": "text", "start": 0, "end": 63},
               "score": 0.95, "claims": 2}, ...]        # char offsets within the section text
and, in the pool meta, "links_meta" with the model and settings used.

Requires a running OpenAI-compatible endpoint (see radmetric's scripts/serve.sh):
    export RADMETRIC_LLM_URL=http://localhost:8000/v1   RADMETRIC_LLM_MODEL=gemma-4-31b
    PYTHONPATH=/path/to/radmetric/src python scripts/precompute_links.py data/cases.json

--no-think adds vLLM's chat_template_kwargs {"enable_thinking": false}, needed for
reasoning models (Qwen 3.x) that would otherwise spend the token budget thinking.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from radmetric import llm
    from radmetric.claims import split_sentences
    from radmetric.extract import LLMReportParser
    from radmetric.matching import Matcher
except ImportError as e:  # pragma: no cover
    sys.exit(f"radmetric not importable ({e}) — set PYTHONPATH to radmetric/src and pip install requests scipy numpy")

REF_SECTIONS = ["findings", "impression"]


def patch_no_think() -> None:
    """Route radmetric's chat through a request that disables the model's thinking phase."""
    _orig_key = llm._key

    def chat(prompt, *, system="", max_tokens=256, model=llm.MODEL, use_cache=True, timeout=180):
        k = _orig_key(prompt, system, model + "#nothink", max_tokens)
        if use_cache:
            hit = llm._cache.get(k)
            if hit is not None:
                return hit
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        payload = {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0,
                   "chat_template_kwargs": {"enable_thinking": False}}
        last = None
        for attempt in range(3):
            try:
                r = requests.post(f"{llm.BASE_URL}/chat/completions",
                                  headers={"Authorization": f"Bearer {llm.API_KEY}", "Content-Type": "application/json"},
                                  json=payload, timeout=timeout)
                r.raise_for_status()
                out = (r.json()["choices"][0]["message"]["content"] or "").strip()
                if use_cache:
                    llm._cache.put(k, prompt, out, model)
                return out
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after retries: {last}")

    llm.chat = chat


def sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """(sentence, char_start, char_end) for every sentence radmetric would extract from `text`."""
    out, cursor = [], 0
    for sent in split_sentences(text):
        i = text.find(sent, cursor)
        if i < 0:
            i = text.find(sent)
            if i < 0:
                continue
        out.append((sent, i, i + len(sent)))
        cursor = i + len(sent)
    return out


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def _navigable(a, b) -> bool:
    """The matcher pairs on observation identity and treats anatomy only as a tie-breaker, which
    is right for scoring but links "X is normal" to any other "Y is normal" when nothing better
    is around. For navigation such a pair is noise: normality claims are kept only when the two
    sides name overlapping anatomy. Abnormal findings are linked as matched."""
    if not (a.is_normality or b.is_normality):
        return True
    A = {_norm(x) for x in a.anatomy if _norm(x)}
    B = {_norm(x) for x in b.anatomy if _norm(x)}
    if not A or not B:
        return False
    return any(x in y or y in x or set(x.split()) & set(y.split()) for x in A for y in B)


def links_for(case: dict, parser: LLMReportParser, matcher: Matcher, min_score: float = 0.0) -> list[dict]:
    ref = case["reference"]
    cand = case["candidate"]
    ref_texts = [(s, ref.get(s) or "") for s in REF_SECTIONS if (ref.get(s) or "").strip()]
    cand_texts = [("text", cand.get("text") or "")] if (cand.get("text") or "").strip() else \
                 [(s, cand.get(s) or "") for s in REF_SECTIONS if (cand.get(s) or "").strip()]
    parsed = parser.parse_many([t for _, t in ref_texts] + [t for _, t in cand_texts])
    ref_parsed, cand_parsed = parsed[: len(ref_texts)], parsed[len(ref_texts):]

    def collect(section_texts, parsed_list):
        claims, where = [], {}
        for (section, text), (cl, _aligned) in zip(section_texts, parsed_list):
            spans = sentence_spans(text)
            by_sentence = {s: (section, a, b) for s, a, b in spans}
            for c in cl:
                loc = by_sentence.get(c.sentence)
                if loc is None:
                    continue
                claims.append(c)
                where[id(c)] = loc
        return claims, where

    ref_claims, ref_where = collect(ref_texts, ref_parsed)
    cand_claims, cand_where = collect(cand_texts, cand_parsed)
    pairs = matcher.match(ref_claims, cand_claims)

    agg: dict[tuple, dict] = {}
    for p in pairs:
        if p.kind != "matched":
            continue
        if p.score < min_score or not _navigable(p.reference, p.candidate):
            continue
        r, c = ref_where[id(p.reference)], cand_where[id(p.candidate)]
        key = (r, c)
        entry = agg.setdefault(key, {"ref": {"section": r[0], "start": r[1], "end": r[2]},
                                     "cand": {"section": c[0], "start": c[1], "end": c[2]},
                                     "score": 0.0, "claims": 0})
        entry["score"] = max(entry["score"], round(float(p.score), 3))
        entry["claims"] += 1
    return sorted(agg.values(), key=lambda e: (e["ref"]["section"], e["ref"]["start"], e["cand"]["start"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", type=Path, help="cases JSON to annotate in place (e.g. data/cases.json)")
    ap.add_argument("--out", type=Path, default=None, help="write here instead of in place")
    ap.add_argument("--no-think", action="store_true", help="disable the model's thinking phase (Qwen 3.x)")
    ap.add_argument("--force", action="store_true", help="recompute cases that already have links")
    ap.add_argument("--min-score", type=float, default=0.85,
                    help="drop matched pairs below this matcher score (its own threshold, 0.62, is tuned for "
                         "scoring; lexical tail matches like 'thickening'~'thickening' are noise for navigation)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if args.no_think:
        patch_no_think()
    h = llm.health()
    print(f"endpoint {h['url']} model {h['model']} ok={h['ok']} ({h['latency']:.2f}s): {h['response'][:60]!r}")

    data = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    todo = [c for c in cases if args.force or "links" not in c]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} of {len(cases)} cases to process")

    parser, matcher = LLMReportParser(), Matcher()
    t0 = time.time()
    n_links = 0
    for i, c in enumerate(todo, 1):
        c["links"] = links_for(c, parser, matcher, args.min_score)
        n_links += len(c["links"])
        if i % 10 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} · {n_links} links · {time.time() - t0:.0f}s", flush=True)
            out = args.out or args.cases
            out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    if isinstance(data, dict):
        data.setdefault("meta", {})["links_meta"] = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "method": "radmetric claim extraction + Hungarian matching, aggregated to sentence pairs",
            "llm_model": h["model"], "llm_url": h["url"], "no_think": args.no_think,
            "match_threshold": matcher.threshold, "min_score": args.min_score,
        }
    out = args.out or args.cases
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} · {sum(len(c.get('links', [])) for c in cases)} links total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
