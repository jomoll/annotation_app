#!/usr/bin/env python3
"""Streamlit reader-study app: rate model-generated radiology reports against
the original (reference) report.

Raters create their own account, get a fixed set of studies assigned, and rate
every candidate report of each study on the questionnaire in questions.py.
Answers autosave on every navigation and are prefilled on return.

Run (bind to localhost and reach it through an SSH tunnel if the data is sensitive):
    streamlit run app/app.py --server.address 127.0.0.1 --server.port 8750
"""
from __future__ import annotations

import html
from collections import Counter

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import assignment
import auth
import progress
import storage
from cases_data import CASES, CASES_BY_ID, CANDIDATE_IDS, POOL_META, STUDY_IDS, USING_EXAMPLE
from config import (APP_SUBTITLE, APP_TITLE, HIGHLIGHT_DEFAULT, MODALITY_LABEL, PANEL_HEIGHT_PX, POSITIONS,
                    QUESTIONS_BESIDE_REPORTS, STUDIES_PER_RATER)
from questions import QUESTIONS, REQUIRED_KEYS, likert_values
from theme import THEME_CSS, badge


# ============================================================
# Small helpers
# ============================================================
def _esc(text: str) -> str:
    return html.escape(text or "")


def _brand():
    st.markdown(
        '<div class="brand-row"><div class="brand-logo">RR</div><div>'
        f'<p class="brand-title">{_esc(APP_TITLE)}</p>'
        f'<div class="brand-sub">{_esc(APP_SUBTITLE)} · {_esc(MODALITY_LABEL)}</div>'
        "</div></div>",
        unsafe_allow_html=True,
    )


def _widget_key(qkey: str, case_id: str) -> str:
    return f"q_{qkey}_{case_id}"


def _is_complete(rating: dict) -> bool:
    return all(str(rating.get(k, "")).strip() != "" for k in REQUIRED_KEYS)


# ============================================================
# Auth screen
# ============================================================
def auth_screen():
    _brand()
    st.caption("Research prototype · not for clinical use · report text stays on this server.")

    tab_login, tab_signup = st.tabs(["Login", "Create account"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email (or `admin` for the host account)")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary")
        if submitted:
            ok, name_or_err, role = auth.check_login(email, password)
            if ok:
                st.session_state["auth"] = {"email": email.strip().lower(), "name": name_or_err, "role": role}
                st.rerun()
            else:
                st.error(name_or_err)

    with tab_signup:
        st.caption("Create your own account — we'll assign you a fixed set of cases on your first visit "
                   "to the Cases tab. You can log out and continue any time; your answers are saved.")
        with st.form("signup_form"):
            name = st.text_input("Name")
            email_su = st.text_input("Email", key="signup_email")
            position = st.selectbox("Position (optional)", POSITIONS, index=None, placeholder="Select…")
            years = st.text_input("Years of experience reading this modality (optional)", key="signup_years")
            password_su = st.text_input(f"Password (min. {auth.MIN_PASSWORD_LEN} characters)", type="password", key="signup_pw")
            password_su2 = st.text_input("Repeat password", type="password", key="signup_pw2")
            submitted_su = st.form_submit_button("Create account", type="primary")
        if submitted_su:
            if password_su != password_su2:
                st.error("The two passwords don't match.")
            else:
                ok, msg = auth.create_account(email_su, name, password_su, position or "", years.strip())
                (st.success if ok else st.error)(msg)


# ============================================================
# Overview page — a short explanation, not a slide deck
# ============================================================
def overview_page():
    n_cands = len(CANDIDATE_IDS)
    with st.container(border=True):
        st.subheader("What this is")
        st.markdown(
            f"Automatically generated **{MODALITY_LABEL}** reports, compared against the original report "
            "written by the radiologist. Every case shows the **reference (original) report on the left** and "
            "one **candidate report on the right**. You answer six short questions about the candidate, "
            "always judging it *relative to the reference* — the reference is the ground truth here, even if "
            "you would have worded it differently yourself."
        )
        per_study = max(Counter(c["study_id"] for c in CASES).values()) if CASES else 1
        if per_study > 1:
            layout = (f"- You are assigned **{STUDIES_PER_RATER} studies**. For each study you rate all its "
                      f"{per_study} candidates back to back, so the reference only has to be read once.\n")
        else:
            layout = (f"- You are assigned **{STUDIES_PER_RATER} studies**, each with exactly one candidate report — "
                      "every case is a fresh reference.\n")
        st.markdown(
            f"- The candidates come from **{n_cands} different systems**; which system produced a given "
            "candidate is hidden and the order is shuffled.\n"
            + layout +
            "- Answers are **saved automatically** whenever you move to another case. You can stop at any "
            "time and continue later; the app resumes where you left off.\n"
            "- Cases you have completed are marked with ✓ in the case selector."
        )

    with st.container(border=True):
        st.subheader("The six questions")
        cols = st.columns(2)
        for i, q in enumerate(QUESTIONS):
            if q["type"] == "likert":
                vals = likert_values(q)
                lo, hi = q["scale"][vals[0]][0], q["scale"][vals[-1]][0]
                scale_txt = f"{vals[0]}–{vals[-1]} scale · {vals[0]} = {lo} · {vals[-1]} = {hi}"
            else:
                scale_txt = " / ".join(q["options"])
            note = f' · {q["note"]}' if q.get("note") else ""
            with cols[i % 2]:
                st.markdown(
                    f'<div class="q-card"><div class="qn">Question {i+1}</div>'
                    f'<div class="qt">{_esc(q["title"])}</div>'
                    f'<div class="qp">{_esc(q["prompt"])}</div>'
                    f'<div class="qs">{_esc(scale_txt)}{_esc(note)}</div></div>',
                    unsafe_allow_html=True,
                )
        with st.expander("Full per-score definitions"):
            for i, q in enumerate(QUESTIONS):
                if q["type"] != "likert":
                    continue
                st.markdown(f"**{i+1}. {q['title']}**")
                _render_scale_definitions(q)

    if st.button("Go to cases →", type="primary"):
        st.session_state["page"] = "cases"
        st.rerun()


def _render_scale_definitions(q: dict):
    lines = []
    for v in likert_values(q):
        short, long = q["scale"][v]
        long_html = "<br>".join(_esc(x) for x in long.split("\n") if x.strip())
        desc = f"<b>{v} — {_esc(short)}</b>" + (f": {long_html}" if long_html else "")
        lines.append(f'<div class="scale-def">{desc}</div>')
    st.markdown("".join(lines), unsafe_allow_html=True)


# ============================================================
# Cases page
# ============================================================
# ---- hover highlighting of linked sentences ---------------------------------
def _highlights_on() -> bool:
    return bool(st.session_state.get("hl_on", HIGHLIGHT_DEFAULT))


def _linked_html(text: str, spans: list[tuple[int, int, list[int]]]) -> str:
    """Escape `text`, wrapping each (start, end, link ids) span in a hoverable <span>."""
    out, pos = [], 0
    for start, end, ids in sorted(spans):
        if start < pos:
            continue
        out.append(_esc(text[pos:start]))
        out.append(f'<span class="lnk" data-l="{",".join(str(i) for i in sorted(set(ids)))}">{_esc(text[start:end])}</span>')
        pos = end
    out.append(_esc(text[pos:]))
    return "".join(out)


def _link_spans(case: dict, side: str) -> dict[str, list[tuple[int, int, list[int]]]]:
    """{section: [(start, end, [link ids])]} for one side ("ref" or "cand") of the case's links."""
    by_section: dict[str, dict[tuple[int, int], list[int]]] = {}
    for i, link in enumerate(case.get("links") or []):
        loc = link[side]
        by_section.setdefault(loc["section"], {}).setdefault((loc["start"], loc["end"]), []).append(i)
    return {sec: [(a, b, ids) for (a, b), ids in d.items()] for sec, d in by_section.items()}


def _report_text(text: str, spans: list[tuple[int, int, list[int]]] | None):
    body = _linked_html(text, spans) if spans else _esc(text)
    st.markdown(f'<div class="report-text">{body}</div>', unsafe_allow_html=True)


_LINK_JS = """<script>
(function(){
  const P = window.parent; const D = P.document;
  if (P.__lnkBound) return; P.__lnkBound = true;
  const ids = el => (el.dataset.l || '').split(',');
  function clear(){ D.querySelectorAll('.lnk.hl').forEach(x => x.classList.remove('hl')); }
  D.addEventListener('mouseover', e => {
    const t = e.target.closest ? e.target.closest('.lnk') : null;
    clear(); if (!t) return;
    const mine = ids(t); const box = t.closest('[data-testid="stVerticalBlockBorderWrapper"]');
    let first = null;
    D.querySelectorAll('.lnk').forEach(x => {
      if (ids(x).some(i => mine.includes(i))) {
        x.classList.add('hl');
        if (!first && x !== t && x.closest('[data-testid="stVerticalBlockBorderWrapper"]') !== box) first = x;
      }
    });
    if (first) first.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  });
  D.addEventListener('mouseout', e => {
    const t = e.target.closest ? e.target.closest('.lnk') : null;
    const to = e.relatedTarget && e.relatedTarget.closest ? e.relatedTarget.closest('.lnk') : null;
    if (t && !to) clear();
  });
})();
</script>"""


def _inject_link_js():
    # Real JS only runs inside components.html's iframe; same-origin, so it can reach the
    # parent page and attach one delegated hover listener there (guarded against reruns).
    components.html(_LINK_JS, height=0)


def _render_reference(ref: dict, header: bool = True, spans: dict | None = None):
    if header:
        st.markdown('<div class="cmp-head gold">Reference report (original)</div>', unsafe_allow_html=True)
    meta_bits = []
    ci = (ref.get("clinical_information") or "").strip()
    if ci and ci.lower() not in {"not given.", "not given", ""}:
        meta_bits.append(f"<b>Clinical information:</b> {_esc(ci)}")
    tech = (ref.get("technique") or "").strip()
    if tech:
        meta_bits.append(f"<b>Technique:</b> {_esc(tech)}")
    if meta_bits:
        st.markdown('<div class="report-meta">' + "<br>".join(meta_bits) + "</div>", unsafe_allow_html=True)
    spans = spans or {}
    if ref.get("findings"):
        st.markdown('<div class="report-label">Findings</div>', unsafe_allow_html=True)
        _report_text(ref["findings"], spans.get("findings"))
    if ref.get("impression"):
        st.markdown('<div class="report-label">Impression</div>', unsafe_allow_html=True)
        _report_text(ref["impression"], spans.get("impression"))


def _candidate_title(pos_in_study: int, n_in_study: int) -> str:
    suffix = f" · {pos_in_study} of {n_in_study} for this study" if n_in_study > 1 else ""
    return f"Candidate report{suffix}"


def _render_candidate(cand: dict, pos_in_study: int, n_in_study: int, header: bool = True, spans: dict | None = None):
    if header:
        st.markdown(f'<div class="cmp-head sys">{_candidate_title(pos_in_study, n_in_study)}</div>',
                    unsafe_allow_html=True)
    text = (cand.get("text") or "").strip()
    if not text:
        st.markdown('<div class="report-text"><i>(empty candidate report)</i></div>', unsafe_allow_html=True)
        return
    spans = spans or {}
    if cand.get("findings") or cand.get("impression"):
        # structured candidate — same layout as the reference
        if cand.get("findings"):
            st.markdown('<div class="report-label">Findings</div>', unsafe_allow_html=True)
            _report_text(cand["findings"], spans.get("findings"))
        if cand.get("impression"):
            st.markdown('<div class="report-label">Impression</div>', unsafe_allow_html=True)
            _report_text(cand["impression"], spans.get("impression"))
    else:
        st.markdown('<div class="report-label">Report</div>', unsafe_allow_html=True)
        _report_text(cand["text"], spans.get("text"))


def _render_question(i: int, q: dict, case_id: str, existing: dict):
    st.markdown(f"**{i+1}. {q['title']}**")
    st.caption(q["prompt"] + (f"  \n_{q['note']}_" if q.get("note") else ""))
    key = _widget_key(q["key"], case_id)
    saved = str(existing.get(q["key"], "")).strip()
    if q["type"] == "likert":
        opts = likert_values(q)
        default = opts.index(int(saved)) if saved.isdigit() and int(saved) in opts else None

        def fmt(v, _q=q):
            short = _q["scale"][v][0]
            return f"{v} · {short}" if short else str(v)

        st.radio(q["title"], opts, index=default, horizontal=True, format_func=fmt,
                 key=key, label_visibility="collapsed")
        with st.expander("Score definitions", expanded=False):
            _render_scale_definitions(q)
    else:
        opts = q["options"]
        default = opts.index(saved) if saved in opts else None
        st.radio(q["title"], opts, index=default, horizontal=True, key=key, label_visibility="collapsed")


def cases_page(rater_slug: str):
    if not CASES:
        st.info("No cases loaded — build data/cases.json first (see README).")
        return
    my_cases = assignment.cases_for(rater_slug, CASES)
    if not my_cases:
        st.info("No cases could be assigned to you. Please contact the study lead.")
        return
    case_ids = [c["id"] for c in my_cases]
    ratings = storage.load_ratings(rater_slug)

    def _gather_rating(cid: str) -> dict:
        """Current widget values for case `cid`, read from session_state by key — works even
        before those widgets are rendered again in this run (e.g. right before navigating)."""
        ss = st.session_state
        out = {}
        for q in QUESTIONS:
            v = ss.get(_widget_key(q["key"], cid))
            out[q["key"]] = "" if v is None else str(v)
        out["comment"] = ss.get(_widget_key("comment", cid)) or ""
        out["highlights_on"] = "on" if _highlights_on() else "off"
        return out

    def _autosave_if_needed(cid: str):
        # No separate save button: every navigation saves what is currently filled in. Skipped
        # when nothing was touched, so merely browsing past a case doesn't create a blank row.
        rating = _gather_rating(cid)
        if any(v.strip() for k, v in rating.items() if k != "highlights_on"):
            storage.save_rating(rater_slug, CASES_BY_ID[cid], rating)

    if "case_idx" not in st.session_state:
        last = progress.load().get(rater_slug)
        st.session_state["case_idx"] = case_ids.index(last) if last in case_ids else 0
    idx = max(0, min(st.session_state["case_idx"], len(my_cases) - 1))
    st.session_state["case_idx"] = idx
    case = my_cases[idx]
    progress.set_last_case(rater_slug, case["id"])

    # position of this candidate within its study, for the header
    siblings = [c["id"] for c in my_cases if c["study_id"] == case["study_id"]]
    pos_in_study, n_in_study = siblings.index(case["id"]) + 1, len(siblings)
    study_no = [c["study_id"] for c in my_cases].index(case["study_id"])
    study_no = len(dict.fromkeys(c["study_id"] for c in my_cases[: study_no + 1]))
    n_studies = len(dict.fromkeys(c["study_id"] for c in my_cases))

    def _go(new_idx: int):
        _autosave_if_needed(case["id"])
        st.session_state["case_idx"] = new_idx
        st.rerun()

    n_done = sum(1 for cid in case_ids if _is_complete(ratings.get(cid, {})))

    def _label(i, c):
        sibs = [s["id"] for s in my_cases if s["study_id"] == c["study_id"]]
        done = "✓ " if _is_complete(ratings.get(c["id"], {})) else ""
        cand = f" · candidate {sibs.index(c['id']) + 1}" if len(sibs) > 1 else ""
        return f"{done}Case {i+1} of {len(my_cases)} · study {c['study_id']}{cand}"

    labels = [_label(i, c) for i, c in enumerate(my_cases)]

    def _on_select(key: str, current_id: str):
        # Runs before the rerun when the rater picks a case in the dropdown: save what is filled
        # in for the case they are leaving, then jump. (A plain value comparison after rendering
        # cannot tell a user pick from the widget's stale state after Next/Previous.)
        chosen = st.session_state.get(key)
        if chosen in labels:
            _autosave_if_needed(current_id)
            st.session_state["case_idx"] = labels.index(chosen)

    def _nav_row(pos: str):
        # one compact row: previous · case selector · next · progress — same row top and bottom
        c_prev, c_sel, c_next, c_done = st.columns([1.2, 4, 1.2, 1.1], vertical_alignment="center")
        if c_prev.button("← Save & Previous", disabled=idx == 0, key=f"nav_prev_{pos}", width="stretch"):
            _go(idx - 1)
        key = f"case_select_{pos}"
        st.session_state[key] = labels[idx]  # keep the dropdown in step with the current case
        c_sel.selectbox("Case", labels, key=key, label_visibility="collapsed",
                        on_change=_on_select, args=(key, case["id"]))
        if idx < len(my_cases) - 1:
            if c_next.button("Save & Next →", key=f"nav_next_{pos}", type="primary", width="stretch"):
                _go(idx + 1)
        elif c_next.button("Save", key=f"nav_save_{pos}", type="primary", width="stretch"):
            _autosave_if_needed(case["id"])
            st.toast("Saved — that was your last case. Thank you!")
        c_done.markdown(badge(f"Completed {n_done} / {len(my_cases)}", "green" if n_done == len(my_cases) else "gray"),
                        unsafe_allow_html=True)

    _nav_row("top")
    where = f"Study {study_no} of {n_studies}" + (f" · candidate {pos_in_study} of {n_in_study} for this study" if n_in_study > 1 else "")
    guidance = f"{where} · judge the candidate relative to the reference."

    existing = ratings.get(case["id"], {})

    def _questions():
        st.caption(guidance)
        for i, q in enumerate(QUESTIONS):
            _render_question(i, q, case["id"], existing)
        st.text_area("Comment (optional)", value=existing.get("comment", ""),
                     placeholder="Anything notable — e.g. the specific error you saw…",
                     key=_widget_key("comment", case["id"]))

    use_links = _highlights_on() and bool(case.get("links"))
    ref_spans = _link_spans(case, "ref") if use_links else None
    cand_spans = _link_spans(case, "cand") if use_links else None
    if use_links:
        _inject_link_js()

    if QUESTIONS_BESIDE_REPORTS:
        # Three scrollable boxes side by side: the reports stay in view while the rater scrolls
        # through the questions. Headers sit above the boxes so they never scroll away.
        st.markdown(f"<style>:root{{--panel-h:{int(PANEL_HEIGHT_PX)}px}}</style>", unsafe_allow_html=True)
        col_ref, col_cand, col_q = st.columns([1.15, 1.15, 1])
        with col_ref:
            st.markdown('<div class="cmp-head gold standalone">Reference report (original)</div>', unsafe_allow_html=True)
            with st.container(border=True, height=PANEL_HEIGHT_PX, key="panel_ref"):
                _render_reference(case["reference"], header=False, spans=ref_spans)
        with col_cand:
            st.markdown(f'<div class="cmp-head sys standalone">{_candidate_title(pos_in_study, n_in_study)}</div>',
                        unsafe_allow_html=True)
            with st.container(border=True, height=PANEL_HEIGHT_PX, key="panel_cand"):
                _render_candidate(case["candidate"], pos_in_study, n_in_study, header=False, spans=cand_spans)
        with col_q:
            st.markdown(f'<div class="cmp-head standalone q">Your rating · case {idx+1} of {len(my_cases)}</div>', unsafe_allow_html=True)
            with st.container(border=True, height=PANEL_HEIGHT_PX, key="panel_q"):
                _questions()
    else:
        col_ref, col_cand = st.columns(2)
        with col_ref:
            with st.container(border=True):
                _render_reference(case["reference"], spans=ref_spans)
        with col_cand:
            with st.container(border=True):
                _render_candidate(case["candidate"], pos_in_study, n_in_study, spans=cand_spans)
        with st.container(border=True):
            _questions()

    _nav_row("bottom")


# ============================================================
# Admin: all ratings
# ============================================================
def admin_ratings_page():
    st.subheader("Ratings overview")
    st.caption("Everything saved so far, joined with the (hidden) candidate identity. Latest rating per rater "
               "and case; the full append-only history is in data/annotations/.")

    df = storage.all_ratings_latest()
    if df.empty:
        st.info("No ratings saved yet.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Ratings", len(df))
        c2.metric("Raters", df["rater"].nunique())
        c3.metric("Studies covered", df["study_id"].nunique())

        st.markdown("**Progress per rater**")
        rows = []
        for slug in sorted(df["rater"].unique()):
            assigned = assignment.cases_for(slug, CASES) if CASES else []
            mine = df[df["rater"] == slug]
            n_complete = sum(1 for _, r in mine.iterrows() if _is_complete(r.to_dict()))
            rows.append({"rater": slug, "assigned": len(assigned), "saved": len(mine), "complete": n_complete})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        st.markdown("**Mean score per candidate system**")
        likert_keys = [q["key"] for q in QUESTIONS if q["type"] == "likert"]
        num = df.copy()
        for k in likert_keys:
            num[k] = pd.to_numeric(num[k], errors="coerce")
        agg = num.groupby("candidate_id")[likert_keys].mean().round(2)
        agg["n"] = num.groupby("candidate_id").size()
        for q in QUESTIONS:
            if q["type"] == "binary":
                yes = num.assign(_y=(num[q["key"]] == q["options"][0]).astype(float))
                yes.loc[num[q["key"]].eq(""), "_y"] = float("nan")
                agg[f"{q['key']}={q['options'][0]} (%)"] = (yes.groupby("candidate_id")["_y"].mean() * 100).round(0)
        st.dataframe(agg, width="stretch")

        st.markdown("**All ratings**")
        st.dataframe(df.sort_values(["study_id", "candidate_id", "rater"]), width="stretch", hide_index=True)
        st.download_button("Download latest ratings (CSV)", df.to_csv(index=False).encode("utf-8"),
                           file_name="ratings_latest.csv", mime="text/csv")
        st.download_button("Download full history (CSV)", storage.all_ratings_history().to_csv(index=False).encode("utf-8"),
                           file_name="ratings_history.csv", mime="text/csv")

    if CASES:
        st.markdown("**Study coverage** (raters assigned per study)")
        cov = assignment.coverage_table(CASES)
        cov_df = pd.DataFrame({"study_id": list(cov), "raters_assigned": list(cov.values())})
        st.caption(f"{len(cov_df)} studies · {len(CASES)} cases · candidates: {', '.join(CANDIDATE_IDS)}")
        st.dataframe(cov_df.sort_values(["raters_assigned", "study_id"]), width="stretch", hide_index=True)
    if POOL_META:
        with st.expander("Pool metadata"):
            st.json(POOL_META)


# ============================================================
# Main
# ============================================================
def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide", page_icon="🩻")
    st.markdown(THEME_CSS, unsafe_allow_html=True)

    if USING_EXAMPLE:
        st.markdown('<div class="notice-banner">Running on the bundled example cases — build data/cases.json '
                    'to load your real pool (see README).</div>', unsafe_allow_html=True)

    auth.ensure_default_admin()

    if "auth" not in st.session_state:
        st.session_state["auth"] = None
    if not st.session_state["auth"]:
        auth_screen()
        return

    email = st.session_state["auth"]["email"]
    name = st.session_state["auth"]["name"]
    role = st.session_state["auth"].get("role", "rater")
    slug = auth.user_slug(email)
    is_admin = role == "admin"

    page_labels = {"overview": "Overview", "cases": "Cases", "admin_ratings": "Admin: Ratings"}
    nav_options = ["Overview", "Cases"] + (["Admin: Ratings"] if is_admin else [])
    if "page" not in st.session_state:
        st.session_state["page"] = "overview"
    current_label = page_labels.get(st.session_state["page"], "Overview")

    nav_col, user_col = st.columns([3, 1])
    with nav_col:
        page = st.radio("Navigation", nav_options, horizontal=True, label_visibility="collapsed",
                        index=nav_options.index(current_label) if current_label in nav_options else 0)
        st.session_state["page"] = {v: k for k, v in page_labels.items()}[page]
    with user_col:
        u1, u2 = st.columns([2, 1])
        u1.markdown(f"**{_esc(name)}**" + (" · 🔑 admin" if is_admin else ""))
        if u2.button("Logout"):
            st.session_state["auth"] = None
            st.rerun()
        if "hl_on" not in st.session_state:
            st.session_state["hl_on"] = HIGHLIGHT_DEFAULT
        st.toggle("Highlight matching findings", key="hl_on",
                  help="Hover a sentence in one report to light up the corresponding sentence(s) in the other. "
                       "Matches were precomputed automatically and can be wrong or missing — they are a "
                       "navigation aid, not a judgment.")
    st.caption(f"{APP_TITLE} · research prototype · not for clinical use")
    st.divider()

    if st.session_state["page"] == "overview":
        overview_page()
    elif st.session_state["page"] == "cases":
        cases_page(slug)
    elif st.session_state["page"] == "admin_ratings" and is_admin:
        admin_ratings_page()
    else:
        st.session_state["page"] = "overview"
        st.rerun()


if __name__ == "__main__":
    main()
