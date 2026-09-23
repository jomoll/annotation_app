#!/usr/bin/env python3
"""Generate candidate chest X-ray reports for the selected ReXGradient studies with one open model.

One environment per model (their transformers pins conflict), all offline once the weights are cached:
    export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
    /data2/moll/envs/chexagent/bin/python scripts/generate_cxr_candidates.py views      # once: frontal/lateral per image
    /data2/moll/envs/chexagent/bin/python scripts/generate_cxr_candidates.py chexagent
    /data2/moll/envs/cxrgen/bin/python    scripts/generate_cxr_candidates.py maira2
    /data2/moll/envs/llavarad/bin/python  scripts/generate_cxr_candidates.py llavarad

Reads data/raw/rexgradient/selected_studies.csv (from select_rexgradient_studies.py) and the images under
/data2/moll/datasets/rexgradient/deid_png. Writes data/raw/rexgradient/candidates/<model>.jsonl, one line
per study: {"id", "model", "text", "inputs": {...}, "seconds"}. Resumable: studies already in the file are
skipped. ReXGradient PNGs are 16-bit; they are windowed to 8-bit RGB with a 0.5/99.5 percentile stretch.

Inputs per model follow the authors' own inference examples:
  chexagent  all images of the study + indication  ("Given the indication: ..., write a structured findings section for the CXR.")
  maira2     frontal image (+ lateral if present) + indication, technique, comparison; non-grounded findings
  llavarad   frontal image only ("Describe the findings of the chest x-ray.")
CXRMate-2 is not generated here: the authors released their ReXGradient test outputs (see build_pool_rexgradient.py).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "rexgradient"
IMG_ROOT = Path("/data2/moll/datasets/rexgradient")
MODELS = Path("/data2/moll/models")
DEVICE = "cuda:1"

VIEW_PROMPTS = {
    "official": "What is the view of this chest X-ray? Options: (a) PA, (b) AP, (c) LATERAL",   # CheXagent's own wrapper
    "binary": "Is this chest X-ray a frontal view or a lateral view? Options: (a) FRONTAL, (b) LATERAL",
}
VIEW_PROMPT = VIEW_PROMPTS["official"]


# ---- images -------------------------------------------------------------------------------------
def load_rgb(path: Path, cache_dir: Path | None = None) -> Image.Image:
    """16-bit grayscale PNG -> 8-bit RGB (percentile window). Cached as PNG next to the run if asked."""
    if cache_dir:
        c = cache_dir / (path.stem + "_8bit.png")
        if c.exists():
            return Image.open(c).convert("RGB")
    a = np.asarray(Image.open(path)).astype(np.float32)
    if a.ndim == 3:
        a = a.mean(axis=2)
    lo, hi = np.percentile(a, 0.5), np.percentile(a, 99.5)
    a = np.clip((a - lo) / max(hi - lo, 1.0), 0, 1)
    im = Image.fromarray((a * 255).astype(np.uint8)).convert("RGB")
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        im.save(cache_dir / (path.stem + "_8bit.png"))
    return im


def study_images(row) -> list[Path]:
    return [IMG_ROOT / p for p in str(row.image_paths).split("|")]


def load_views() -> dict[str, str]:
    p = RAW / "views.json"
    return json.loads(p.read_text()) if p.exists() else {}


def frontal_and_lateral(row, views: dict[str, str]) -> tuple[Path, Path | None]:
    """Frontal image first. Uses the classified views; with one image or no classification, the first
    image is treated as frontal. Metadata view labels (AP/PA/LL/LATERAL) win when present."""
    imgs = study_images(row)
    meta = [v.upper() for v in str(row.views).split("|")]
    lab = []
    for img, mv in zip(imgs, meta + [""] * len(imgs)):
        if mv in ("AP", "PA", "POSTERO_ANTERIOR"):
            lab.append("frontal")
        elif mv in ("LL", "RL", "LATERAL"):
            lab.append("lateral")
        else:
            lab.append(views.get(str(img.relative_to(IMG_ROOT)), "frontal"))
    fr = [i for i, l in zip(imgs, lab) if l == "frontal"] or imgs[:1]
    la = [i for i, l in zip(imgs, lab) if l == "lateral" and i != fr[0]]
    return fr[0], (la[0] if la else None)


# ---- models -------------------------------------------------------------------------------------
class CheXagent:
    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        m = MODELS / "CheXagent-2-3b"
        self.tok = AutoTokenizer.from_pretrained(m, trust_remote_code=True)
        # as in the authors' wrapper: load in fp32, then cast (torch_dtype= at load breaks the vision tower)
        self.model = AutoModelForCausalLM.from_pretrained(m, device_map=DEVICE, trust_remote_code=True).to(torch.bfloat16).eval()

    def generate(self, paths: list[str], prompt: str, max_new_tokens: int = 512) -> str:
        query = self.tok.from_list_format([*[{"image": p} for p in paths], {"text": prompt}])
        conv = [{"from": "system", "value": "You are a helpful assistant."}, {"from": "human", "value": query}]
        ids = self.tok.apply_chat_template(conv, add_generation_prompt=True, return_tensors="pt").to(DEVICE)
        out = self.model.generate(ids, do_sample=False, num_beams=1, temperature=1.0, top_p=1.0, use_cache=True,
                                  max_new_tokens=max_new_tokens)[0]
        return self.tok.decode(out[ids.size(1):-1]).strip()

    def findings(self, row, views, cache) -> tuple[str, dict]:
        paths = [str(cache / (p.stem + "_8bit.png")) for p in study_images(row)]
        for p in study_images(row):
            load_rgb(p, cache)
        ind = str(row.indication or "").strip() or "Not given."
        prompt = f'Given the indication: "{ind}", write a structured findings section for the CXR.'
        return self.plain(self.generate(paths, prompt)), {"images": len(paths), "indication": ind}

    @staticmethod
    def plain(text: str) -> str:
        """CheXagent's structured output interleaves section tags ("[Breathing: Lungs]") and markdown
        emphasis with the sentences. Raters see reports as prose, so drop the scaffolding, keep the text."""
        text = re.sub(r"\[[^\]]{1,60}\]\s*", "", text)
        text = text.replace("**", "").replace("__", "")
        return re.sub(r"\s+", " ", text).strip()

    def view(self, path: Path, cache, prompt: str = VIEW_PROMPT) -> str:
        load_rgb(path, cache)
        ans = self.generate([str(cache / (path.stem + "_8bit.png"))], prompt, 16).lower()
        if prompt == VIEW_PROMPTS["binary"]:
            return "lateral" if "lateral" in ans or "(b)" in ans else "frontal"
        return "lateral" if "lateral" in ans or "(c)" in ans else "frontal"


class Maira2:
    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        m = MODELS / "maira-2"
        self.torch = torch
        self.model = AutoModelForCausalLM.from_pretrained(m, trust_remote_code=True, torch_dtype=torch.bfloat16).eval().to(DEVICE)
        self.proc = AutoProcessor.from_pretrained(m, trust_remote_code=True)

    def findings(self, row, views, cache) -> tuple[str, dict]:
        fr, la = frontal_and_lateral(row, views)
        inputs = self.proc.format_and_preprocess_reporting_input(
            current_frontal=load_rgb(fr, cache), current_lateral=load_rgb(la, cache) if la else None, prior_frontal=None,
            indication=str(row.indication or "").strip() or None, technique=str(row.technique or "").strip() or None,
            comparison=str(row.comparison or "").strip() or None, prior_report=None, return_tensors="pt", get_grounding=False,
        ).to(DEVICE)
        with self.torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=300, use_cache=True)
        text = self.proc.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
        text = self.proc.convert_output_to_plaintext_or_grounded_sequence(text.lstrip())
        return str(text).strip(), {"frontal": fr.name, "lateral": la.name if la else None, "indication": row.indication}


class LlavaRad:
    def __init__(self):
        import os
        import torch
        os.chdir(MODELS / "llava-rad")  # relative encoder config/checkpoint paths in its config.json
        from llava.model.builder import load_pretrained_model
        from llava.utils import disable_torch_init
        disable_torch_init()
        self.torch = torch
        self.tok, self.model, self.image_processor, _ = load_pretrained_model(
            str(MODELS / "llava-rad"), str(MODELS / "vicuna-7b-v1.5"), "llavarad", device_map={"": 1}, device=DEVICE)

    def findings(self, row, views, cache) -> tuple[str, dict]:
        from llava.constants import IMAGE_TOKEN_INDEX
        from llava.conversation import conv_templates
        from llava.mm_utils import KeywordsStoppingCriteria, tokenizer_image_token
        fr, _ = frontal_and_lateral(row, views)
        conv = conv_templates["v1"].copy()
        conv.append_message(conv.roles[0], "<image>\nDescribe the findings of the chest x-ray.\n")
        conv.append_message(conv.roles[1], None)
        pix = self.image_processor.preprocess(load_rgb(fr, cache), return_tensors="pt")["pixel_values"][0].half().unsqueeze(0).to(DEVICE)
        ids = tokenizer_image_token(conv.get_prompt(), self.tok, IMAGE_TOKEN_INDEX, return_tensors="pt").unsqueeze(0).to(DEVICE)
        with self.torch.inference_mode():
            out = self.model.generate(ids, images=pix, do_sample=False, temperature=0.0, max_new_tokens=1024, use_cache=True,
                                      stopping_criteria=[KeywordsStoppingCriteria(["</s>"], self.tok, ids)])
        return self.tok.batch_decode(out[:, ids.shape[1]:], skip_special_tokens=True)[0].strip(), {"frontal": fr.name}


# ---- driver -------------------------------------------------------------------------------------
def load_selection() -> pd.DataFrame:
    sel = pd.read_csv(RAW / "selected_studies.csv", dtype=str).fillna("")
    meta = pd.read_csv(IMG_ROOT / "metadata" / "test_metadata.csv", dtype=str).fillna("").set_index("id")
    sel["indication"] = sel.id.map(meta.Indication)
    sel["comparison"] = sel.id.map(meta.Comparison)
    sel["technique"] = sel.StudyDescription.map(lambda d: {"1": "Single view of the chest.", "2": "Two views of the chest."}.get(
        re.search(r"(\d)V", d).group(1) if re.search(r"(\d)V", d) else "", "Chest radiograph."))
    return sel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", choices=["views", "chexagent", "maira2", "llavarad"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--evaluate", action="store_true",
                    help="views only: score each prompt on the extracted test images that carry a view label, then stop")
    ap.add_argument("--view-prompt", choices=sorted(VIEW_PROMPTS), default="official")
    args = ap.parse_args(argv)

    sel = load_selection()
    if args.limit:
        sel = sel.head(args.limit)
    cache = RAW / "images_8bit"
    out_dir = RAW / "candidates"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.model == "views" and args.evaluate:
        # ground truth: the ~12% of test images whose DICOM view position survived de-identification
        js = json.loads((IMG_ROOT / "metadata" / "test_metadata.json").read_text())
        labelled = []
        for st in js.values():
            for path, vp in zip(st["ImagePath"], st["ImageViewPosition"]):
                v = (vp or "").upper()
                truth = "frontal" if v in ("AP", "PA", "POSTERO_ANTERIOR") else "lateral" if v in ("LL", "RL", "LATERAL") else None
                full = IMG_ROOT / path.replace("../", "", 1)
                if truth and full.exists():
                    labelled.append((full, truth))
        rng = random.Random(0)
        rng.shuffle(labelled)
        labelled = labelled[: args.limit or 300]
        n_lat = sum(1 for _, t in labelled if t == "lateral")
        print(f"{len(labelled)} labelled images available ({n_lat} lateral)")
        m = CheXagent()
        for name, prompt in VIEW_PROMPTS.items():
            ok = Counter()
            for path, truth in labelled:
                pred = m.view(path, cache, prompt)
                ok[(truth, pred)] += 1
            acc = (ok[("frontal", "frontal")] + ok[("lateral", "lateral")]) / max(1, len(labelled))
            print(f"prompt {name:9s} accuracy {acc:.3f} | confusion (truth,pred): {dict(ok)}")
        return 0

    if args.model == "views":
        views = {} if args.force else load_views()
        todo = [p for _, r in sel.iterrows() if r.n_views == "2" for p in study_images(r)
                if str(p.relative_to(IMG_ROOT)) not in views and p.exists()]
        print(f"classifying {len(todo)} images of two-view studies")
        m = CheXagent()
        for i, p in enumerate(todo, 1):
            views[str(p.relative_to(IMG_ROOT))] = m.view(p, cache, VIEW_PROMPTS[args.view_prompt])
            if i % 20 == 0 or i == len(todo):
                (RAW / "views.json").write_text(json.dumps(views, indent=1))
                print(f"  {i}/{len(todo)}", flush=True)
        print("done:", Counter(views.values()))
        return 0

    out_path = out_dir / f"{args.model}.jsonl"
    done = set()
    if out_path.exists() and not args.force:
        done = {json.loads(l)["id"] for l in out_path.read_text().splitlines() if l.strip()}
    todo = sel[~sel.id.isin(done)]
    # images arrive in batches while the archive downloads: only generate for complete studies, write
    # nothing for the others so a later run picks them up
    present = todo.apply(lambda r: all(p.exists() for p in study_images(r)), axis=1)
    print(f"{args.model}: {int(present.sum())} studies to generate ({len(done)} already done, "
          f"{int((~present).sum())} waiting for images)")
    todo = todo[present]
    if todo.empty:
        return 0
    views = load_views()
    model = {"chexagent": CheXagent, "maira2": Maira2, "llavarad": LlavaRad}[args.model]()
    with out_path.open("a" if not args.force else "w", encoding="utf-8") as f:
        for i, (_, row) in enumerate(todo.iterrows(), 1):
            t0 = time.time()
            try:
                text, inputs = model.findings(row, views, cache)
                err = None
            except Exception as e:  # noqa: BLE001
                text, inputs, err = "", {}, f"{type(e).__name__}: {e}"
            f.write(json.dumps({"id": row.id, "model": args.model, "text": text, "inputs": inputs,
                                "seconds": round(time.time() - t0, 1), "error": err}, ensure_ascii=False) + "\n")
            f.flush()
            if i % 10 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} · last {time.time() - t0:.1f}s" + (f" · ERROR {err}" if err else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
