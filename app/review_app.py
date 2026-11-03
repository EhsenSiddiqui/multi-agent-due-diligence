"""
Review screen for staff (demo).  Run from the project root:

    pip install streamlit pandas
    streamlit run app/review_app.py

It ONLY reads the stage files in results/<case_id>/ (and can trigger a pipeline run),
so it keeps working whatever implementation produced them. Reviewer decisions are
saved to results/<case_id>/07_review.json: a human record, separate from the
tool's own output, so nobody can confuse the two.

SIMPLIFIED: no login, single reviewer, local files.
BETTER: sign-in, several reviewers with comments, and export to the global ethical
checks register (guide 1.6).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a5dd.config import load_config  # noqa: E402
from a5dd.io_utils import ContractError, read_stage  # noqa: E402
from a5dd.pipeline import list_cases, run_case  # noqa: E402
from a5dd.schemas import (ORIGIN_LABELS, AssessmentManifest, EvidenceTable, QuestionAnswers,  # noqa: E402
                          ScoreResult, SearchResults)

st.set_page_config(page_title="A5 Due Diligence Review", layout="wide")
cfg = load_config()
results = cfg.path("results_dir")

# ---- Sidebar: choose and run a case ------------------------------------------------------
st.sidebar.title("A5 Due Diligence")
case_id = st.sidebar.selectbox("Case", list_cases(cfg))
st.sidebar.caption(f"search={cfg.provider('search')} · extractor={cfg.provider('extractor')} · "
                   f"judge={cfg.provider('judge')}")
if st.sidebar.button("Run pipeline", type="primary"):
    run_case(cfg, case_id, quiet=True)
    st.sidebar.success("Done")

try:
    answers = read_stage(results, case_id, "answers", QuestionAnswers)
    score = read_stage(results, case_id, "score", ScoreResult)
    evidence = read_stage(results, case_id, "evidence", EvidenceTable)
    manifest = read_stage(results, case_id, "manifest", AssessmentManifest)
    search = read_stage(results, case_id, "search", SearchResults)
except ContractError:
    st.info("No results yet for this case. Click 'Run pipeline' in the sidebar.")
    st.stop()

# ---- Summary ---------------------------------------------------------------------------
st.info("Draft prepared by the AI agent. The tool proposes; a person decides.")
st.caption("Prepared by: AI agent (automated draft, not yet reviewed). Case details: Water Care staff. "
           "Your decisions below are saved as Water Care staff decisions.")
c1, c2, c3 = st.columns(3)
c1.metric("Proposed risk level", score.risk_level.value.upper(), f"{score.risk_answers_count} risk answers")
c2.metric("Unclear answers", score.unclear_count)
c3.metric("If every unclear were a risk", score.risk_level_if_unclear_were_yes.value.upper())
st.write(f"**Suggested next step:** {score.suggested_next_step}  \n"
         f"**Suggested decision maker:** {score.suggested_decision_maker}")

# ---- Answers with reviewer overrides ----------------------------------------------------------
st.subheader("Core questions: accept or change each proposed call")
review_path = results / case_id / "07_review.json"
saved = json.loads(review_path.read_text()) if review_path.exists() else {}
decisions = {}
for a in answers.answers:
    with st.expander(f"{a.question_id} · proposed: {a.risk_call.value.upper()} · {a.question_text}"):
        st.write(a.response)
        c_for, c_against = st.columns(2)
        c_for.markdown(f"**Points to a risk**  \n{a.case_for_risk or '—'}")
        c_against.markdown(f"**Points against**  \n{a.case_against_risk or '—'}")
        if a.why_not_yes and a.risk_call.value != "yes":
            st.caption(f'Not "yes": {a.why_not_yes}')
        if a.why_not_no and a.risk_call.value != "no":
            st.caption(f'Not "no": {a.why_not_no}')
        if a.challenge:
            changed = f' Changed from "{a.call_before_challenge}".' if a.call_before_challenge else " Judge held its call."
            st.info(f'**Challenger** (argued for "{a.challenger_call}"): {a.challenge}  \n'
                    f"**Judge's reply:** {a.rebuttal or '-'}{changed}")
        st.caption(f"Evidence: {', '.join(a.evidence_ids) or 'none'}")
        for q in a.staff_questions:
            st.write(f"❓ {q}")
        options = ["yes", "no", "unclear"]
        prev = saved.get("calls", {}).get(a.question_id, a.risk_call.value)
        decisions[a.question_id] = st.radio("Reviewer's call", options, index=options.index(prev),
                                            key=f"call_{a.question_id}", horizontal=True)

note = st.text_area("Reviewer note", saved.get("note", ""))
reviewer = st.text_input("Reviewer name", saved.get("reviewer", ""))
# Guide 1.5: whoever reviews must declare any conflict of interest; if there is one, someone else reviews.
no_conflict = st.checkbox("I declare I have no conflict of interest with this organisation (guide 1.5)",
                          value=saved.get("conflict_of_interest_declared_none", False))
if st.button("Save review", disabled=not (no_conflict and reviewer.strip())):
    review_path.write_text(json.dumps({
        "provenance": {"origin": "water_care_staff", "prepared_by": reviewer or "Water Care reviewer",
                       "human_reviewed": True,
                       "note": "Decisions made by a person at Water Care after reviewing the agent's draft."},
        "case_id": case_id, "reviewer": reviewer, "note": note, "calls": decisions,
        "conflict_of_interest_declared_none": no_conflict,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    st.success(f"Saved to {review_path.relative_to(ROOT)}")
if not (no_conflict and reviewer.strip()):
    st.caption("Enter your name and confirm you have no conflict of interest to save. If you do have one, "
               "another member of staff must review this case.")

# ---- Evidence and gaps -------------------------------------------------------------------
st.subheader("Evidence table")
st.dataframe([{
    "ID": e.evidence_id, "Status": e.status.value, "Publisher": e.publisher,
    "Published by": ORIGIN_LABELS.get(e.origin, "unknown"), "Date": str(e.published_date),
    "Not safe to infer": " ".join(e.not_safe_to_infer),
    "Reliability": e.reliability.value, "Entity": e.entity_match.value,
    "Questions": ", ".join(e.question_ids), "Quote": e.quote} for e in evidence.items], width="stretch")

st.subheader("Agent log: follow-up research")
agent_rows = [{"Round": q.round, "Action": "Opened link" if q.action == "follow_link" else "Searched",
               "Target": q.query, "Found": ", ".join(q.hit_doc_ids) or "nothing new", "Why": q.reason}
              for q in search.queries if q.round > 0]
if agent_rows:
    st.dataframe(agent_rows, width="stretch")
else:
    st.write("No follow-up actions (agent switched off or nothing to follow).")

st.subheader("Gaps and limits")
for g in manifest.gaps:
    st.write(f"- {g}")

st.subheader("Files")
for label, rel in manifest.files.items():
    path = results / case_id / rel
    st.download_button(f"Download {rel}", path.read_bytes(), file_name=rel, key=label)
