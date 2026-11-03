"""
Renders the assessment file as a calm, risk-ordered HTML page and a short PDF.

Order (what a reviewer needs first comes first):
  1. At a glance           proposed risk level, worst case, next step, who decides
  2. Risks indicated       the "yes" answers, with their key evidence inline
  3. Needs your judgement  the "unclear" answers, each with the question that would settle it
  4. No issues found       the "no" answers, in one short block
  5. Evidence              every source, most serious first; click for quote and what it proves
  6. Limits                what this research could not establish
  Folded away: how the research was done, who prepared this, audit trail.

Sections follow the CALL (yes -> unclear -> no), never an invented urgency score, so the
agreed criteria stay the only basis for ordering. Within a section, findings are ordered by
the strongest evidence behind them (finding > investigation > allegation > response > background),
the same rule the evidence table uses. Every card keeps its Q-number, so it maps back to template b.

SIMPLIFIED: fixed layout, English only.  BETTER: WaterAid branding; version-stamped PDF for the register.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from .schemas import (ORIGIN_LABELS, EvidenceItem, EvidenceTable, IntakeResult, Origin, QuestionAnswer,
                      QuestionAnswers, RiskCall, ScoreResult, SearchResults)

DRAFT_NOTE = "Draft prepared by the AI agent for staff review. Every risk call is a proposal; a person decides."
SHORT_Q = {"Q1": "High-risk or excluded sectors", "Q2": "Impact on community water, sanitation and hygiene",
           "Q3": "Operations in low- or middle-income countries", "Q4": "Investigations in the last 10 years",
           "Q5": "Media, NGO or public campaigns", "Q6": "Anything else relevant"}
STATUS_LABEL = {"allegation": "Allegation", "investigation": "Investigation", "finding": "Finding",
                "response": "Org. response", "context": "Background"}
SEVERITY = {"finding": 0, "investigation": 1, "allegation": 2, "response": 3, "context": 4}
MAX_INLINE_EVIDENCE = 3


@dataclass
class ReportContext:
    intake: IntakeResult
    search: SearchResults
    evidence: EvidenceTable
    answers: QuestionAnswers
    score: ScoreResult
    gaps: list[str]


def _e(value) -> str:
    return html.escape(str(value), quote=True)


# ============================================================================ shared content

def search_failures(ctx: ReportContext) -> tuple[int, int]:
    """(failed, total) searches and link openings."""
    return sum(1 for q in ctx.search.queries if q.error), len(ctx.search.queries)


def search_warning_text(ctx: ReportContext) -> str:
    failed, total = search_failures(ctx)
    if not failed:
        return ""
    return (f"WARNING: {failed} of {total} searches or link openings failed. Answers marked 'no' may be wrong "
            "because sources could not be checked. Fix the errors (see Limits) and run again before relying on this file.")


def provenance_lines(ctx: ReportContext) -> list[tuple[str, str]]:
    """Who prepared what."""
    n_partner = sum(e.origin == Origin.PARTNER for e in ctx.evidence.items)
    n_third = sum(e.origin == Origin.THIRD_PARTY for e in ctx.evidence.items)
    return [
        ("This report", f"{ORIGIN_LABELS[Origin.AI_AGENT]} ({ctx.score.provenance.prepared_by}). Not yet reviewed by a person."),
        ("Case details", f"{ORIGIN_LABELS[ctx.intake.case.provenance.origin]}: {ctx.intake.case.provenance.prepared_by}."),
        ("Evidence sources", f"{n_partner} published by the organisation itself; {n_third} by third parties "
                             "(news, regulators, courts, NGOs, other companies)."),
        ("Rules applied", "Water Care's ethical checks manual, encoded by staff in config/criteria.json."),
        ("Decisions", "Water Care staff only."),
    ]


def ruled_out(a: QuestionAnswer) -> list[tuple[str, str]]:
    """The elimination reasons that apply to this call: ('Not "no"', reason), ('Not "yes"', reason)."""
    out = []
    if a.risk_call != RiskCall.YES and a.why_not_yes:
        out.append(('Not "yes"', a.why_not_yes))
    if a.risk_call != RiskCall.NO and a.why_not_no:
        out.append(('Not "no"', a.why_not_no))
    return out


def by_severity(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Findings, then investigations, allegations, responses, background; newest first within each."""
    return sorted(items, key=lambda e: (SEVERITY[e.status.value], -(e.published_date.toordinal() if e.published_date else 0)))


def cited(a: QuestionAnswer, ctx: ReportContext) -> list[EvidenceItem]:
    """The evidence an answer cites, most serious first."""
    index = {e.evidence_id: e for e in ctx.evidence.items}
    return by_severity([index[i] for i in a.evidence_ids if i in index])


def grouped(ctx: ReportContext) -> dict[RiskCall, list[QuestionAnswer]]:
    """Answers grouped by call; within each group, strongest evidence first, then question number."""
    def strength(a):
        ev = cited(a, ctx)
        return (SEVERITY[ev[0].status.value] if ev else 9, a.question_id)
    return {call: sorted((a for a in ctx.answers.answers if a.risk_call == call), key=strength)
            for call in (RiskCall.YES, RiskCall.UNCLEAR, RiskCall.NO)}


def _research_counts(ctx: ReportContext) -> str:
    fixed = sum(1 for q in ctx.search.queries if q.round == 0)
    agent = sum(1 for q in ctx.search.queries if q.round > 0)
    traced = len(ctx.evidence.trace_log)
    return (f"{fixed} standard searches, {agent} follow-up actions by the researcher"
            + (f" and {traced} by the tracer" if traced else "")
            + f"; {len(ctx.evidence.items)} evidence items kept, {len(ctx.evidence.rejected)} set aside.")


ISSUE_TONE = {"allegation": "allegation", "investigation_opened": "investigation", "charges_or_lawsuit": "investigation",
              "appeal": "investigation", "ruling": "finding", "fine": "finding", "settlement": "finding",
              "dismissed": "response", "remediation": "response"}


def _issue_card(i) -> str:
    from .issues import status_label
    tone = ISSUE_TONE.get(i.current_status, "context")
    outcome = ("<span class='ok'>Outcome found</span>" if i.outcome_found
               else "<span class='notok'>No outcome found in the sources searched</span>")
    steps = "".join(
        f"<li><span class='when'>{_e(e.date or 'undated')}{'*' if e.date_is_publication else ''}</span>"
        f"<span class='chip {ISSUE_TONE.get(e.event_type, 'context')}'>{_e(status_label(e.event_type))}</span> "
        f"{_e(e.publisher)} <span class='muted'>[{e.evidence_id}]</span></li>" for e in i.events)
    trace = (f"<div class='muted'><b>Tracer:</b> {_e(i.trace_notes)}</div>" if i.traced and i.trace_notes else "")
    return (f"<div class='issue'><div class='ch'><span class='qid'>{i.issue_id}</span><span class='qt'>{_e(i.label)}</span>"
            f"<span class='chip {tone}' style='margin-left:auto'>Now: {_e(status_label(i.current_status))}</span></div>"
            f"<div class='imeta'>First reported by {_e(i.first_reported)} &middot; {i.independent_sources} independent "
            f"source{'s' if i.independent_sources != 1 else ''} &middot; {outcome} &middot; "
            f"{', '.join(i.question_ids)}</div><ol class='tl'>{steps}</ol>{trace}</div>")


# ============================================================================ HTML

CSS = """
:root{--ink:#1d2b34;--muted:#61717b;--line:#e1e7eb;--bg:#f5f7f8;--card:#fff;--brand:#0b5a7a;
--yes:#a8261d;--yes-bg:#fbeae8;--no:#2b7543;--no-bg:#e7f4eb;--unc:#875700;--unc-bg:#fdf3da}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 "Segoe UI",Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{max-width:980px;margin:0 auto;padding:28px 20px 48px}
header h1{margin:0;font-size:26px;letter-spacing:-.01em}header p{margin:4px 0 0;color:var(--muted);font-size:13px}
.warn{background:var(--yes-bg);color:var(--yes);border-radius:8px;padding:10px 14px;font-weight:600;margin:16px 0}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:16px 0}
section.risk{border-left:5px solid var(--yes)}section.judge{border-left:5px solid var(--unc)}section.clear{border-left:5px solid var(--no)}
h2{font-size:15px;margin:0 0 4px;color:var(--brand);text-transform:uppercase;letter-spacing:.04em}
h2 .n{color:var(--muted);font-weight:600}.lead{color:var(--muted);font-size:13px;margin:0 0 10px}
.glance{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:10px 0 12px}
.tile{border:1px solid var(--line);border-radius:8px;padding:12px 14px}.tile .k{color:var(--muted);font-size:12px}
.tile .v{font-size:22px;font-weight:700;margin-top:2px}.tile .s{color:var(--muted);font-size:12px}
.next{font-size:14px}.next div{margin:2px 0}.next b{display:inline-block;min-width:150px}
.pill{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.yes,.high{background:var(--yes-bg);color:var(--yes)}.no,.low{background:var(--no-bg);color:var(--no)}
.unclear,.medium{background:var(--unc-bg);color:var(--unc)}
.card{border-top:1px solid var(--line);padding:12px 0}.card:first-of-type{border-top:none}
.ch{display:flex;gap:10px;align-items:baseline}.qid{font-weight:700;color:var(--muted);min-width:28px}
.qt{font-weight:600}.ans{margin:4px 0 0 38px}.decide{margin:8px 0 0 38px;background:var(--unc-bg);border-radius:6px;padding:8px 10px;font-size:14px}
.decide b{color:var(--unc)}
.ev{margin:8px 0 0 38px;font-size:13px}.ev div{margin:3px 0}.evq{color:#33424c;font-style:italic}
.chip{display:inline-block;font-size:11px;padding:1px 7px;border-radius:4px;background:#eef3f6;white-space:nowrap;margin-right:4px}
.chip.allegation{background:#fdf3da}.chip.investigation{background:#fde4d2}.chip.finding{background:#fbeae8}.chip.response{background:#e4eef8}
details{margin:6px 0 0 38px}details>summary{cursor:pointer;color:var(--brand);font-size:13px}
.sides{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px;font-size:13px}
.side{background:var(--bg);border-radius:6px;padding:8px 10px}.side b{display:block;font-size:12px;color:var(--muted);margin-bottom:2px}
.out{font-size:13px;margin-top:8px}.out div{margin:3px 0}
.none{color:var(--muted);font-size:14px}
.issue{border-top:1px solid var(--line);padding:12px 0}.issue:first-of-type{border-top:none}
.imeta{margin:4px 0 0 38px;font-size:13px;color:var(--muted)}
ol.tl{list-style:none;margin:8px 0 4px 38px;padding:0 0 0 14px;border-left:2px solid var(--line);font-size:13px}
ol.tl li{margin:4px 0;position:relative}ol.tl li::before{content:"";position:absolute;left:-19px;top:7px;width:8px;height:8px;border-radius:50%;background:var(--line)}
.when{display:inline-block;min-width:92px;font-variant-numeric:tabular-nums;color:var(--muted)}
.issue .muted{margin-left:38px}
.tag{margin-left:auto;font-size:11px;color:var(--brand);background:#e4eef8;border-radius:4px;padding:1px 7px}
.debate{margin-top:8px;font-size:13px;border-left:3px solid #c9d6de;padding:4px 10px}.debate div{margin:3px 0}
.summary{font-size:14px;margin:6px 0 10px}
ul.clearlist{margin:0;padding-left:18px}ul.clearlist li{margin:4px 0}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;vertical-align:top;padding:8px 6px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:12px}table.evt{table-layout:fixed}table.evt td:first-child{white-space:nowrap}
table details{margin:0}.org{color:var(--muted);font-size:12px}.quote{font-style:italic;color:#33424c;margin:4px 0}
.ok{color:var(--no)}.notok{color:var(--yes)}
ul.lim{margin:0;padding-left:18px}ul.lim li{margin:4px 0}.muted{color:var(--muted);font-size:13px}
.fold{background:transparent;border:none;padding:0 4px}.fold>details{margin:0}.fold>details>summary{font-size:14px;font-weight:600}
@media (max-width:640px){.sides{grid-template-columns:1fr}.ans,.ev,.decide,details{margin-left:0}}
@media print{body{background:#fff}section{border:none;padding:6px 0}details>summary{display:none}}
"""


def _inline_evidence(a: QuestionAnswer, ctx: ReportContext) -> str:
    ev = cited(a, ctx)
    if not ev:
        return ""
    rows = []
    for e in ev[:MAX_INLINE_EVIDENCE]:
        when = e.published_date.strftime("%b %Y") if e.published_date else "undated"
        rows.append(f"<div><span class='chip {e.status.value}'>{STATUS_LABEL[e.status.value]}</span>"
                    f"<b>{_e(e.publisher)}</b> ({when}) [{e.evidence_id}] "
                    f"<span class='evq'>&ldquo;{_e(e.quote)}&rdquo;</span></div>")
    more = len(ev) - MAX_INLINE_EVIDENCE
    if more > 0:
        rows.append(f"<div class='muted'>+ {more} more in the Evidence section</div>")
    return f"<div class='ev'>{''.join(rows)}</div>"


def debate_label(a: QuestionAnswer) -> str:
    """One-line outcome of the judge-challenger debate, or '' if there was none."""
    if not a.challenge:
        return ""
    if a.call_before_challenge:
        return f'Changed from "{a.call_before_challenge}" after the challenger\'s objection'
    return f'Challenger argued for "{a.challenger_call}"; the judge held its call'


def _debate(a: QuestionAnswer) -> str:
    if not a.challenge:
        return ""
    return (f"<div class='debate'><div><b>Challenger</b> (argued for &ldquo;{_e(a.challenger_call)}&rdquo;): "
            f"{_e(a.challenge)}</div><div><b>Judge's reply</b>: {_e(a.rebuttal) or '&mdash;'}</div></div>")


def _why(a: QuestionAnswer) -> str:
    sides = (f"<div class='sides'><div class='side'><b>Points to a risk</b>{_e(a.case_for_risk) or '&mdash;'}</div>"
             f"<div class='side'><b>Points against</b>{_e(a.case_against_risk) or '&mdash;'}</div></div>")
    out = "".join(f"<div><b>{_e(k)}:</b> {_e(v)}</div>" for k, v in ruled_out(a))
    label = "Why this answer (both sides" + (", and the challenge)" if a.challenge else ")")
    return (f"<details><summary>{label}</summary>{sides}<div class='out'>{out}</div>{_debate(a)}"
            f"<div class='muted'>Template b, {a.question_id}: {_e(a.question_text)}</div></details>")


def _card(a: QuestionAnswer, ctx: ReportContext, with_decide: bool) -> str:
    decide = ""
    if with_decide and a.staff_questions:
        decide = f"<div class='decide'><b>Decide:</b> {_e(' '.join(a.staff_questions))}</div>"
    extra = ""
    if not with_decide and a.staff_questions:
        extra = f"<div class='ev muted'>For the reviewer: {_e(' '.join(a.staff_questions))}</div>"
    tag = f"<span class='tag'>{_e(debate_label(a))}</span>" if a.challenge else ""
    return (f"<div class='card'><div class='ch'><span class='qid'>{a.question_id}</span>"
            f"<span class='qt'>{_e(SHORT_Q.get(a.question_id, a.question_text))}</span>{tag}</div>"
            f"<div class='ans'>{_e(a.response)}</div>{decide}{_inline_evidence(a, ctx)}{extra}{_why(a)}</div>")


def _evidence_row(e: EvidenceItem) -> str:
    when = e.published_date.strftime("%d %b %Y") if e.published_date else "undated"
    nots = "".join(f"<div class='notok'>&#10007; Not safe to infer: {_e(n)}</div>" for n in e.not_safe_to_infer)
    return (f"<tr><td style='word-break:break-all'>{e.evidence_id}</td><td><span class='chip {e.status.value}'>{STATUS_LABEL[e.status.value]}</span></td>"
            f"<td><a href='{_e(e.source_url)}'>{_e(e.publisher)}</a><div class='org'>{when} &middot; "
            f"{_e(ORIGIN_LABELS.get(e.origin, 'unknown'))} &middot; reliability {e.reliability.value}</div></td>"
            f"<td><details><summary>Quote and what it proves</summary><div class='quote'>&ldquo;{_e(e.quote)}&rdquo;</div>"
            f"<div class='ok'>&#10003; Safe to infer: {_e(e.safe_to_infer)}</div>{nots}</details></td></tr>")


EVT_HEAD = ("<colgroup><col style='width:78px'><col style='width:120px'><col style='width:290px'><col></colgroup>"
            "<tr><th>ID</th><th>Type</th><th>Source</th><th>Details</th></tr>")


def render_html(ctx: ReportContext) -> str:
    c, s = ctx.intake.case, ctx.score
    g = grouped(ctx)
    warning = search_warning_text(ctx)
    gaps_main, gaps_more = ctx.gaps[:4], ctx.gaps[4:]

    risks = "".join(_card(a, ctx, with_decide=False) for a in g[RiskCall.YES]) \
        or "<p class='none'>No answer indicates a risk.</p>"
    judge = "".join(_card(a, ctx, with_decide=True) for a in g[RiskCall.UNCLEAR]) \
        or "<p class='none'>Nothing needs a judgement call.</p>"
    clear_items = "".join(
        f"<li><b>{a.question_id} {_e(SHORT_Q.get(a.question_id, ''))}:</b> {_e(a.response)}</li>" for a in g[RiskCall.NO])
    clear = (f"<ul class='clearlist'>{clear_items}</ul>" if clear_items else "<p class='none'>None.</p>")
    policy = "".join(f"<li>{_e(q)}</li>" for q in ctx.answers.policy_questions)

    agent_rows = "".join(
        f"<tr><td>{q.round}</td><td>{'Opened link' if q.action == 'follow_link' else 'Searched'}</td>"
        f"<td style='word-break:break-all'>{_e(q.query)}</td><td>{', '.join(q.hit_doc_ids) or ('error' if q.error else 'nothing new')}</td>"
        f"<td>{_e(q.reason)}</td></tr>" for q in [*(q for q in ctx.search.queries if q.round > 0),
                                                   *ctx.evidence.trace_log])
    terms = ", ".join(f"{q.term}{' (' + ', '.join(q.hit_doc_ids) + ')' if q.hit_doc_ids else ''}"
                      for q in ctx.search.queries if q.round == 0)
    prov = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in provenance_lines(ctx))
    serious = [i for i in ctx.evidence.issues if i.serious]
    minor = [i for i in ctx.evidence.issues if not i.serious]
    issues_section = ""
    if ctx.evidence.issues:
        issues_section = (
            f"<section><h2>Issues over time <span class='n'>({len(ctx.evidence.issues)})</span></h2>"
            "<p class='lead'>Evidence about the same issue, in the order things happened, with where each issue "
            "stands now. Many articles repeating one report count as one source. * = article date; the event "
            "date was not given.</p>" + "".join(_issue_card(i) for i in serious)
            + (f"<details style='margin-left:0'><summary>{len(minor)} minor or older issues</summary>"
               + "".join(_issue_card(i) for i in minor) + "</details>" if minor else "") + "</section>")
    cited_ids = {i for a in ctx.answers.answers for i in a.evidence_ids}
    cited_items = by_severity([e for e in ctx.evidence.items if e.evidence_id in cited_ids])
    other_items = by_severity([e for e in ctx.evidence.items if e.evidence_id not in cited_ids])
    summary = (f"<p class='muted'><b>Research summary</b> (by the research agent):</p><p class='summary'>"
               f"{_e(ctx.search.research_summary)}</p>") if ctx.search.research_summary else ""

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Assessment: {_e(c.organisation_name)}</title><style>{CSS}</style></head><body><div class="wrap">
<header><h1>{_e(c.organisation_name)}</h1>
<p>Ethical due-diligence assessment &middot; desk research (template b) &middot; {_e(s.generated_at[:10])}
&middot; {_e(DRAFT_NOTE)}</p></header>
{f'<div class="warn">{_e(warning)}</div>' if warning else ''}

<section><h2>At a glance</h2><div class="glance">
<div class="tile"><div class="k">Proposed risk level</div><div class="v"><span class="pill {s.risk_level.value}">{s.risk_level.value}</span></div>
<div class="s">{s.risk_answers_count} of 6 answers indicate a risk</div></div>
<div class="tile"><div class="k">Needs your judgement</div><div class="v">{s.unclear_count}</div><div class="s">answers could not be settled</div></div>
<div class="tile"><div class="k">If every unclear answer were a risk</div><div class="v"><span class="pill {s.risk_level_if_unclear_were_yes.value}">{s.risk_level_if_unclear_were_yes.value}</span></div></div>
</div>
<div class="next"><div><b>Suggested next step</b>{_e(s.suggested_next_step)}</div>
<div><b>Decided by</b>{_e(s.suggested_decision_maker)}</div>
<div><b>Relationship</b>{_e(c.relationship_type.replace('_', ' '))}, US${c.annual_value_usd:,.0f}/year, {_e(c.presence)}{', brand affiliation' if c.brand_affiliation else ''}</div></div>
<details style="margin-left:0"><summary>When to escalate</summary><p class="muted">{_e(s.escalation_note)}</p></details></section>

<section class="risk"><h2>Risks indicated <span class="n">({len(g[RiskCall.YES])})</span></h2>
<p class="lead">Answers where "no" was ruled out by the evidence.</p>{risks}</section>

<section class="judge"><h2>Needs your judgement <span class="n">({len(g[RiskCall.UNCLEAR])})</span></h2>
<p class="lead">Neither "yes" nor "no" could be ruled out. Each card says what would settle it.</p>{judge}
<details style="margin-left:0"><summary>Open questions about the rules themselves</summary><ul class="lim">{policy}</ul></details></section>

<section class="clear"><h2>No issues found <span class="n">({len(g[RiskCall.NO])})</span></h2>
<p class="lead">Nothing pointed to a risk in the sources searched. A quiet search is not a clean result: see Limits.</p>{clear}</section>

{issues_section}

<section><h2>Evidence <span class="n">({len(cited_items)} cited)</span></h2>
<p class="lead">The sources behind the answers above, most serious first. Click a row for the exact quote, what it
proves, and what it does not.</p>
<table class="evt">{EVT_HEAD}{''.join(_evidence_row(e) for e in cited_items) or '<tr><td colspan=4>None.</td></tr>'}</table>
{f'<details style="margin-left:0"><summary>{len(other_items)} other sources reviewed but not cited</summary><table class="evt">{EVT_HEAD}' + ''.join(_evidence_row(e) for e in other_items) + '</table></details>' if other_items else ''}
</section>

<section><h2>Limits of this research</h2>
<p class="lead">{_e(_research_counts(ctx))}</p>
<ul class="lim">{''.join(f'<li>{_e(x)}</li>' for x in gaps_main)}</ul>
{f'<details style="margin-left:0"><summary>{len(gaps_more)} more</summary><ul class="lim">' + ''.join(f'<li>{_e(x)}</li>' for x in gaps_more) + '</ul></details>' if gaps_more else ''}
</section>

<section class="fold"><details><summary>How the research was done</summary>{summary}
<p class="muted">The agent's follow-up actions after the standard searches, with its reason for each:</p>
<table><tr><th>Step</th><th>Action</th><th>Target</th><th>Found</th><th>Why</th></tr>
{agent_rows or '<tr><td colspan=5>No follow-up actions.</td></tr>'}</table>
<p class="muted">Standard searches (template b terms, with hits): {_e(terms)}</p></details></section>

<section class="fold"><details><summary>Who prepared this</summary><table>{prov}</table></details></section>

<section class="fold"><details><summary>Audit trail</summary><p class="muted">Search: {_e(ctx.search.producer)}
&middot; extraction: {_e(ctx.evidence.producer)} &middot; judging: {_e(ctx.answers.producer)} &middot; scoring: {_e(s.producer)}
&middot; schema {_e(s.schema_version)} &middot; generated {_e(s.generated_at)}</p></details></section>
</div></body></html>"""


# ============================================================================ PDF

def write_pdf(ctx: ReportContext, path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=8.8, leading=11.5)
    small = ParagraphStyle("s", parent=body, fontSize=7.6, leading=9.6, textColor=colors.HexColor("#61717b"))
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, alignment=0, spaceAfter=1)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=10.5, textColor=colors.HexColor("#0b5a7a"),
                        spaceBefore=10, spaceAfter=3, keepWithNext=1)
    colour = {"yes": "#a8261d", "no": "#2b7543", "unclear": "#875700", "low": "#2b7543", "medium": "#875700", "high": "#a8261d"}
    x = html.escape

    def P(text, style=body):
        return Paragraph(x(str(text)), style)

    def tag(value):
        return Paragraph(f'<font color="{colour.get(value, "#1d2b34")}"><b>{value.upper()}</b></font>', body)

    def grid(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e1e7eb")),
                 ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        if header:
            style.append(("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#61717b")))
        t.setStyle(TableStyle(style))
        return t

    def challenge_note(a):
        return f"<br/><i>{x(debate_label(a))}.</i>" if a.challenge else ""

    def evidence_lines(a):
        ev = cited(a, ctx)[:MAX_INLINE_EVIDENCE]
        return "<br/>".join(f"<b>{x(STATUS_LABEL[e.status.value])}</b>: {x(e.publisher)} [{e.evidence_id}]" for e in ev) or "&mdash;"

    c, s = ctx.intake.case, ctx.score
    g = grouped(ctx)
    story = [P(c.organisation_name, h1),
             P(f"Ethical due-diligence assessment, desk research (template b), {s.generated_at[:10]}. {DRAFT_NOTE}", small)]
    if (w := search_warning_text(ctx)):
        story += [Spacer(1, 4), Paragraph(f'<font color="#a8261d"><b>{x(w)}</b></font>', body)]

    story.append(P("At a glance", h2))
    story.append(grid([
        [P("Proposed risk level"), tag(s.risk_level.value), P(f"{s.risk_answers_count} of 6 answers indicate a risk")],
        [P("Needs your judgement"), P(str(s.unclear_count)), P("answers could not be settled")],
        [P("If every unclear were a risk"), tag(s.risk_level_if_unclear_were_yes.value), P("")],
        [P("Suggested next step"), P(s.suggested_next_step), P("")],
        [P("Decided by"), P(s.suggested_decision_maker), P("")],
    ], [42 * mm, 90 * mm, 42 * mm], header=False))

    story.append(Paragraph(f'<font color="{colour["yes"]}">Risks indicated ({len(g[RiskCall.YES])})</font>', h2))
    if g[RiskCall.YES]:
        rows = [[P("Q"), P("Finding"), P("Key evidence"), P('Why not "no"')]]
        rows += [[P(a.question_id), Paragraph(f"<b>{x(SHORT_Q.get(a.question_id, ''))}</b><br/>{x(a.response)}", body),
                  Paragraph(evidence_lines(a), small), Paragraph(x(a.why_not_no) + challenge_note(a), small)]
                 for a in g[RiskCall.YES]]
        story.append(grid(rows, [9 * mm, 68 * mm, 52 * mm, 45 * mm]))
    else:
        story.append(P("No answer indicates a risk.", small))

    story.append(Paragraph(f'<font color="{colour["unclear"]}">Needs your judgement ({len(g[RiskCall.UNCLEAR])})</font>', h2))
    if g[RiskCall.UNCLEAR]:
        rows = [[P("Q"), P("Finding"), P("Decide"), P("Why neither answer was ruled out")]]
        rows += [[P(a.question_id), Paragraph(f"<b>{x(SHORT_Q.get(a.question_id, ''))}</b><br/>{x(a.response)}", body),
                  P(" ".join(a.staff_questions), body),
                  Paragraph("<br/>".join(f"<b>{x(k)}:</b> {x(v)}" for k, v in ruled_out(a)) + challenge_note(a), small)]
                 for a in g[RiskCall.UNCLEAR]]
        story.append(grid(rows, [9 * mm, 55 * mm, 50 * mm, 60 * mm]))
    else:
        story.append(P("Nothing needs a judgement call.", small))

    story.append(Paragraph(f'<font color="{colour["no"]}">No issues found ({len(g[RiskCall.NO])})</font>', h2))
    for a in g[RiskCall.NO]:
        story.append(Paragraph(f"<b>{a.question_id} {x(SHORT_Q.get(a.question_id, ''))}:</b> {x(a.response)}", small))
    if not g[RiskCall.NO]:
        story.append(P("None.", small))

    if ctx.evidence.issues:
        from .issues import status_label
        story.append(P(f"Issues over time ({len(ctx.evidence.issues)})", h2))
        rows = [[P("Issue"), P("Now"), P("First reported"), P("Outcome"), P("Timeline")]]
        for i in ctx.evidence.issues:
            rows.append([Paragraph(f"<b>{i.issue_id}</b> {x(i.label)}", body), P(status_label(i.current_status), small),
                         P(i.first_reported, small),
                         P("found" if i.outcome_found else "not found in sources searched", small),
                         Paragraph("<br/>".join(f"{x(e.date or 'undated')}: {x(status_label(e.event_type))} "
                                                f"[{e.evidence_id}]" for e in i.events), small)])
        story.append(grid(rows, [44 * mm, 24 * mm, 34 * mm, 24 * mm, 48 * mm]))

    cited_ids = {i for a in ctx.answers.answers for i in a.evidence_ids}
    cited_items = by_severity([e for e in ctx.evidence.items if e.evidence_id in cited_ids])
    story.append(P(f"Evidence cited ({len(cited_items)}, most serious first)", h2))
    if len(cited_items) < len(ctx.evidence.items):
        story.append(P(f"{len(ctx.evidence.items) - len(cited_items)} other sources were reviewed but not cited; "
                       "all are listed in 06_evidence.csv.", small))
    rows = [[P("ID"), P("Type"), P("Source"), P("Quote"), P("What it proves / does not prove")]]
    for e in cited_items:
        when = e.published_date.strftime("%d %b %Y") if e.published_date else "undated"
        rows.append([P(e.evidence_id), P(STATUS_LABEL[e.status.value]),
                     Paragraph(f"{x(e.publisher)}<br/><font size='7'>{when}, {x(ORIGIN_LABELS.get(e.origin, ''))}</font>", body),
                     P(f"“{e.quote}”", small),
                     Paragraph(f'<font color="#2b7543">Safe: {x(e.safe_to_infer)}</font><br/>'
                               + "<br/>".join(f'<font color="#a8261d">Not safe: {x(n)}</font>' for n in e.not_safe_to_infer),
                               small)])
    story.append(grid(rows, [15 * mm, 19 * mm, 35 * mm, 63 * mm, 42 * mm]))

    story.append(P("Limits of this research", h2))
    story.append(P(f"{_research_counts(ctx)} A quiet search is not a clean result.", small))
    story += [P(f"- {gap}", small) for gap in ctx.gaps]
    if ctx.answers.policy_questions:
        story.append(P("Open questions about the rules", h2))
        story += [P(f"- {q}", small) for q in ctx.answers.policy_questions]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(18 * mm, 10 * mm, f"{c.organisation_name}: due-diligence assessment. Prepared by the AI agent; "
                                            "draft for staff review.")
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(str(path), pagesize=A4, title=f"Assessment: {c.organisation_name}",
                      leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm
                      ).build(story, onFirstPage=footer, onLaterPages=footer)
