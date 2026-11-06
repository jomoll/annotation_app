"""Put the reference's line breaks back into an injected report, changing no words.

The injector works on whitespace tokens and rejoins them with single spaces, so a multi-line
reference comes back as one paragraph. That is a leak, not a cosmetic issue: injection is the
strongest stratum, so a reader who notices that the one-paragraph candidate is the near-perfect one
reads the answer off the layout. (Measured on the brain-MRI arm: 2.9 lines against the
references' 11.1, before this was added.)

An injected report keeps ~98% of the reference's words, so difflib can locate where each reference
line ended and a newline goes back in at the same place. Substituted, deleted and appended text just
shift the boundaries; nothing is rewritten, and the caller checks that, whitespace aside, the result
is identical to the input.
"""
from __future__ import annotations

import difflib


def flat(s: str) -> str:
    return " ".join((s or "").split())


def restore(reference: str, injected: str) -> str | None:
    """`injected` re-broken at `reference`'s line boundaries, or None if that is not possible."""
    ref_words: list[str] = []
    bounds: set[int] = set()
    for line in reference.split("\n"):
        w = line.split()
        if not w:
            continue
        ref_words += w
        bounds.add(len(ref_words))          # a line ends after this many reference words
    inj_words = injected.split()
    if not ref_words or not inj_words:
        return None
    bounds.discard(len(ref_words))          # the last boundary is the end of the text

    sm = difflib.SequenceMatcher(None, ref_words, inj_words, autojunk=False)
    pos: dict[int, int] = {0: 0, len(ref_words): len(inj_words)}   # ref gap -> injected gap
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1 + 1):
                pos[i1 + k] = j1 + k
        else:                                # edited span: pin only its two ends
            pos.setdefault(i1, j1)
            pos[i2] = j2

    cuts = sorted({pos[b] for b in bounds if b in pos and 0 < pos[b] < len(inj_words)})
    if not cuts:
        return None
    out, prev = [], 0
    for c in cuts:
        out.append(" ".join(inj_words[prev:c]))
        prev = c
    out.append(" ".join(inj_words[prev:]))
    return "\n".join(x for x in out if x)


def restore_checked(reference: str, injected: str) -> str:
    """restore(), but return `injected` unchanged unless the result is word-for-word identical."""
    if "\n" not in reference.strip() or "\n" in injected.strip():
        return injected
    new = restore(reference, injected)
    return new if new is not None and flat(new) == flat(injected) else injected
