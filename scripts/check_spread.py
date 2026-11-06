#!/usr/bin/env python3
"""Check the quality spread of your model-generated candidates -- on one local GPU, no server.

Run this BEFORE deciding on fallbacks (step 4 of docs/new_modality_guide.pdf). It scores every
candidate against its reference with two independent metrics and shows how the scores are spread:
if your models already cover the range from poor to near-perfect, you need no fallbacks; if a band
is empty, `scripts/generate_fallbacks.py` can fill it.

    # same inputs as scripts/build_pool.py
    python scripts/check_spread.py --references refs.csv --studies eval_ids.txt \\
        --candidates modelA=out/modelA.jsonl modelB=out/modelB.jsonl \\
        --assignment data/raw/us/assignment.csv --out data/raw/us/spread.csv

    # or a pool file you already built
    python scripts/check_spread.py --pool data/cases_us.json --out data/raw/us/spread.csv

Metrics -- both run in-process with `transformers`; nothing is sent anywhere:

  green      The GREEN error-counting prompt (Ostmeier et al. 2024), answered by a local model:
             matched findings / (matched + clinically significant errors). Default model: the
             published judge StanfordAIMI/GREEN-RadLlama2-7b (fp16, ~14 GB of GPU memory). Any
             instruction-tuned model can stand in via --green-model, e.g.
             Qwen/Qwen3-4B-Instruct-2507 (~9 GB). See reporttools/green.py.
  bertscore  BERTScore F1 (roberta-large, rescaled with the package baseline): how close the
             wording is. No clinical judgement, but cheap, deterministic and independent of GREEN.

Where the two metrics disagree about which candidates are good, trust neither alone and read a
sample of reports yourself.

Output: one CSV row per candidate (study_id, system, green, green_matched, green_sig_errors,
bertscore), the raw judge answers next to it (<out>.green.jsonl, reused on a re-run), and a summary
printed to the terminal: the distribution per system and pooled, the empty bands, and the
agreement between the metrics.

Requirements: pip install -r requirements-tools.txt plus torch, transformers, accelerate,
bert-score (see requirements-spread.txt). The models download from the Hugging Face Hub on first
use; set HF_HOME to choose where.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from reporttools import green as green_mod  # noqa: E402
from reporttools.reports import load_reports, reference_text  # noqa: E402

DEFAULT_GREEN = "StanfordAIMI/GREEN-RadLlama2-7b"
BANDS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]
BAND_NAMES = ["0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1"]

# The chat format the GREEN fine-tunes were trained with (green_score/green.py).
GREEN_TEMPLATE = (
    "{% for message in messages %}\n{% if message['from'] == 'human' %}\n"
    "{{ '<|user|>\n' + message['value'] + eos_token }}\n{% elif message['from'] == 'system' %}\n"
    "{{ '<|system|>\n' + message['value'] + eos_token }}\n{% elif message['from'] == 'gpt' %}\n"
    "{{ '<|assistant|>\n'  + message['value'] + eos_token }}\n{% endif %}\n"
    "{% if loop.last and add_generation_prompt %}\n{{ '<|assistant|>' }}\n{% endif %}\n{% endfor %}")


# ----------------------------------------------------------------------------- inputs

def pairs_from_pool(path: Path) -> list[dict]:
    pool = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for c in pool["cases"]:
        ref = c["reference"]
        cand = c["candidate"]
        out.append({"case_id": c["id"], "study_id": c["study_id"], "system": c["candidate_id"],
                    "reference": reference_text(ref), "candidate": cand.get("text") or reference_text(cand)})
    return out


def pairs_from_files(args) -> list[dict]:
    from build_pool import load_candidates   # same candidate format, same answer-key stripping
    refs = {r["study_id"]: r for r in load_reports(args.references, args.id_col)}
    cands = {}
    for spec in args.candidates:
        name, _, p = spec.partition("=")
        if not name or not p:
            sys.exit(f"--candidates expects NAME=PATH, got {spec!r}")
        cands[name] = load_candidates(Path(p))
    if args.assignment:
        a = pd.read_csv(args.assignment, dtype=str).fillna("")
        a.columns = [c.lower() for c in a.columns]
        idc = next(c for c in ("study_id", "study_uid", "id") if c in a.columns)
        plan = [(r[idc].strip(), r["system"].strip()) for r in a.to_dict("records")]
    else:
        plan = [(sid, n) for n, d in cands.items() for sid in d]
    if args.studies:
        keep = {l.strip() for l in args.studies.read_text().splitlines() if l.strip() and not l.startswith("#")}
        plan = [(s, n) for s, n in plan if s in keep]
    out, missing = [], 0
    for sid, name in plan:
        r, c = refs.get(sid), cands.get(name, {}).get(sid)
        if r is None or c is None or not reference_text(r):
            missing += 1
            continue
        out.append({"case_id": f"{sid}__{name}", "study_id": sid, "system": name,
                    "reference": reference_text(r), "candidate": c["text"]})
    if missing:
        print(f"skipped {missing} planned candidates with no reference or no usable text")
    return out


# ----------------------------------------------------------------------------- metrics

def run_green(pairs: list[dict], model_id: str, cache: Path, batch: int, max_new: int, device: str) -> None:
    done = {}
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            # an answer without a verdict (usually cut off by the token budget) is scored again
            if r.get("model") == model_id and green_mod.score(r.get("response"))["green"] is not None:
                done[r["case_id"]] = r["response"]
    todo = [p for p in pairs if p["case_id"] not in done]
    print(f"GREEN with {model_id}: {len(pairs) - len(todo)} cached, {len(todo)} to score")
    if todo:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        is_green = "green" in model_id.lower()
        tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=is_green, padding_side="left",
                                            **({"add_eos_token": True, "use_fast": True} if is_green else {}))
        if is_green or not tok.chat_template:
            tok.chat_template = GREEN_TEMPLATE
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        dtype = torch.float16 if is_green else "auto"
        model = AutoModelForCausalLM.from_pretrained(model_id, trust_remote_code=is_green,
                                                     dtype=dtype).to(device).eval()

        def render(prompt: str) -> str:
            if tok.chat_template == GREEN_TEMPLATE:
                msgs = [{"from": "human", "value": prompt}, {"from": "gpt", "value": ""}]
                return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            return tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False,
                                           add_generation_prompt=True, enable_thinking=False)

        # longest first, so a batch that does not fit fails at once, not after an hour
        todo.sort(key=lambda p: -len(p["reference"]) - len(p["candidate"]))
        with cache.open("a", encoding="utf-8") as fh, torch.inference_mode():
            for i in range(0, len(todo), batch):
                chunk = todo[i:i + batch]
                texts = [render(green_mod.make_prompt(p["reference"], p["candidate"])) for p in chunk]
                enc = tok(texts, return_tensors="pt", padding=True, add_special_tokens=False).to(device)
                gen = model.generate(**enc, max_new_tokens=max_new, do_sample=False, temperature=None,
                                     top_p=None, top_k=None, pad_token_id=tok.pad_token_id)
                outs = tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
                for p, o in zip(chunk, outs):
                    done[p["case_id"]] = o
                    fh.write(json.dumps({"case_id": p["case_id"], "model": model_id, "response": o}) + "\n")
                fh.flush()
                print(f"  {min(i + batch, len(todo))}/{len(todo)}", flush=True)
        del model
    for p in pairs:
        s = green_mod.score(done.get(p["case_id"], ""))
        p["green"], p["green_matched"], p["green_sig_errors"] = s["green"], s["matched"], s["significant_errors"]


def run_bertscore(pairs: list[dict], device: str, batch: int, model: str) -> None:
    """roberta-large, layer 17, rescaled -- bert-score's English default. `model` may be a local copy
    of roberta-large for machines that cannot reach the Hub; the layer and baseline stay those of
    roberta-large, so only a copy of that model is valid here."""
    import bert_score
    from bert_score import score
    bs_score = sys.modules["bert_score.score"]   # the module; bert_score.score is the function
    baseline = Path(bert_score.__file__).parent / "rescale_baseline" / "en" / "roberta-large.tsv"
    get_tok = bs_score.get_tokenizer

    def capped(*a, **k):                 # a local copy has no length limit; roberta's is 512
        t = get_tok(*a, **k)
        t.model_max_length = min(t.model_max_length, 512)
        return t
    bs_score.get_tokenizer = capped
    print(f"BERTScore on {len(pairs)} candidates")
    _, _, f1 = score([p["candidate"] for p in pairs], [p["reference"] for p in pairs], model_type=model,
                     num_layers=17, lang="en", rescale_with_baseline=True, baseline_path=str(baseline),
                     device=device, batch_size=batch, verbose=False)
    for p, v in zip(pairs, f1.tolist()):
        p["bertscore"] = v


# ----------------------------------------------------------------------------- summary

def describe(v: pd.Series) -> str:
    v = v.dropna()
    if v.empty:
        return "no scores"
    shares = np.histogram(v.clip(0, 1), bins=BANDS)[0] / len(v)
    q1, med, q3 = v.quantile([0.25, 0.5, 0.75])
    bands = "  ".join(f"{s:4.0%}" for s in shares)
    return f"n={len(v):4d}  median {med:5.2f}  IQR {q1:4.2f}-{q3:4.2f}  | {bands}"


# Our three arms, model-generated candidates only (no fallbacks), 200 studies each; validated against
# reader ratings on 2026-11-06. CT and X-ray needed no fallbacks; brain MRI did.
#   (median, share in the top band 0.8-1, share in the bottom band 0-0.2)
REFERENCE = {
    "green:GREEN-RadLlama2-7b": {"chest CT": (0.38, 0.09, 0.14), "chest X-ray": (0.40, 0.12, 0.10),
                                 "brain MRI": (0.07, 0.00, 0.67)},
    "green:Qwen3-4B-Instruct-2507": {"chest CT": (0.50, 0.11, 0.07), "chest X-ray": (0.38, 0.11, 0.13),
                                     "brain MRI": (0.00, 0.02, 0.72)},
}
# BERTScore (rescaled) lives on a compressed scale -- even good candidates rarely pass 0.6 -- so its
# absolute bands mean nothing; compare medians with these instead.
BERT_REFERENCE = {"chest CT": 0.22, "chest X-ray": 0.23, "brain MRI": 0.02}


def summary(df: pd.DataFrame, metrics: list[str], green_model: str) -> str:
    from scipy.stats import spearmanr
    head = f"{'':22s}{'':47s}| {'  '.join(f'{b:>4s}' for b in ['0-.2', '.2-.4', '.4-.6', '.6-.8', '.8-1'])}"
    L = []
    for m in metrics:
        L += ["", f"== {m}" + (f" ({green_model})" if m == "green" else " (rescaled roberta-large F1)"), head]
        for sysname, g in df.groupby("system"):
            L.append(f"  {sysname[:20]:20s}{describe(g[m])}")
        L.append(f"  {'ALL':20s}{describe(df[m])}")
        v = df[m].dropna().clip(0, 1)
        if m == "green" and len(v):
            shares = np.histogram(v, bins=BANDS)[0] / len(v)
            empty = [n for n, s in zip(BAND_NAMES, shares) if s < 0.03]
            L.append(f"  empty or near-empty bands (<3% of candidates): {', '.join(empty) or 'none'}")
            bad = df["green"].isna().sum()
            if bad:
                L.append(f"  {bad} answers had no parsable verdict (see the .green.jsonl file); they are "
                         "left out. Many? raise --max-new-tokens.")
            ref = next((r for k, r in REFERENCE.items() if k.split(":", 1)[1].lower() in green_model.lower()), None)
            L.append("  for comparison, our arms with model candidates only (median / top band / bottom band):")
            for arm, (med, top, bot) in (ref or REFERENCE["green:GREEN-RadLlama2-7b"]).items():
                L.append(f"    {arm:12s} {med:.2f} / {top:3.0%} / {bot:3.0%}")
            if ref is None:
                L.append("    (measured with GREEN-RadLlama2-7b; another judge shifts the scale, so compare "
                         "the shape, not the numbers)")
        if m == "bertscore" and len(v):
            L.append("  bands do not apply: rescaled BERTScore rarely exceeds 0.6 even for good candidates.")
            L.append("  for comparison, median with model candidates only: " +
                     ", ".join(f"{a} {x:.2f}" for a, x in BERT_REFERENCE.items()))
    if len(metrics) == 2:
        d = df.dropna(subset=metrics)
        if len(d) > 10:
            r = spearmanr(d[metrics[0]], d[metrics[1]]).statistic
            L += ["", f"agreement between the metrics: Spearman {r:+.2f} over {len(d)} candidates "
                      "(our arms: +0.45 to +0.83)"]
            med = d.groupby("system")[metrics].median()
            if len(med) > 2:
                rs = spearmanr(med[metrics[0]], med[metrics[1]]).statistic
                L.append(f"  system ranking by median: Spearman {rs:+.2f} over {len(med)} systems")
            if r < 0.3:
                L.append("  LOW: the metrics disagree on which candidates are good -- read a sample of "
                         "reports before trusting either distribution.")
    L += ["",
          "How to read this (step 4 of docs/new_modality_guide.pdf): what matters is whether the "
          "candidates cover the range, not the mean. If no GREEN band is empty and some candidates "
          "reach the top band (CT and X-ray: 9-12%), the models alone are the arm -- no fallbacks. "
          "If the top is empty or the bottom holds most candidates (brain MRI: 0-2% top, ~70% bottom), "
          "error injection (`generate_fallbacks.py inject`) fills the top; retrieval with "
          "--label-mode similar the middle. Add the smallest number that fills the gap. Both metrics "
          "are coarse (Spearman with readers 0.2-0.8 depending on modality), so they decide the "
          "spread, not individual cases -- and read a dozen reports yourself before deciding."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pool", type=Path, help="a pool built by scripts/build_pool.py")
    src.add_argument("--references", nargs="+", type=Path, help="reference reports (as for build_pool.py)")
    ap.add_argument("--candidates", nargs="+", metavar="NAME=PATH")
    ap.add_argument("--assignment", type=Path)
    ap.add_argument("--studies", type=Path)
    ap.add_argument("--id-col")
    ap.add_argument("--out", type=Path, required=True, help="per-candidate CSV")
    ap.add_argument("--metrics", nargs="+", choices=["green", "bertscore"], default=["green", "bertscore"])
    ap.add_argument("--green-model", default=DEFAULT_GREEN,
                    help="Hugging Face id or local path of the GREEN judge (default: the published one)")
    ap.add_argument("--bertscore-model", default="roberta-large",
                    help="roberta-large, or a local copy of it (offline machines)")
    ap.add_argument("--device", default="cuda", help="e.g. cuda, cuda:1, cpu (cpu is very slow for GREEN)")
    ap.add_argument("--batch", type=int, default=8, help="GREEN batch size; lower it if memory runs out")
    ap.add_argument("--max-new-tokens", type=int, default=2048,
                    help="answer budget; general models write longer explanations than the GREEN "
                         "fine-tunes, and an answer cut off before its verdict cannot be scored")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N candidates (a dry run)")
    args = ap.parse_args(argv)
    if args.references and not args.candidates:
        ap.error("--references needs --candidates")

    pairs = pairs_from_pool(args.pool) if args.pool else pairs_from_files(args)
    pairs = [p for p in pairs if p["reference"] and p["candidate"]]
    if args.limit:
        pairs = pairs[:args.limit]
    if not pairs:
        print("ERROR: no reference/candidate pairs to score")
        return 1
    print(f"{len(pairs)} candidates over {len({p['study_id'] for p in pairs})} studies, "
          f"{len({p['system'] for p in pairs})} systems")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if "green" in args.metrics:
        run_green(pairs, args.green_model, args.out.with_suffix(".green.jsonl"), args.batch,
                  args.max_new_tokens, args.device)
    if "bertscore" in args.metrics:
        run_bertscore(pairs, args.device, max(args.batch, 32), args.bertscore_model)

    cols = ["case_id", "study_id", "system"] + [c for c in ("green", "green_matched", "green_sig_errors",
                                                             "bertscore") if c in pairs[0]]
    df = pd.DataFrame(pairs)[cols]
    df.to_csv(args.out, index=False)
    text = summary(df, args.metrics, args.green_model)
    print(text)
    args.out.with_suffix(".summary.txt").write_text(text + "\n", encoding="utf-8")
    print(f"\nwrote {args.out} and {args.out.with_suffix('.summary.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
