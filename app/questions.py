"""The rating questionnaire — the one file to edit when adapting the study.

Every question is a dict with
    key      column name in the annotation CSV
    title    short heading shown above the widget
    prompt   the actual question
    type     "likert" (numeric scale) or "binary" (two options)
    scale    likert only: {score: (short label, definition)} — the keys define the scale
             (1–5, 1–3, …); the definition may contain "\n" for several lines
    options  binary only: the two option labels
    note     optional hint shown under the prompt
    required whether the case counts as "rated" without it (default True)

Order here is display order. The stored value is the score or the option label,
never the anchor text, so anchors can be reworded later without breaking
comparability of already-saved ratings.
"""
from __future__ import annotations

QUESTIONS = [
    {
        "key": "completeness",
        "title": "Completeness",
        "prompt": "Is all necessary information that the patient would need, given the clinical question, present in the candidate?",
        "type": "likert",
        "scale": {
            1: ("None", "Captures no important information."),
            2: ("~25%", "Captures about 25% of the important information."),
            3: ("~50%", "Captures about 50% of the important information."),
            4: ("~75%", "Captures about 75% of the important information."),
            5: ("All", "Captures all of the important information."),
        },
    },
    {
        "key": "correctness",
        "title": "Correctness",
        "prompt": ("How medically accurate is the candidate? Judge whether its statements and interpretations are "
                   "supported by the reference report, regardless of completeness or relevance."),
        "type": "likert",
        "scale": {
            1: ("Mostly incorrect", "The overall interpretation is wrong or fabricated; major medical or radiological "
                                    "errors substantially undermine the report."),
            2: ("Substantially incorrect", "Several significant errors, though the overall picture is partly right."),
            3: ("Partially correct", "A mix of correct and incorrect information, with neither clearly dominating."),
            4: ("Mostly correct", "Essentially correct, with only minor factual or interpretive errors."),
            5: ("Fully correct", "All medically meaningful statements and interpretations are accurate; no substantive errors."),
        },
    },
    {
        "key": "same_diagnosis",
        "title": "Same diagnosis",
        "prompt": "Does the candidate conclude the same main diagnosis as the reference?",
        "type": "binary",
        "options": ["Yes", "No"],
    },
    {
        "key": "safety",
        "title": "Clinical safety",
        "prompt": "Does any statement in the candidate lead to a serious safety risk?",
        "note": ("For example a fabricated or omitted finding, an incorrect finding, or a severe mismatch with the "
                 "reference that could lead to a different treatment."),
        "type": "binary",
        "options": ["Yes", "No"],
    },
    {
        "key": "overall",
        "title": "Overall score",
        "prompt": "Your overall judgment of the candidate report.",
        "note": ("Excluding style. Not a calculation from the previous scores — this is deliberately subjective; "
                 "the definitions are guidelines."),
        "type": "likert",
        "scale": {
            1: ("Very poor", "Fundamentally inadequate or clinically unreliable.\n"
                             "Major errors, omissions, or incorrect interpretation substantially undermine the report."),
            2: ("Poor", "Substantial problems that reduce clinical usefulness.\n"
                        "Important findings may be missing, incorrect, or misinterpreted.\n"
                        "Requires significant correction before being relied upon."),
            3: ("Acceptable / mixed", "Provides meaningful clinical value but has noticeable limitations.\n"
                                      "May have moderate omissions, incorrect details, or an imperfect interpretation.\n"
                                      "Still reasonably useful overall."),
            4: ("Good", "Clinically useful and largely correct.\n"
                        "May contain minor errors, omissions, or imprecision.\n"
                        "No issue that meaningfully affects the clinical interpretation."),
            5: ("Excellent", "Clinically strong and highly useful.\n"
                             "Accurate, appropriately complete, and reaches the appropriate diagnosis/impression.\n"
                             "No meaningful errors or omissions."),
        },
    },
    {
        "key": "presentation",
        "title": "Presentation quality",
        "prompt": "How well does the candidate present its content — clarity, prioritisation, conciseness?",
        "note": "Form only, not content. Subjective; does not feed into the overall score.",
        "type": "likert",
        "scale": {
            1: ("Poor", "Not clear, wrong ordering, verbose."),
            2: ("Acceptable", "Minor issues with ordering, clarity or verbosity."),
            3: ("Perfect", "Clear, well ordered, concise."),
        },
    },
]

QUESTION_KEYS = [q["key"] for q in QUESTIONS]
REQUIRED_KEYS = [q["key"] for q in QUESTIONS if q.get("required", True)]


def likert_values(q: dict) -> list[int]:
    """The scale points of a likert question, ascending."""
    return sorted(q["scale"])
