"""Shared visual design system — CSS custom properties and a small component
language (panels, pills, gold-vs-cyan comparison headers) targeted at
Streamlit's stable data-testid hooks. Presentation only: no rating logic here.
Inject once via st.markdown(THEME_CSS, unsafe_allow_html=True) in main()."""

THEME_CSS = """
<style>
:root{
  --bg:#f4f7f9; --panel:#ffffff; --panel-2:#f8fafb; --border:#dfe6ea;
  --text:#132126; --text-dim:#374151; --text-faint:#4b5563;
  --accent:#0891b2; --accent-dark:#0e7490; --accent-bg:#ecfeff; --accent-bg-2:#cffafe;
  --gold:#f5f0e6; --gold-border:#e2d5b8; --gold-text:#7a5a12;
  --green:#15803d; --green-bg:#eafaf0; --green-border:#bfe8cf;
  --amber:#b45309; --amber-bg:#fff6e8; --amber-border:#f2ddb0;
  --red:#b91c1c; --red-bg:#fdeeee; --red-border:#f2c6c6;
  --gray:#5b6c74; --gray-bg:#eef2f4; --gray-border:#dbe3e7;
  --radius:12px;
  --shadow:0 1px 2px rgba(20,40,50,.04), 0 6px 20px rgba(20,40,50,.06);
}
/* Light-only by design (config.toml pins base="light"). */

/* ---- base ---- */
[data-testid="stAppViewContainer"], [data-testid="stHeader"]{ background:var(--bg); }
/* No Streamlit toolbar (Deploy / menu) and a tight top margin: on the Cases page every pixel of
   height goes to the three side-by-side boxes. */
[data-testid="stHeader"]{ display:none; }
[data-testid="stMainBlockContainer"], .block-container{ padding-top:1.2rem !important; padding-bottom:1rem !important; }
hr{ margin:.6rem 0 !important; }
html, body, [class*="css"]{ font-family:Inter,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
h1,h2,h3,h4,h5,h6{ letter-spacing:-.01em; }
/* Captions carry the rating instructions — treat them as body copy, not decoration. */
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p,
[data-testid="stCaptionContainer"] div{
  color:#1c272e !important; font-size:15px !important; line-height:1.6 !important;
}

/* ---- panels: st.container(border=True) ---- */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]){
  border-radius:var(--radius) !important; border-color:var(--border) !important;
  box-shadow:var(--shadow); background:var(--panel);
}

/* ---- buttons ---- */
.stButton > button{ border-radius:9px; font-weight:560; border-color:var(--border); }
.stButton > button:hover{ border-color:var(--accent); color:var(--accent-dark); }
.stButton > button[kind="primary"]{ background:var(--accent); border-color:var(--accent); }
.stButton > button[kind="primary"]:hover{ background:var(--accent-dark); border-color:var(--accent-dark); }

/* ---- metrics ---- */
[data-testid="stMetric"]{
  background:var(--panel-2); border:1px solid var(--border); border-radius:10px; padding:10px 14px;
}
[data-testid="stMetricLabel"]{ color:var(--text-faint); font-size:11.5px; text-transform:uppercase; letter-spacing:.05em; }

[data-testid="stExpander"]{ border-radius:10px !important; border-color:var(--border) !important; }
[data-testid="stTextArea"] textarea{ border-radius:9px; }

/* ---- radio groups as pill/button tiles (rating scales) ---- */
[data-testid="stRadio"] > div[role="radiogroup"]{ gap:6px; }
[data-testid="stRadio"] label{
  border:1px solid var(--border); background:var(--panel-2); border-radius:8px;
  padding:8px 14px !important; margin:0 !important; transition:.12s border-color, .12s background;
  font-size:15px !important; line-height:1.5 !important;
}
[data-testid="stRadio"] label p{ font-size:15px !important; line-height:1.5 !important; }
[data-testid="stRadio"] label:has(input:checked){
  border-color:var(--accent); background:var(--accent-bg); font-weight:650;
}
[data-testid="stRadio"] label:hover{ border-color:var(--accent); }

/* ---- brand header ---- */
.brand-row{ display:flex; align-items:center; gap:10px; margin-bottom:6px; }
.brand-logo{
  width:34px; height:34px; border-radius:9px; flex-shrink:0;
  background:linear-gradient(135deg,var(--accent),var(--accent-dark));
  display:flex; align-items:center; justify-content:center; color:#fff; font-weight:700; font-size:14px;
}
.brand-title{ font-size:17px; font-weight:650; color:var(--text); margin:0; }
.brand-sub{ font-size:13px; color:var(--text-faint); margin-top:1px; }

/* ---- notice banner ---- */
.notice-banner{
  background:var(--amber-bg); color:var(--amber); border:1px solid var(--amber-border); font-size:13px;
  text-align:center; padding:6px 12px; border-radius:8px; margin-bottom:14px;
}

/* ---- badges ---- */
.badge{
  display:inline-flex; align-items:center; gap:5px; border-radius:7px; padding:3px 9px;
  font-size:12px; font-weight:650; border:1px solid; white-space:nowrap;
}
.badge.green{ color:var(--green); background:var(--green-bg); border-color:var(--green-border); }
.badge.amber{ color:var(--amber); background:var(--amber-bg); border-color:var(--amber-border); }
.badge.red{ color:var(--red); background:var(--red-bg); border-color:var(--red-border); }
.badge.gray{ color:var(--gray); background:var(--gray-bg); border-color:var(--gray-border); }
.badge.accent{ color:var(--accent-dark); background:var(--accent-bg); border-color:var(--accent-bg-2); }

/* ---- question cards on the overview page ---- */
.q-card{ border:1px solid var(--border); background:var(--panel-2); border-radius:10px; padding:13px 15px; height:100%; margin-bottom:10px; }
.q-card .qn{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--accent-dark); }
.q-card .qt{ font-size:14.5px; font-weight:700; color:var(--text); margin:2px 0 4px; }
.q-card .qp{ font-size:13px; color:var(--text-dim); line-height:1.5; }
.q-card .qs{ font-size:12px; color:var(--text-faint); margin-top:6px; }

/* ---- gold-vs-cyan comparison columns ---- */
.cmp-head{
  padding:9px 14px; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
  border-radius:var(--radius) var(--radius) 0 0; margin:-1px -1px 12px -1px;
}
/* header rendered OUTSIDE a scrollable box (side-by-side layout) */
.cmp-head.standalone{ margin:0 0 6px 0; border-radius:10px; border:1px solid transparent; }
/* scrollable boxes of the side-by-side layout: never taller than the viewport */
/* Streamlit puts the inline height on the border wrapper (wrapper > div > .st-key-…), so cap
   both the wrapper and the keyed block: the boxes end at the bottom of the viewport. */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > .st-key-panel_ref),
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > .st-key-panel_cand),
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > .st-key-panel_q),
.st-key-panel_ref, .st-key-panel_cand, .st-key-panel_q{
  height:min(var(--panel-h, 720px), calc(100vh - 300px)) !important;
  max-height:min(var(--panel-h, 720px), calc(100vh - 300px)) !important;
}
.cmp-head.gold{ background:var(--gold); color:var(--gold-text); border-bottom:1px solid var(--gold-border); }
.cmp-head.q{ background:var(--gray-bg); color:var(--text-dim); border-bottom:1px solid var(--gray-border); }
.cmp-head.sys{ background:var(--accent-bg); color:var(--accent-dark); border-bottom:1px solid var(--accent-bg-2); }
.report-label{ font-size:11.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--text-faint); margin:10px 0 2px; }
.report-text{ font-size:15px; line-height:1.65; color:var(--text); white-space:pre-wrap; }
.report-meta{ font-size:13px; color:var(--text-dim); line-height:1.5; }

/* ---- linked sentences (hover highlighting) ---- */
.lnk{ text-decoration:underline dotted rgba(8,145,178,.45); text-underline-offset:3px; text-decoration-thickness:1px; cursor:default; border-radius:3px; }
.lnk.hl{ background:var(--accent-bg-2); text-decoration-color:var(--accent); }

/* ---- scale definitions ---- */
.scale-def{ font-size:13.5px; color:var(--text-dim); line-height:1.55; margin:2px 0 0; }
.scale-def b{ color:var(--text); }
</style>
"""


def badge(label: str, color: str = "gray") -> str:
    return f'<span class="badge {color}">{label}</span>'
