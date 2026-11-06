#!/usr/bin/env python3
"""Build docs/new_modality_guide.pdf — how to run a reader-study arm for a new modality.

    python3 docs/make_new_modality_guide.py

Content is the protocol as actually run for the chest-CT and chest-X-ray arms, plus the failures
we hit doing it. Edit here and rebuild; do not edit the PDF.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, ListFlowable, ListItem,
                                NextPageTemplate, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

OUT = Path(__file__).resolve().parent / "new_modality_guide.pdf"

INK = colors.HexColor("#1a1d21")
MUTED = colors.HexColor("#5b6670")
RULE = colors.HexColor("#c9d1d8")
ACCENT = colors.HexColor("#1f5c7a")
WARN_BG = colors.HexColor("#fdf5e6")
WARN_EDGE = colors.HexColor("#c08a2e")
CODE_BG = colors.HexColor("#f4f6f8")

ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold",
                            fontSize=21, leading=25, textColor=INK, alignment=TA_LEFT,
                            spaceAfter=2),
    "sub": ParagraphStyle("sub", parent=ss["Normal"], fontName="Helvetica", fontSize=10.5,
                          leading=15, textColor=MUTED, spaceAfter=14),
    "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=13,
                         leading=16, textColor=ACCENT, spaceBefore=15, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=10.5,
                         leading=13, textColor=INK, spaceBefore=10, spaceAfter=4),
    "body": ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5,
                           leading=14, textColor=INK, spaceAfter=6),
    "bullet": ParagraphStyle("bullet", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5,
                             leading=13.5, textColor=INK, spaceAfter=3),
    "code": ParagraphStyle("code", parent=ss["Normal"], fontName="Courier", fontSize=8.2,
                           leading=11.5, textColor=INK, backColor=CODE_BG,
                           borderPadding=(5, 6, 5, 6), spaceBefore=3, spaceAfter=7),
    "warn": ParagraphStyle("warn", parent=ss["Normal"], fontName="Helvetica", fontSize=9,
                           leading=13, textColor=INK, spaceAfter=2),
    "cap": ParagraphStyle("cap", parent=ss["Normal"], fontName="Helvetica-Oblique", fontSize=8.3,
                          leading=11.5, textColor=MUTED, spaceAfter=8),
}


def P(t, k="body"):
    return Paragraph(t, S[k])


def bullets(items, style="bullet"):
    return ListFlowable([ListItem(Paragraph(i, S[style]), leftIndent=10) for i in items],
                        bulletType="bullet", bulletFontSize=6, bulletOffsetY=1,
                        leftIndent=12, spaceAfter=6)


def steps(items):
    return ListFlowable([ListItem(Paragraph(i, S["bullet"]), leftIndent=12) for i in items],
                        bulletType="1", bulletFormat="%s.", leftIndent=14, spaceAfter=6)


def code(lines):
    return Paragraph("<br/>".join(l.replace("&", "&amp;").replace("<", "&lt;").replace(" ", "&nbsp;")
                                  for l in lines), S["code"])


def warn(title, text):
    t = Table([[Paragraph(f"<b>{title}</b><br/>{text}", S["warn"])]], colWidths=[165 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WARN_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 2, WARN_EDGE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return KeepTogether([t, Spacer(1, 7)])


def table(rows, widths, head=True):
    data = [[Paragraph(f"<b>{c}</b>" if head and i == 0 else c, S["bullet"]) for c in r]
            for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor("#e8ecef")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    t.setStyle(TableStyle(style))
    return KeepTogether([t, Spacer(1, 9)])


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(22 * mm, 15 * mm, 188 * mm, 15 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(22 * mm, 10.5 * mm, "Reader study — adding a new modality")
    canvas.drawRightString(188 * mm, 10.5 * mm, f"{doc.page}")
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(str(OUT), pagesize=A4,
                          leftMargin=22 * mm, rightMargin=22 * mm,
                          topMargin=20 * mm, bottomMargin=20 * mm,
                          title="Reader study: adding a new modality",
                          author="Johannes Moll")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=footer)])

    F = []
    F.append(P("Adding a new modality to the reader study", "title"))
    F.append(P("A step-by-step protocol, as run for the chest-CT and chest-X-ray arms, using the "
               "reader-study app at github.com/jomoll/annotation_app. Written for a "
               "collaborator setting up a new arm from scratch.", "sub"))

    # ---------------------------------------------------------------- what the study is for
    F.append(P("0. Know what the candidates are for", "h1"))
    F.append(P("The study validates an automatic report-quality <b>metric</b> against clinician "
               "judgement. It does not benchmark models. That single fact decides most of the "
               "design questions below: candidate reports are <b>stimuli</b>, not subjects. What "
               "the stimulus set must do is span the quality range and cover the error types the "
               "metric has to detect. Which generator produced a given report matters only as a "
               "means to that end.", "body"))
    F.append(P("Two consequences people get wrong. You do not need the candidate models to be "
               "separable from one another, so do not add models hoping to tell them apart. And "
               "if the models available for your modality genuinely cannot span the range, the "
               "candidates do not all have to come from models at all.", "body"))

    # ---------------------------------------------------------------- step 1
    F.append(P("1. Choose the dataset and fix the split", "h1"))
    F.append(bullets([
        "Use a <b>held-out split only</b> (test, or validation if the models were tuned on test). "
        "Candidate models must not have trained on the studies your readers see.",
        "Check the licence before anything else. Gated or non-commercial datasets constrain where "
        "images may be sent, which matters if you later call a hosted model.",
        "Confirm the reference reports have usable sections. We rely on <b>findings</b> and "
        "<b>impression</b>; clinical information and technique are shown for context but are not "
        "scored.",
    ]))
    F.append(warn("Verify the split actually contains what you need",
                  "For the MRI arm the dataset was advertised as brain and spine, but the test "
                  "split proved to be 97.8% brain, 2.2% combined brain-and-spine, and exactly one "
                  "spine study. A planned spine stratum was impossible. Count your strata in the "
                  "held-out split before designing around them."))

    # ---------------------------------------------------------------- step 2
    F.append(P("2. Select and filter studies", "h1"))
    F.append(steps([
        "<b>One study per patient.</b> Keep the earliest; a follow-up leaks information even when "
        "its own report looks self-contained.",
        "<b>Exclude reports that reference a prior.</b> Models see one study and cannot know what "
        "changed. Use a strong-phrase regex for the obvious cases (\"compared with\", \"previous "
        "examination\", a de-identified date token) and send borderline wording (\"stable\", "
        "\"unchanged\", \"interval\") to an LLM to decide. Log every exclusion with its reason.",
        "<b>Stratify</b> on whatever drives rating variance in your modality, and cap the "
        "normal share.",
        "<b>Over-select</b> by roughly 3x so you can drop studies later without re-sampling.",
    ]))
    F.append(warn("Cap the normal share deliberately",
                  "In our X-ray arm, normal-versus-abnormal explained 49% of the variance in "
                  "overall scores, against 4.7% for model identity. Readers rate normal studies "
                  "highly almost regardless of candidate quality. We rebuilt the pool at a 15% "
                  "normal share. Decide this before generating candidates, not after."))

    # ---------------------------------------------------------------- step 3
    F.append(P("3. Generate candidate reports", "h1"))
    F.append(P("<b>The default design is four or five models specialised for the modality, and "
               "nothing else.</b> That is what both existing arms do, and a new arm should match "
               "them unless it is forced not to. Specialised models fail in the ways real systems "
               "fail, which is what the metric has to cope with, and keeping the arms alike keeps "
               "them comparable.", "body"))
    F.append(P("<b>One candidate per reference.</b> Each study gets exactly one candidate report, "
               "and the systems are spread equally at random within each stratum. Our arms are 200 "
               "distinct studies with five systems at 40 each. Showing the same study to every "
               "model instead spends the annotation budget on correlated judgements of one "
               "reference; with a fixed budget, prefer reference diversity.", "body"))
    F.append(P("Released outputs count. Four of our five chest-CT systems are the authors' own "
               "published predictions rather than anything we ran, and one X-ray system likewise. "
               "Look for released test-split outputs before building an inference environment.",
               "body"))
    F.append(P("Fall back only if step 4 says the pool is inadequate", "h2"))
    F.append(P("In ascending order of distance from the default. Add the least you need, and "
               "record which strategy produced each candidate so it can be a stratum in the "
               "analysis.", "body"))
    F.append(table([
        ["Fallback", "What it gives you", "Reach for it when"],
        ["More specialised models", "Same failure modes, wider spread", "Any remain untried for "
         "the modality"],
        ["General-purpose model on rendered slices", "Fluent, confident, often wrong", "Specialised "
         "models are few or weak"],
        ["Controlled error injection", "Errors known by category, span and severity", "The top of "
         "the range is empty"],
        ["Retrieval of a similar case", "Fluent, correctly styled, plausibly wrong", "A principled "
         "floor is missing"],
    ], [44 * mm, 58 * mm, 63 * mm]))
    F.append(P("The last two are implemented in this repository and need no other install: "
               "<font face='Courier' size='8.5'>scripts/generate_fallbacks.py</font> (lexicons for "
               "ultrasound or any other modality go in a JSON file, see "
               "<font face='Courier' size='8.5'>reporttools/lexicons/</font>). Both derive from the "
               "reference text, so keep them as their own stratum in every analysis.", "body"))
    F.append(code([
        "python3 scripts/generate_fallbacks.py coverage --modality <m> --acquisition <a>",
        "python3 scripts/generate_fallbacks.py inject   --reports refs.csv --studies eval.txt \\",
        "        --modality <m> --acquisition <a> --out data/raw/<m>/candidates/injected.jsonl",
        "python3 scripts/generate_fallbacks.py retrieve --reports refs.csv --studies eval.txt \\",
        "        --donor-reports train.csv --out data/raw/<m>/candidates/retrieved.jsonl",
    ]))
    F.append(P("On brain MRI a general-purpose model beat every specialised one we tried, so the "
               "ordering above is about staying close to the other arms, not about which model is "
               "best. Three brain-MRI models bear this out: the best reached 0.31 median F1 "
               "against 0.36 for a general-purpose model.", "cap"))
    F.append(warn("A low claim-F1 can be the metric's fault, not the model's",
                  "One brain-MRI model scored 0.09 and looked like a total failure. It was not. On "
                  "the studies it summarised as \u201cstudy is unremarkable\u201d the reference was "
                  "genuinely normal and the judge marked its single claim CORRECT \u2014 but the "
                  "reference enumerates each normal structure separately, so a normal study parses "
                  "into 19-29 normality claims against the candidate's 1. F1 = 2x1/(19+1) = 0.10 "
                  "for a report that is right. Claim-level F1 systematically punishes correct, "
                  "concise normality reporting, and the penalty scales with how verbosely the "
                  "reference lists normal structures. Before dropping a model on a low median F1, "
                  "check the normal studies separately: look at correct/candidate_claims, not F1 "
                  "alone. The same caution applies to any arm whose references enumerate "
                  "normality, which is most CT and MR reporting."))
    F.append(P("Record for every candidate: study id, system, strategy, findings, impression, "
               "which image series were used, and a prompt version string. You will want all of it "
               "when a result looks odd.", "body"))

    # ---------------------------------------------------------------- step 4
    F.append(P("4. Check the spread before booking any clinician time", "h1"))
    F.append(P("Score every candidate against its reference with an automatic metric and look at "
               "the <b>distribution</b>, not the mean. This is the step that saves a wasted study.",
               "body"))
    F.append(P("Any claim-level metric will do for this check \u2014 for example RadMatch "
               "(github.com/raidium-med/RadMatch) or radmetric (github.com/jomoll/radmetric). "
               "Score one row per candidate and keep the per-report output.", "body"))
    F.append(table([
        ["Arm", "Median F1", "Reports with no correct claim", "Clearing 0.3"],
        ["Chest CT", "0.46", "0%", "92%"],
        ["Chest X-ray", "0.33", "8%", "65%"],
        ["Brain MRI", "0.23", "20%", "40%"],
    ], [45 * mm, 32 * mm, 54 * mm, 34 * mm]))
    F.append(P("CT and X-ray span a usable range with specialised models alone and need no "
               "fallback. Brain MRI does not reach the top of the scale, which is the only reason "
               "that arm supplements its models.", "cap"))
    F.append(P("<b>The decision rule.</b> If the pool has no empty bin and a few per cent of "
               "candidates clear the top of the scale, stop: four or five specialised models is "
               "the arm, exactly as in CT and X-ray. Add a fallback strategy only to fill a bin "
               "the models leave empty, and add the smallest number that fills it. A pool whose "
               "median sits near the floor, or where a large share of candidates contain no "
               "correct claim at all, cannot be fixed by adding more of the same kind of model.",
               "body"))
    F.append(warn("Pick a judge that is not also a candidate",
                  "Every model we had served locally was also in the candidate set, so it would "
                  "have graded its own reports. We used a text-only model instead: the judge reads "
                  "reference and candidate text, never images, so a text-only model qualifies and "
                  "cannot be one of the candidates."))
    F.append(warn("Disable thinking mode on a reasoning judge",
                  "A reasoning model returns its trace in the message body. Our claim extractor "
                  "then read 4 claims out of reports holding 29, and the parser silently scored "
                  "near zero. Send chat_template_kwargs {enable_thinking: false}, or use a model "
                  "that returns reasoning in a separate field, and always print the parse rate. "
                  "Not every server honours the switch: behind a LiteLLM gateway glm-5.3-flash "
                  "kept thinking and returned empty answers once its budget ran out, so raise the "
                  "answer budget instead (ANNOTATION_LLM_MAX_TOKENS for the bundled tools)."))

    # ---------------------------------------------------------------- step 5
    F.append(P("5. Stand up the app", "h1"))
    F.append(P("The app is a small Streamlit reader study, published so collaborators can drop in "
               "their own reference and candidate pairs. The modality, the questions and the data "
               "source are all pluggable; nothing below needs a fork.", "body"))
    F.append(code([
        "git clone https://github.com/jomoll/annotation_app.git && cd annotation_app",
        "pip install -r requirements.txt",
        "streamlit run app/app.py --server.address 127.0.0.1 --server.port 8750",
    ]))
    F.append(bullets([
        "With no pool present it runs on a bundled example so you can see the interface before "
        "building anything.",
        "First start seeds a host account <font face='Courier' size='8.5'>admin / admin</font>. "
        "<b>Change that password before the app is reachable by anyone else.</b>",
        "Readers sign up themselves. Only a salted password hash is stored, never the password.",
        "Each reader's ratings append to their own CSV under "
        "<font face='Courier' size='8.5'>data/annotations/</font>; the latest row per case wins, "
        "so a reader may revise an answer and nothing is overwritten.",
        "Expose it over an SSH tunnel rather than binding it to a public interface.",
    ]))
    F.append(P("The questionnaire lives in one file, "
               "<font face='Courier' size='8.5'>app/questions.py</font>. That is the file to edit "
               "for a different question set: each question carries its key, prompt, type and the "
               "per-score definitions the reader sees. Scales may differ in length between "
               "questions.", "body"))

    F.append(P("6. Build the pool and register the modality", "h1"))
    F.append(steps([
        "Add the modality to <font face='Courier' size='8.5'>config.MODALITIES</font>.",
        "Build the per-modality pool file, then merge all modalities into the single cases file "
        "the app serves. Case ids must be unique across pools.",
        "Precompute the claim links that drive hover highlighting, if your readers want it. Ours "
        "asked for it and it is on by default.",
        "Keep every pool file and all raw candidates out of version control.",
    ]))
    F.append(code([
        "# before generating: one system per study, normal/abnormal mix equal per system",
        "python3 scripts/assign_systems.py --references refs.csv --studies eval.txt \\",
        "        --systems modelA modelB injected retrieved --out data/raw/<m>/assignment.csv",
        "# after generating: references + candidate files -> the pool the app serves",
        "python3 scripts/build_pool.py --modality <m> --references refs.csv \\",
        "        --candidates modelA=a.jsonl modelB=b.jsonl injected=... retrieved=... \\",
        "        --assignment data/raw/<m>/assignment.csv --out data/cases_<m>.json",
        "python3 scripts/merge_pools.py <m>=data/cases_<m>.json --out data/cases.json",
        "python3 scripts/precompute_links.py data/cases.json        # optional, needs an LLM",
    ]))
    F.append(P("References may be sectioned (findings + impression) or one block of text, as "
               "pathology reports usually are; both are shown to the reader as written. "
               "build_pool.py copies only report text into the pool \u2014 the injection ground truth "
               "and retrieval's donor ids stay in the candidate files, out of the readers' browsers.",
               "body"))

    # ---------------------------------------------------------------- step 6
    F.append(P("7. Set the reader allocation", "h1"))
    F.append(P("Two readers per modality, each rating enough studies that the overlap reaches the "
               "share you want. For a 200-study pool and 20% overlap: 120 studies each, giving "
               "240 slots and therefore 40 double-rated.", "body"))
    F.append(code([
        "RATERS_PER_MODALITY = 2",
        "STUDIES_PER_RATER   = 120      # 2 x 120 - 200 = 40 shared",
        "TARGET_COVERAGE     = 2",
        "OVERLAP_FIRST       = True     # shared block at the START of each list",
    ]))
    F.append(warn("Never reorder a reader's list once they have started",
                  "The app resumes a new session from the stored case id, but an open session "
                  "tracks an integer position. Reordering moves the reader somewhere else in the "
                  "list. One of our readers skipped the 40-case shared block twice this way. Put "
                  "the shared block first at list creation and never touch the order again."))

    # ---------------------------------------------------------------- step 7
    F.append(P("8. Onboard the readers", "h1"))
    F.append(bullets([
        "Put the question definitions where the reader sees them on every case. A verbal briefing "
        "does not hold: after one in-person clarification our reader's scores corrected for about "
        "half a day and then drifted back past where they started.",
        "State explicitly what each question is <i>not</i> about. Our completeness question has "
        "never reached acceptable agreement in any arm (Spearman 0.11 to 0.25 across three "
        "measurements and two modalities) because readers disagree on whether it means coverage of "
        "the reference or presence of the clinically relevant information.",
        "Check for inverted binaries in the first few cases. One reader answered the safety "
        "question with the opposite polarity for 15 cases before anyone noticed.",
    ]))

    # ---------------------------------------------------------------- step 8
    F.append(P("9. Monitor while the study runs", "h1"))
    F.append(table([
        ["Check", "How", "What is wrong if it fails"],
        ["Binary polarity", "Mean overall for Yes versus No on each binary, per reader",
         "Reader has the question inverted"],
        ["Reader offset", "Mean difference on the shared block",
         "Systematic generosity; correctable now, only statistically later"],
        ["Agreement", "Spearman and weighted kappa on the shared block",
         "Question wording, if rank agreement is fine but kappa is not"],
        ["Blank submissions", "Rows with some answers missing",
         "Ungenerated candidate, or the reader navigated away"],
    ], [34 * mm, 68 * mm, 63 * mm]))
    F.append(P("Expect a persistent offset between readers even when they rank cases together. In "
               "our X-ray pair, rank agreement on overall is 0.72 while one reader sits a full "
               "point above the other on every scale.", "cap"))

    # ---------------------------------------------------------------- step 9
    F.append(P("10. Analyse", "h1"))
    F.append(bullets([
        "Report arms separately. Never pool a correlation across modalities: our GREEN-versus-"
        "reader correlation was 0.44 on CT and 0.77 on X-ray, and the gap survives matching the "
        "sample size.",
        "Control for normal versus abnormal. It is the largest single confound in every arm we "
        "have measured.",
        "Quote the human ceiling alongside any metric correlation. A metric at 0.44 against readers "
        "who agree with each other at 0.73 is a different claim from one at 0.44 against readers "
        "who agree at 0.50.",
        "If candidates came from more than one strategy, treat strategy as a stratum. Injected and "
        "retrieved reports derive from reference text rather than images and cannot be presented "
        "as model output.",
    ]))

    # ---------------------------------------------------------------- checklist
    F.append(P("Checklist before the first reader logs in", "h1"))
    F.append(bullets([
        "Held-out split confirmed, and the strata you planned actually exist in it",
        "One study per patient; prior-referencing reports excluded and logged",
        "Normal share capped deliberately",
        "Four or five specialised models, one candidate per reference, balanced within strata",
        "Spread checked on the distribution, with a judge that is not a candidate",
        "Fallback strategies added only where the models left a bin empty, and recorded as a stratum",
        "Shared block first in both readers' lists",
        "Question definitions visible on every case, with the common misreading named",
        "App running, admin password changed, readers able to sign up",
        "Raw candidates and pool files git-ignored",
    ]))

    doc.build(F)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
