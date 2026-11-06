"""The GREEN prompt and score, for an OpenAI-compatible judge.

GREEN (Ostmeier et al., Findings of EMNLP 2024; github.com/Stanford-AIMI/GREEN, MIT per its
setup.py): a judge model lists clinically significant and insignificant errors in six categories
plus the matched findings, and

    GREEN = matched / (matched + clinically significant errors)       (0 if nothing matched)

The prompt (`make_prompt`, including its 300-word truncation of each report) and the parsing are
copied from green_score/utils.py and green_score/green.py. The published judge is a fine-tuned
GREEN-RadLlama2-7b trained on chest X-ray pairs; here the prompt goes to whatever general model
ANNOTATION_LLM_* points at, so the numbers are a GREEN variant and not comparable with published
GREEN values. For checking the spread of a candidate pool that does not matter: only the ordering
and the shape of the distribution are used.
"""
from __future__ import annotations

import re

CATEGORIES = ["Clinically Significant Errors", "Clinically Insignificant Errors", "Matched Findings"]
SUB = [
    "(a) False report of a finding in the candidate",
    "(b) Missing a finding present in the reference",
    "(c) Misidentification of a finding's anatomic location/position",
    "(d) Misassessment of the severity of a finding",
    "(e) Mentioning a comparison that isn't in the reference",
    "(f) Omitting a comparison detailing a change from a prior study",
]


def make_prompt(text1: str, text2: str, max_len: int = 300) -> str:
    """text1 = reference, text2 = candidate. Verbatim from green_score/utils.py."""
    text1 = " ".join(text1.split()[:max_len])
    text2 = " ".join(text2.split()[:max_len])
    return f"Objective: Evaluate the accuracy of a candidate radiology report in comparison to a reference radiology report composed by expert radiologists.\n\n    Process Overview: You will be presented with:\n\n    1. The criteria for making a judgment.\n    2. The reference radiology report.\n    3. The candidate radiology report.\n    4. The desired format for your assessment.\n\n    1. Criteria for Judgment:\n\n    For each candidate report, determine:\n\n    The count of clinically significant errors.\n    The count of clinically insignificant errors.\n\n    Errors can fall into one of these categories:\n\n    a) False report of a finding in the candidate.\n    b) Missing a finding present in the reference.\n    c) Misidentification of a finding's anatomic location/position.\n    d) Misassessment of the severity of a finding.\n    e) Mentioning a comparison that isn't in the reference.\n    f) Omitting a comparison detailing a change from a prior study.\n    Note: Concentrate on the clinical findings rather than the report's writing style. Evaluate only the findings that appear in both reports.\n\n    2. Reference Report:\n    {text1}\n\n    3. Candidate Report:\n    {text2}\n\n    4. Reporting Your Assessment:\n\n    Follow this specific format for your output, even if no errors are found:\n    ```\n    [Explanation]:\n    <Explanation>\n\n    [Clinically Significant Errors]:\n    (a) <Error Type>: <The number of errors>. <Error 1>; <Error 2>; ...; <Error n>\n    ....\n    (f) <Error Type>: <The number of errors>. <Error 1>; <Error 2>; ...; <Error n>\n\n    [Clinically Insignificant Errors]:\n    (a) <Error Type>: <The number of errors>. <Error 1>; <Error 2>; ...; <Error n>\n    ....\n    (f) <Error Type>: <The number of errors>. <Error 1>; <Error 2>; ...; <Error n>\n\n    [Matched Findings]:\n    <The number of matched findings>. <Finding 1>; <Finding 2>; ...; <Finding n>\n    ```\n"


def _clean(response: str) -> str:
    if "[Explanation]:" in response:
        if "<|assistant|>" in response:
            response = response.split("<|assistant|>")[-1]
        response = response.split("[Explanation]:")[-1]
    if "<|assistant|>" in response:
        response = response.split("<|assistant|>")[-1]
    return response.replace("</s>", "").replace("<unk>", "")


def _counts(text: str, category: str) -> tuple[int, list[int]] | None:
    m = re.search(rf"\[{category}\]:\s*(.*?)(?:\n\s*\n|\Z)", text, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    if body.startswith("No"):
        return 0, [0] * 6
    if category == "Matched Findings":
        n = re.findall(r"^\b\d+\b(?=\.)", body)
        return (int(n[0]) if n else 0), [0] * 6
    subs = [s.split(" ", 1)[0] + " " for s in SUB]
    lines = sorted(re.findall(r"\([a-f]\) .*", body))
    if not lines:
        lines = sorted(re.findall(r"\([1-6]\) .*", body))
        subs = [f"({i}) " for i in range(1, 7)]
    sub = [0] * 6
    for pos, prefix in enumerate(subs):
        for line in lines:
            if line.startswith(prefix):
                n = re.findall(r"(?<=: )\b\d+\b(?=\.)", line)
                if n:
                    sub[pos] = int(n[0])
    return sum(sub), sub


def score(response: str) -> dict:
    """{'green', 'matched', 'significant_errors'}; green is None when the answer has no verdict."""
    text = _clean(response or "")
    sig, matched = _counts(text, CATEGORIES[0]), _counts(text, CATEGORIES[2])
    if sig is None or matched is None:
        return {"green": None, "matched": None, "significant_errors": None}
    n_match, n_sig = matched[0], sig[0]
    return {"green": 0.0 if n_match == 0 else n_match / (n_match + n_sig),
            "matched": n_match, "significant_errors": n_sig}
