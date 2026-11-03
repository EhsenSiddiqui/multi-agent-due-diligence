"""
STAGE 6: Write outputs.  Reads 01-05  ->  writes the assessment file + 06_manifest.json

Produces, in results/<case_id>/:
    06_assessment.html      The full assessment file (open in any browser): summary, answers,
                            staff questions, evidence table, agent log, gaps, audit trail.
    06_assessment.pdf       The same as a fixed PDF record (for the register / audit trail).
    06_evidence.csv         The evidence table, for spreadsheets.
    06_template_b.docx      Template b (desk-based questions) filled in.
    06_template_c.docx      Template c section 1 filled; human sections left blank.
    06_template_e_risks.docx Draft of template e's "Potential risks" box only.
    06_manifest.json        List of the files above + the gaps list (machine-readable).

SIMPLIFIED: the Word files are new documents that copy the templates' layout and
headings; they do not edit the original .docx files.
BETTER: open the original templates from data/manual and fill their table cells
(python-docx can do this), so staff get the exact forms they already use.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..config import Config
from ..io_utils import case_dir, read_stage, write_stage
from ..reports import ReportContext, render_html, write_pdf
from ..schemas import (
    ORIGIN_LABELS,
    AssessmentManifest,
    Origin,
    agent_provenance,
    EvidenceStatus,
    EvidenceTable,
    IntakeResult,
    QuestionAnswers,
    RiskCall,
    ScoreResult,
    SearchResults,
)

STAGE = "06_outputs"
BANNER = "Draft prepared by the A5 due-diligence agent for Water Care staff review."


# ---------------------------------------------------------------------------- gaps

def build_gaps(search: SearchResults, evidence: EvidenceTable, answers: QuestionAnswers) -> list[str]:
    """Everything the reviewer should know was NOT established."""
    gaps = [f"Not established by the research agent: {q}" for q in search.open_questions]
    gaps += list(search.coverage_notes)
    failed = [q for q in search.queries if q.error]
    if len(failed) > 5:
        gaps.append(f"{len(failed)} searches or page openings failed, e.g. '{failed[0].query}' ({failed[0].error}).")
    else:
        gaps += [f"Search failed: '{q.query}' ({q.error})" for q in failed]
    for i in evidence.issues:
        if i.serious and not i.outcome_found:
            how = " (the tracer searched for it)" if i.traced else ""
            gaps.append(f"{i.issue_id} {i.label}: no outcome found in the sources searched{how}. "
                        "Ask the organisation, or check the regulator or court register.")
    for a in answers.answers:
        if a.risk_call == RiskCall.NO and not a.evidence_ids:
            gaps.append(f"{a.question_id}: nothing found in the sources searched. A quiet search is not a clean result.")
    by_reason: dict[str, list[str]] = {}
    for r in evidence.rejected:
        by_reason.setdefault(r.reason, []).append(r.doc_id)
    for reason, ids in by_reason.items():
        ids = sorted(set(ids))
        shown = ", ".join(ids[:8]) + (f" and {len(ids) - 8} more" if len(ids) > 8 else "")
        gaps.append(f"Source{'s' if len(ids) > 1 else ''} {shown} set aside: {reason}")
    return gaps


# ---------------------------------------------------------------------------- Word files

# Who fills each box. Shown in a "Filled by" column on every form.
BY_AI = "AI agent (draft)"
BY_STAFF = "Water Care staff"
BY_CASE_CARD = "Water Care staff (case card)"
FORM_NOTE = ("PROVENANCE: boxes marked 'AI agent (draft)' were filled automatically by the A5 due-diligence "
             "agent and have NOT been reviewed. Boxes marked 'Water Care staff' must be completed by a person.")


def _heading_doc(title: str):
    """New Word document with the demo banner, the provenance note and a title."""
    from docx import Document  # python-docx
    doc = Document()
    doc.add_paragraph(BANNER).runs[0].bold = True
    doc.add_paragraph(FORM_NOTE).runs[0].italic = True
    doc.add_heading(title, level=1)
    return doc


def _table(doc, rows: list[list[str]], header: bool = True):
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    for r, row in enumerate(rows):
        for col, value in enumerate(row):
            cell = table.cell(r, col)
            cell.text = value
            if header and r == 0:
                for run in cell.paragraphs[0].runs:
                    run.bold = True
    return table


def write_template_b(path: Path, intake, search, evidence, answers):
    doc = _heading_doc("Water Care ethical check questions: desk-based research results")
    c = intake.case
    _table(doc, [
        ["Field", "Value", "Filled by"],
        ["Prepared by (Relationship Lead)", "", BY_STAFF],
        ["Date", "", BY_STAFF],
        ["Third party name", c.organisation_name, BY_CASE_CARD],
        ["Nature of potential relationship",
         f"{c.relationship_type.replace('_', ' ')}, US${c.annual_value_usd:,.0f} per year", BY_CASE_CARD],
    ])
    doc.add_paragraph("")
    rows = [["No", "Question", "Response", "Potential risk? (proposed)", "Filled by"]]
    for i, a in enumerate(answers.answers, start=1):
        rows.append([str(i), a.question_text, a.response, a.risk_call.value, BY_AI])
    _table(doc, rows)

    # Search-term checklist: ticked when searched; "red flag" when a hit became adverse evidence.
    adverse = {e.doc_id for e in evidence.items
               if e.status in (EvidenceStatus.ALLEGATION, EvidenceStatus.INVESTIGATION, EvidenceStatus.FINDING)}
    doc.add_heading("Key search terms (checklist ticked by the AI agent)", level=2)
    term_rows = [["Term", "Searched", "Red flag"]]
    for q in search.queries:
        if q.round > 0 or q.term == "(profile)":
            continue
        flag = "YES" if adverse & set(q.hit_doc_ids) else ""
        term_rows.append([q.term, "error" if q.error else "✓", flag])
    _table(doc, term_rows)
    doc.save(path)


def write_template_c(path: Path, intake, score):
    doc = _heading_doc("Risk scoring, verification, and low risk decision record")
    doc.add_paragraph(f"Third party name: {intake.case.organisation_name}  (from the case card: {BY_STAFF})")
    doc.add_heading("1. Risk assessment", level=2)
    _table(doc, [
        ["Field", "Value", "Filled by"],
        ["Initial ethical risk questions: number of potential risk answers", str(score.risk_answers_count), BY_AI],
        ["Resulting risk categorisation (low / medium / high)", score.risk_level.value, BY_AI],
    ])
    doc.add_heading("2. Verification", level=2)
    _table(doc, [
        ["Field", "Value", "Filled by"],
        ["Verification required", score.verification_note, BY_AI],
        ["Internal verification completed by", "", BY_STAFF],
        ["Date verification carried out", "", BY_STAFF],
        ["Summary of additional findings", "", BY_STAFF],
        ["Notes from consultation", "", BY_STAFF],
        ["Outcome of verification", "", BY_STAFF],
    ])
    doc.add_heading("3. Low risk recommendation and decision", level=2)
    _table(doc, [
        ["Field", "Value", "Filled by"],
        ["Opportunity summary and recommendation", "", BY_STAFF],
        ["Decision by relevant team lead/manager/director", "", BY_STAFF],
        ["Date logged on global ethical checks register", "", BY_STAFF],
    ])
    doc.save(path)


def write_template_e_risks(path: Path, intake, evidence, answers):
    doc = _heading_doc("EC decision making template: potential risks to the Global Ethical Standards (DRAFT)")
    doc.add_paragraph(f"Third party: {intake.case.organisation_name.rstrip('.')}. Filled by: {BY_AI}. Only the "
                      "'Potential risks' box is drafted here; every recommendation, yes/no and rationale box "
                      f"in template e is for {BY_STAFF}.")
    by_standard = {"S1": [], "S2": []}
    for a in answers.answers:
        if a.risk_call == RiskCall.NO:
            continue
        for s in {s for e in evidence.items if e.evidence_id in a.evidence_ids for s in e.standards} or {"S2"}:
            by_standard.setdefault(s, []).append(a)
    labels = {"S1": "Ethical Standard 1: barriers to WASH", "S2": "Ethical Standard 2: reputation and credibility"}
    for s, items in by_standard.items():
        doc.add_heading(labels.get(s, s), level=2)
        if not items:
            doc.add_paragraph("No potential risks identified in the sources searched.")
        for a in items:
            doc.add_paragraph(f"{a.question_id} ({a.risk_call.value}): {a.response}", style="List Bullet")
    doc.save(path)


# ---------------------------------------------------------------------------- run

def run(cfg: Config, case_id: str) -> AssessmentManifest:
    results_dir = cfg.path("results_dir")
    intake = read_stage(results_dir, case_id, "intake", IntakeResult)
    search = read_stage(results_dir, case_id, "search", SearchResults)
    evidence = read_stage(results_dir, case_id, "evidence", EvidenceTable)
    answers = read_stage(results_dir, case_id, "answers", QuestionAnswers)
    score = read_stage(results_dir, case_id, "score", ScoreResult)

    out = case_dir(results_dir, case_id)
    gaps = build_gaps(search, evidence, answers)
    files = {}

    ctx = ReportContext(intake=intake, search=search, evidence=evidence, answers=answers, score=score, gaps=gaps)
    (out / "06_assessment.html").write_text(render_html(ctx), encoding="utf-8")
    files["assessment_html"] = "06_assessment.html"
    try:
        write_pdf(ctx, out / "06_assessment.pdf")
        files["assessment_pdf"] = "06_assessment.pdf"
    except ImportError:
        gaps.append("reportlab not installed: PDF report was not produced.")

    with open(out / "06_evidence.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        # source_published_by = who published the source; row_prepared_by = who made this row.
        writer.writerow(["evidence_id", "status", "publisher", "source_published_by", "date", "reliability",
                         "entity_match", "questions", "standards", "summary", "quote", "safe_to_infer",
                         "not_safe_to_infer", "url", "row_prepared_by"])
        for e in evidence.items:
            writer.writerow([e.evidence_id, e.status.value, e.publisher, ORIGIN_LABELS.get(e.origin, "unknown"),
                             e.published_date, e.reliability.value, e.entity_match.value, " ".join(e.question_ids),
                             " ".join(e.standards), e.summary, e.quote, e.safe_to_infer,
                             " | ".join(e.not_safe_to_infer), e.source_url,
                             f"{ORIGIN_LABELS[Origin.AI_AGENT]} ({e.extracted_by})"])
    files["evidence_csv"] = "06_evidence.csv"

    try:  # Word output is optional: the HTML/PDF report is the complete record.
        write_template_b(out / "06_template_b.docx", intake, search, evidence, answers)
        write_template_c(out / "06_template_c.docx", intake, score)
        write_template_e_risks(out / "06_template_e_risks.docx", intake, evidence, answers)
        files.update(template_b="06_template_b.docx", template_c="06_template_c.docx",
                     template_e_risks="06_template_e_risks.docx")
    except ImportError:
        gaps.append("python-docx not installed: Word templates were not produced.")

    # Every file in this folder was produced by the agent; the Word forms also hold boxes for staff.
    file_provenance = {name: agent_provenance() for name in files}
    for name in ("template_b", "template_c", "template_e_risks"):
        if name in file_provenance:
            file_provenance[name] = agent_provenance("Agent-filled draft; boxes marked 'Water Care staff' "
                                                     "must be completed and signed by a person.")
    manifest = AssessmentManifest(stage=STAGE, case_id=case_id, producer="s6_outputs",
                                  fictional_data=intake.fictional_data, files=files, gaps=gaps,
                                  file_provenance=file_provenance)
    write_stage(results_dir, case_id, "manifest", manifest)
    return manifest
