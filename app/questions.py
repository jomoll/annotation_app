"""The rating questionnaire — the one file to edit when adapting the study.

Every question is a dict with
    key      column name in the annotation CSV
    title    short heading shown above the widget
    prompt   the actual question
    type     "likert5" (1–5 scale) or "binary" (two options)
    scale    likert5 only: {score: (short label, definition)} for the scores
             you want to anchor; unanchored scores just show the number
    options  binary only: the two option labels
    note     optional grey hint under the prompt
    required whether the case counts as "rated" without it (default True)

Order here is display order. The stored value is the score (1–5) or the
option label, never the anchor text, so anchors can be reworded later without
breaking comparability of already-saved ratings.

TODO(study lead): the per-score definitions below are PROVISIONAL placeholders.
Replace them with the final definitions before the study starts.
"""
from __future__ import annotations

LIKERT = [1, 2, 3, 4, 5]

QUESTIONS = [
    {
        "key": "correctness",
        "title": "Correctness",
        "prompt": "Is the candidate factually and clinically correct?",
        "type": "likert5",
        "scale": {
            1: ("Mostly incorrect", "Most statements are wrong, fabricated or contradict the reference."),
            2: ("Major errors", "At least one clinically relevant error (e.g. wrong laterality, false abnormal/normal call)."),
            3: ("Minor errors", "Only minor inaccuracies that would not change the clinical picture."),
            4: ("Largely correct", "Essentially correct; at most trivial imprecisions."),
            5: ("Fully correct", "Everything stated is correct and consistent with the reference."),
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
        "key": "completeness",
        "title": "Completeness",
        "prompt": "Does the candidate capture all important findings present in the reference?",
        "type": "likert5",
        "scale": {
            1: ("Most findings missing", "The important findings of the reference are largely absent."),
            2: ("Key finding missing", "At least one clinically important finding is missing."),
            3: ("Minor omissions", "Only secondary or incidental findings are missing."),
            4: ("Nearly complete", "All important findings present; small details missing."),
            5: ("Complete", "Every important finding of the reference is captured."),
        },
    },
    {
        "key": "safety",
        "title": "Clinical safety",
        "prompt": (
            "Is there a clinically meaningful safety issue — could acting on the candidate instead of the "
            "reference lead to a misdiagnosis, wrong treatment or a delay?"
        ),
        "type": "likert5",
        "scale": {
            1: ("Severe risk", "Likely to cause serious harm (e.g. missed or invented critical finding)."),
            2: ("Significant risk", "Could plausibly change management for the worse."),
            3: ("Moderate risk", "Might cause unnecessary work-up or minor delay."),
            4: ("Minimal risk", "Differences are unlikely to affect the patient."),
            5: ("No safety issue", "Acting on the candidate would be as safe as acting on the reference."),
        },
    },
    {
        "key": "overall",
        "title": "Overall score",
        "prompt": "Your overall, subjective judgment of the candidate report.",
        "type": "likert5",
        "scale": {
            1: ("Unacceptable", ""),
            2: ("Poor", ""),
            3: ("Acceptable with revision", ""),
            4: ("Good", ""),
            5: ("Excellent", ""),
        },
    },
    {
        "key": "presentation",
        "title": "Presentation quality",
        "prompt": "Does the candidate communicate its findings effectively — clearly, concisely and in a well-structured way?",
        "type": "likert5",
        "note": "Judge form only, not content. This does not feed into the overall score.",
        "scale": {
            1: ("Very poor", "Hard to follow, repetitive, or disorganised."),
            2: ("Poor", ""),
            3: ("Adequate", ""),
            4: ("Good", ""),
            5: ("Excellent", "Clear, concise, well structured — reads like a good report."),
        },
    },
]

QUESTION_KEYS = [q["key"] for q in QUESTIONS]
REQUIRED_KEYS = [q["key"] for q in QUESTIONS if q.get("required", True)]
