"""
STAGE 3: Analyse, build issue timelines, trace open ends.
Reads 01_intake + 02_search  ->  writes 03_evidence.json

A. ANALYSE: turns each retrieved document into labelled evidence items (the AI analyst agent,
   or keyword rules), then applies the SAME quality gate to every item, whoever produced it:
     0. Source never mentions the organisation        -> skipped before analysis (saves cost)
     1. Different organisation (namesake)             -> rejected
     2. Quote is not an exact substring of the source -> rejected (stops invented quotes)
     3. Question IDs outside Q1..Q6                   -> removed
     4. Every kept item gets "safe to infer" / "not safe to infer" notes (inference.py)
   Rejected items are kept with a reason, so a reviewer can see what was thrown away and why.

B. ISSUES: groups the evidence into issues, each with a timeline, its origin, the number of
   independent sources and its current status (issues.py, plain code).

C. TRACE (AI mode): for the most serious issues with an open end (no outcome found, or known
   from one source only), the TRACER agent searches back to the origin and forward to the outcome.
   Pages it opens go through step A, and the issues are rebuilt.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from ..config import Config
from ..inference import apply_inference_rules
from ..io_utils import read_stage, write_stage
from ..issues import build_issues, needs_tracing
from ..providers import get_extractor, get_search, get_tracer
from ..research_tools import ResearchSession
from ..schemas import (EntityMatch, EvidenceItem, EvidenceTable, IntakeResult, QueryLog, RejectedItem, SearchResults,
                       SourceDocument)
from ..text_utils import mentions_identity

STAGE = "03_evidence"


_SAME = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                       " ": " "})


def _norm(text: str) -> str:
    """Collapse whitespace and curly quotes/dashes so formatting does not make a genuine quote fail."""
    return re.sub(r"\s+", " ", text.translate(_SAME)).strip().lower()


def analyse(cfg: Config, extractor, intake: IntakeResult, documents: list[SourceDocument]
            ) -> tuple[list[EvidenceItem], list[RejectedItem]]:
    """Step A for a batch of documents: analyst (in parallel when it is an AI) + the quality gate."""
    kept: list[EvidenceItem] = []
    rejected: list[RejectedItem] = []
    valid_qids = {q["id"] for q in cfg.questions}

    docs = []
    for doc in documents:
        if mentions_identity(f"{doc.title} {doc.text}", intake.case):
            docs.append(doc)
        else:
            rejected.append(RejectedItem(doc_id=doc.doc_id, reason="Source does not mention the organisation."))

    def one(doc):
        try:
            return extractor.extract(doc, intake.case), None
        except Exception as exc:
            return [], f"Analysis failed: {type(exc).__name__}: {exc}"

    workers = int(cfg.settings.get("llm", {}).get("parallel_calls", 6)) if cfg.provider("extractor") == "llm" else 1
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(one, docs))  # Same order as the documents.

    for doc, (items, error) in zip(docs, outcomes):
        if error:
            rejected.append(RejectedItem(doc_id=doc.doc_id, reason=error))
            continue
        for item in items:
            if item.entity_match == EntityMatch.NONE:
                rejected.append(RejectedItem(doc_id=doc.doc_id,
                                             reason="About a different organisation with a similar name (namesake)."))
                continue
            if not item.quote or _norm(item.quote) not in _norm(f"{doc.title}. {doc.text}"):
                rejected.append(RejectedItem(doc_id=doc.doc_id,
                                             reason="Quote not found word-for-word in the source."))
                continue
            item.question_ids = [q for q in item.question_ids if q in valid_qids]
            item.origin = doc.origin  # Who published the source, whatever the extractor said.
            apply_inference_rules(item, intake.case, cfg.criteria["lookback_years"], cfg.assessment_date.year)
            kept.append(item)
    return kept, rejected


def run(cfg: Config, case_id: str) -> EvidenceTable:
    results_dir = cfg.path("results_dir")
    intake = read_stage(results_dir, case_id, "intake", IntakeResult)
    search = read_stage(results_dir, case_id, "search", SearchResults)
    extractor = get_extractor(cfg)
    case = intake.case

    def issues_of(items):
        return build_issues(items, case.organisation_name, case.aliases, cfg.assessment_date.year,
                            cfg.criteria["lookback_years"])

    # A. Analyse every document found in stage 2.
    kept, rejected = analyse(cfg, extractor, intake, search.documents)
    # B. Group into issues with timelines.
    issues = issues_of(kept)

    # C. Trace the most serious open issues (AI mode only).
    tracer = get_tracer(cfg)
    trace_log: list[QueryLog] = []
    added: list[SourceDocument] = []
    to_trace = [i for i in issues if needs_tracing(i)][: int(cfg.settings.get("tracer", {}).get("max_issues", 3))]
    notes: dict[str, str] = {}  # evidence id -> trace note, to carry notes over when issues are rebuilt
    if tracer is not None and to_trace:
        provider = get_search(cfg, case_id)
        provider.set_id_prefix("T")  # Pages opened now get T001... so they never clash with stage 2's IDs.
        documents = {d.url: d for d in search.documents}
        before = set(documents)
        step = max((q.round for q in search.queries), default=0)
        for issue in to_trace:
            session = ResearchSession(cfg, provider, case, documents, trace_log, seen=search.seen_not_opened,
                                      budget=cfg.settings.get("tracer", {}), step_offset=step,
                                      term=f"tracer {issue.issue_id}")
            try:
                note = tracer.trace(issue, case, session)
            except Exception as exc:  # A failed trace is a gap, not a crash.
                note = f"Tracing failed: {type(exc).__name__}: {exc}"
            step = session.step
            for e in issue.events:
                notes[e.evidence_id] = note
        added = [d for u, d in documents.items() if u not in before]
        if added:
            more, more_rejected = analyse(cfg, extractor, intake, added)
            kept += more
            rejected += more_rejected
            issues = issues_of(kept)
        traced_ids = {e.evidence_id for i in to_trace for e in i.events}
        for issue in issues:
            ids = {e.evidence_id for e in issue.events}
            if ids & traced_ids:
                issue.traced = True
                issue.trace_notes = next(notes[i] for i in ids if i in notes)

    result = EvidenceTable(
        stage=STAGE,
        case_id=case_id,
        producer=extractor.name + (f" + {tracer.name}" if tracer else ""),
        fictional_data=intake.fictional_data,
        items=kept,
        rejected=rejected,
        issues=issues,
        trace_log=trace_log,
        added_documents=added,
    )
    write_stage(results_dir, case_id, "evidence", result)
    return result
