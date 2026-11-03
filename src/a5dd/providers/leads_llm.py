"""
AGENT 1: the Researcher (an LLM with tools).  Switch: providers.lead_finder: llm

After the fixed guide 1.3 searches, the researcher gets an overview of every page found
and then works on its own, in a real tool-use loop:

    web_search(query, reason)   run any search it thinks useful
    open_page(url, reason)      read a page that appeared in a result or as a link
    finish(summary, open_questions)

It decides which leads matter, follows them to primary sources (regulator notices,
court records, filings), checks subsidiaries and former names, looks for how
allegations ended (fines, rulings, settlements, company responses), and stops when it
judges the six questions covered. Every action carries its own reason; stage 2 logs them.

GUARD RAILS (in code, not in the prompt)
  - open_page only accepts URLs that appeared in a search result or on a page already
    read: the model cannot invent sources;
  - budgets (turns, searches, pages) come from settings.yaml (agent.*), not the model;
  - page text sent back to the model is trimmed, so one huge page cannot swamp it.
"""

from __future__ import annotations

import json

from ..schemas import CaseCard, SourceDocument
from .base import LeadFinder, ResearchTools, ToolResearcher
from .llm_client import LLMClient

SYSTEM = """You are the lead researcher in a due-diligence team at a water charity (WaterAid).
Your job: find what the public record says about ONE organisation, so colleagues can answer six questions:
Q1 links to excluded or high-risk sectors (arms, tobacco, pornography, fossil fuels, gambling, extractives, alcohol)
Q2 harm to communities' water, sanitation or hygiene (pollution, water use, access to water, health)
Q3 operations or supply chain in low- or middle-income countries
Q4 criminal or regulatory investigations in the last 10 years, and their outcomes
Q5 campaigns or allegations by media, NGOs or the public
Q6 anything else relevant to the charity's reputation or values (labour, human rights, governance)

How to work:
- Start from the overview of what the standard searches found. Find the GAPS and the LEADS.
- Prefer primary sources: regulator and court records, company filings and annual/sustainability reports,
  the organisation's own statements. Use news and NGO reports to find them, and to see allegations.
- For every serious allegation, look for how it ended: ruling, fine, settlement, dismissal, company response.
- Check subsidiaries, joint ventures, former names and recent ownership changes.
- Search on water specifically (pollution, tailings, water use, community water access) and on the
  countries where the organisation operates or sources from.
- Never research private individuals (employees, community members). Executives only in their company role.
- Open only pages likely to contain evidence. Give a short reason for every action.
- Positive coverage is not evidence of no risk. Press releases and praise are repeated everywhere; a single
  critical report can sit far down the results. Scan the results listed but not opened for critical headlines,
  and search where problems are recorded: regulators, courts, NGO trackers (e.g. Business & Human Rights
  Resource Centre), and local-language news in the countries involved.
- Mind the time. Use recent=true to check the last 12 months, so the current state is not missed.
  An article's date is not the event's date.
- Twenty articles repeating one report are one source: look for the ORIGINAL report or record.
- Use search_found_pages to look inside long documents already found (annual or sustainability reports).
- When the six questions are reasonably covered, or the budget is nearly used, call finish.
  In open_questions, list what you could NOT establish."""

TOOLS = [
    {"name": "web_search", "description": "Search the web. Returns titles, URLs and snippets (pages are not opened).",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "The search query"},
         "reason": {"type": "string", "description": "Why this search, in one short sentence"},
         "recent": {"type": "boolean", "description": "true = only the last 12 months"}},
         "required": ["query", "reason"]}},
    {"name": "search_found_pages", "description": "Search INSIDE the pages and PDFs already found; returns the best "
     "matching passages with their document. Use it for long reports.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "open_page", "description": "Open and read a page or PDF. Only URLs from search results or page links.",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string"},
         "reason": {"type": "string", "description": "What you expect to learn, in one short sentence"}},
         "required": ["url", "reason"]}},
    {"name": "finish", "description": "Stop researching.",
     "parameters": {"type": "object", "properties": {
         "summary": {"type": "string", "description": "Three to six sentences: what the research found"},
         "open_questions": {"type": "array", "items": {"type": "string"},
                            "description": "What could not be established"}},
         "required": ["summary", "open_questions"]}},
]


class LLMResearcher(ToolResearcher):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llm = LLMClient(cfg, role="researcher")
        self.page_chars = int(cfg.settings.get("agent", {}).get("page_chars_for_agent", 3500))

    def research(self, case: CaseCard, tools: ResearchTools) -> tuple[str, list[str]]:
        budget = self.cfg.settings.get("agent", {})

        def handle(name: str, args: dict) -> str:
            if name == "web_search":
                hits = tools.search(str(args.get("query", "")), str(args.get("reason", "")), bool(args.get("recent")))
                return json.dumps([{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits], indent=1) \
                    if hits else "No results."
            if name == "search_found_pages":
                found = tools.search_found(str(args.get("query", "")))
                return json.dumps(found, indent=1) if found else "No matching passages."
            if name == "open_page":
                doc = tools.open(str(args.get("url", "")), str(args.get("reason", "")))
                return page_view(doc, self.page_chars)
            return f"Unknown tool {name!r}."

        user = (f"Organisation: {case.organisation_name}\n"
                f"Also known as / subsidiaries: {', '.join(case.aliases) or 'none listed'}\n"
                f"Own websites: {', '.join(case.websites) or 'unknown'}\n"
                f"What staff told us: sectors {case.declared_sectors or 'not stated'}; countries "
                f"{case.countries_of_operation or 'not stated'}. {case.notes}\n"
                f"Assessment date: {self.cfg.assessment_date}. Look back 10 years for investigations.\n"
                f"Budget: about {budget.get('max_searches', 12)} searches and {budget.get('max_pages', 15)} pages.\n\n"
                f"What the standard searches found:\n{tools.overview()}")
        calls = self.llm.run_tools(SYSTEM, user, TOOLS, handle, max_steps=int(budget.get("max_turns", 20)))
        finish = next((c for c in reversed(calls) if c["name"] == "finish"), None)
        if finish:
            args = finish.get("input") or {}
            return str(args.get("summary", "")), [str(q) for q in args.get("open_questions", [])]
        return "The researcher stopped without a summary (budget used up).", ["Research budget ran out before finishing."]


def page_view(doc: SourceDocument, chars: int) -> str:
    """What an agent sees when it opens a page: metadata, trimmed text and the page's links."""
    links = [{"text": l.text[:80], "url": l.url} for l in doc.links[:40]]
    return json.dumps({"doc_id": doc.doc_id, "title": doc.title, "publisher": doc.publisher,
                       "type": doc.source_type, "date": str(doc.published_date) if doc.published_date else None,
                       "text": doc.text[:chars] + (" [...] (use search_found_pages to look further inside)"
                                                   if len(doc.text) > chars else ""),
                       "links": links}, indent=1)


class NoLeadFinder(LeadFinder):
    """Turns the agent off: only the fixed guide 1.3 searches run."""

    def propose(self, documents, case, done_urls, done_searches, limit):
        return []
