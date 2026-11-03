"""
AGENT 3: the Tracer (an LLM with tools).  Switch: providers.tracer: llm

After the analyst has grouped the evidence into issues, some serious issues are left with an
open end: "an NGO alleged pollution in 2019" but no investigation, ruling or settlement found;
or a story known only from second-hand news. For each such issue (up to tracer.max_issues),
the tracer works its own tool loop to:

    BACKWARD  find the ORIGIN: the first report and the primary record behind the news
              (the NGO report, court filing or regulator notice), and its real date;
    FORWARD   find the OUTCOME and the CURRENT STATE: investigation, charges, ruling, appeal,
              fine, settlement, dismissal, remediation, including the last 12 months.

It stops when it finds an authoritative outcome, when it has checked where an outcome would be
recorded and found nothing, or when its budget runs out. "No outcome found" is reported as
such, never guessed. New pages it opens are analysed by the analyst like any other source.

Same guard rails as the researcher: only URLs it was shown, budgets in settings.yaml.
"""

from __future__ import annotations

import json

from ..issues import status_label
from ..schemas import CaseCard, Issue
from .base import ResearchTools, Tracer
from .leads_llm import TOOLS as RESEARCH_TOOLS, page_view
from .llm_client import LLMClient

SYSTEM = """You are the tracer in a due-diligence team at a water charity. You get ONE issue about an organisation,
with the timeline found so far. Complete its story:
1. BACKWARD: find where it started. Look for the original report or primary record (NGO report, court filing,
   regulator notice) rather than articles repeating it. Note its date.
2. FORWARD: find what happened next and where it stands now: investigation, charges, ruling, appeal, fine,
   settlement, dismissal, remediation. Search the regulator, the court, the company's filings and recent news
   (recent=true for the last 12 months).
Stop when you find an authoritative outcome, or when you have checked where an outcome would be recorded and found
nothing. Never guess an outcome. Do not research private individuals. Give a short reason for every action.
Call finish with: what you found, whether you found the origin, and whether you found an outcome."""

TOOLS = [t for t in RESEARCH_TOOLS if t["name"] != "finish"] + [
    {"name": "finish", "description": "Stop tracing this issue.",
     "parameters": {"type": "object", "properties": {
         "summary": {"type": "string", "description": "Two or three sentences: origin and current state"},
         "origin_found": {"type": "boolean"}, "outcome_found": {"type": "boolean"}},
         "required": ["summary", "origin_found", "outcome_found"]}},
]


class LLMTracer(Tracer):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llm = LLMClient(cfg, role="tracer")
        self.page_chars = int(cfg.settings.get("agent", {}).get("page_chars_for_agent", 3500))

    def trace(self, issue: Issue, case: CaseCard, tools: ResearchTools) -> str:
        budget = self.cfg.settings.get("tracer", {})

        def handle(name: str, args: dict) -> str:
            if name == "web_search":
                hits = tools.search(str(args.get("query", "")), str(args.get("reason", "")), bool(args.get("recent")))
                return json.dumps([{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits], indent=1) \
                    if hits else "No results."
            if name == "open_page":
                return page_view(tools.open(str(args.get("url", "")), str(args.get("reason", ""))), self.page_chars)
            if name == "search_found_pages":
                found = tools.search_found(str(args.get("query", "")))
                return json.dumps(found, indent=1) if found else "No matching passages."
            return f"Unknown tool {name!r}."

        timeline = "\n".join(f"- {e.date or 'undated'}{' (publication date)' if e.date_is_publication else ''}: "
                             f"{status_label(e.event_type)}; {e.publisher}; {e.summary}" for e in issue.events)
        user = (f"Organisation: {case.organisation_name} (also: {', '.join(case.aliases) or 'none'})\n"
                f"Issue: {issue.label}\nCurrent status: {status_label(issue.current_status)}; "
                f"outcome found: {'yes' if issue.outcome_found else 'no'}; first reported: {issue.first_reported}; "
                f"independent sources: {issue.independent_sources}\nTimeline so far:\n{timeline}\n"
                f"Assessment date: {self.cfg.assessment_date}. Budget: about {budget.get('max_searches', 4)} searches "
                f"and {budget.get('max_pages', 5)} pages.")
        calls = self.llm.run_tools(SYSTEM, user, TOOLS, handle, max_steps=int(budget.get("max_turns", 10)))
        finish = next((c for c in reversed(calls) if c["name"] == "finish"), None)
        if not finish:
            return "Tracing stopped without a conclusion (budget used up)."
        a = finish.get("input") or {}
        return (f"{a.get('summary', '')} Origin found: {'yes' if a.get('origin_found') else 'no'}. "
                f"Outcome found: {'yes' if a.get('outcome_found') else 'no'}.").strip()
