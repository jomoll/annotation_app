#!/usr/bin/env python3
"""Strip Markdown formatting from candidate reports so no system is identifiable by layout.

Claude is the only system in the brain-MRI arm that formats its output: 40/40 of its reports use
``**bold**`` section headers and ``- `` bullets, while injected, retrieved, Hulu-Med, NeuroVFM and
the MR-RATE references themselves are all plain prose. That makes its stratum separable by eye
before a word is read, so any prior an annotator holds about "the long formatted one" lands on one
system and one system only -- a confound built into the annotations rather than into the model.

The fix has to preserve content. Shortening the reports would not: cutting the v3 prompt's region
walk halves the findings (9.5 -> 4.2 correct claims per report), so length and detection cannot be
separated by prompting. Markdown is different -- the asterisks and hyphens carry no clinical
information, so removing them costs nothing and leaves every claim in place.

Two details worth knowing:

* The app escapes HTML rather than rendering it (``app/app.py``: ``"<br>".join(_esc(x) ...)``), so
  the markers were never rendered as formatting anyway. Annotators would have read literal
  ``**Cerebral parenchyma and white matter**`` on screen.
* Run this before ``precompute_links.py``. That script stores character offsets into the report
  text, so normalising afterwards would silently shift every stored span.

Idempotent: running it twice changes nothing the second time.

    ./scripts/normalize_candidate_format.py data/raw/mrrate/candidates/claude_opus55_v3.jsonl
    ./scripts/normalize_candidate_format.py --check <file>     # report only, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

FIELDS = ("text", "findings", "impression")

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BULLET = re.compile(r"^[ \t]*[-*•]\s+", re.M)
_HEADING = re.compile(r"^[ \t]*#{1,6}\s+", re.M)


def normalize(text: str) -> str:
    """Plain-text equivalent of `text`, with every claim preserved.

    ``**Section**`` on a line of its own becomes ``Section:`` -- the MR-RATE references use exactly
    that shape ("Cranial:", "Cervical and Thoracic:"), so the result reads like the corpus instead
    of like a chat reply. Bold used mid-sentence just loses its markers.
    """
    if not text:
        return text
    out = []
    for line in text.split("\n"):
        line = _HEADING.sub("", line)
        stripped = line.strip()
        # a whole line that is nothing but one bold span: that is a section header
        m = re.fullmatch(r"\*\*(.+?)\*\*:?", stripped)
        if m:
            label = m.group(1).strip().rstrip(":")
            out.append(f"{label}:")
            continue
        line = _BULLET.sub("", line)
        line = _BOLD.sub(r"\1", line)
        out.append(line.rstrip())
    text = "\n".join(out)
    # A report cut off mid-span leaves an opening ``**`` with no partner, which the paired regex
    # cannot match (the old max_tokens=1200 run produced several). Asterisks never carry clinical
    # meaning, so sweep whatever survived.
    text = text.replace("**", "")
    # collapse the blank-line runs that stripping headers can leave behind
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args(argv)

    for path in args.files:
        if not path.exists():
            print(f"{path}: missing", file=sys.stderr)
            return 1
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        changed = 0
        for r in rows:
            if r.get("error"):
                continue
            before = tuple(r.get(f) or "" for f in FIELDS)
            for f in FIELDS:
                if r.get(f):
                    r[f] = normalize(r[f])
            if before != tuple(r.get(f) or "" for f in FIELDS):
                changed += 1
        marks = sum(1 for r in rows if any("**" in (r.get(f) or "") for f in FIELDS))
        print(f"{path.name}: {len(rows)} rows, {changed} normalized, {marks} still carrying '**'")
        if args.check:
            continue
        if changed:
            backup = path.with_suffix(path.suffix + ".premarkdown_bak")
            if not backup.exists():
                shutil.copy2(path, backup)
            with path.open("w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
