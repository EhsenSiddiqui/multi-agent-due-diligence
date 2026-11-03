"""
STAGE 2: Research.  Reads 01_intake.json  ->  writes 02_search.json

PART A: the fixed guide 1.3 protocol (code, same every time)
    One "profile" search for the organisation, then "<name> + term" for each of the 32
    template b search terms. Consistent, auditable coverage, whatever the AI does next.

PART B: the agent (follow-up research)
    AI mode (lead_finder: llm): the RESEARCHER agent works in its own tool-use loop. It sees
        an overview of Part A, INCLUDING results that were listed but not opened (so one critical
        headline is not lost among positive coverage), then searches (optionally the last 12 months
        only), opens pages, follows links and searches inside what it found, until it calls finish
        or runs out of budget. Its summary and open questions are saved.
    Rule mode (lead_finder: heuristic): rules propose rounds of follow-ups (link to a regulator
        PDF, search a named buyer). The offline baseline.

Where the pages come from (providers.search):
    auto = the offline test web for the fictional cases (data/cases/<case>/web), the live web
           (Tavily or DuckDuckGo) for real organisations.

Every action, including failures and the agent's reason for it, is logged. That log is the
coverage record in the assessment file and the "agent log" reviewers can audit.
"""

from __future__ import annotations

from ..config import Config
from ..io_utils import read_stage, write_stage
from ..providers import get_lead_finder, get_search
from ..providers.base import SearchQuery, ToolResearcher
from ..research_tools import ResearchSession  # noqa: F401  (re-exported for older imports)
from ..schemas import IntakeResult, QueryLog, SearchResults, SeenResult, SourceDocument

STAGE = "02_search"


class SearchFailed(RuntimeError):
    """Every search failed. Stopping is safer than writing an assessment that looks clean."""


def build_queries(cfg: Config, name: str) -> list[SearchQuery]:
    """Part A: the profile query plus one query per template b search term."""
    queries = [SearchQuery(text=name)]
    for t in cfg.criteria["search_terms"]:
        queries.append(SearchQuery(text=f"{name} {t['term']}", term=t["term"], patterns=t["patterns"]))
    return queries


def run(cfg: Config, case_id: str) -> SearchResults:
    intake = read_stage(cfg.path("results_dir"), case_id, "intake", IntakeResult)
    case = intake.case
    provider = get_search(cfg, case_id)
    lead_finder = get_lead_finder(cfg)
    max_results = int(cfg.settings["search"]["max_results_per_query"])
    max_docs = int(cfg.settings["search"].get("max_documents_from_standard_searches", 1000))
    agent_cfg = cfg.settings.get("agent", {})

    logs: list[QueryLog] = []
    documents: dict[str, SourceDocument] = {}  # url -> document (de-duplicated)
    seen: dict[str, SeenResult] = {}           # url -> result listed but not opened

    def add(docs: list[SourceDocument]) -> list[str]:
        """Store new documents; return the IDs of all hits (new or already known)."""
        for d in docs:
            if d.url in documents or len(documents) < max_docs:
                documents.setdefault(d.url, d)
        return [documents[d.url].doc_id for d in docs if d.url in documents]

    # ---------------- Part A: fixed protocol
    for q in build_queries(cfg, case.search_name or case.organisation_name):
        try:
            ids = add(provider.search(q, case, max_results))
            logs.append(QueryLog(query=q.text, term=q.term or "(profile)", hit_doc_ids=ids))
            for h in provider.take_unopened():
                seen.setdefault(h.url, SeenResult(title=h.title, url=h.url, snippet=h.snippet, query=q.text))
        except Exception as exc:  # A failed source is recorded as a gap, never hidden.
            logs.append(QueryLog(query=q.text, term=q.term or "(profile)", error=f"{type(exc).__name__}: {exc}"))

    # A run where nothing could be searched must never produce "no risk" answers.
    if logs and all(q.error for q in logs):
        first = logs[0].error or ""
        hint = (" Install the missing library with:  pip install -e \".[dev]\"" if "No module named" in first else "")
        raise SearchFailed(f"All {len(logs)} searches failed for {case_id}, so no assessment was written. "
                           f"First error: {first}.{hint}")

    # ---------------- Part B: the agent
    summary, open_questions = "", []
    if isinstance(lead_finder, ToolResearcher):
        session = ResearchSession(cfg, provider, case, documents, logs, seen=list(seen.values()))
        summary, open_questions = lead_finder.research(case, session)
    else:
        _rule_rounds(lead_finder, provider, case, documents, logs, add, max_results, agent_cfg)

    result = SearchResults(
        stage=STAGE,
        case_id=case_id,
        producer=f"{provider.name} + {lead_finder.name}",
        fictional_data=intake.fictional_data,
        queries=logs,
        documents=list(documents.values()),
        coverage_notes=provider.coverage_notes(),
        seen_not_opened=[s for u, s in seen.items() if u not in documents],
        research_summary=summary,
        open_questions=open_questions,
    )
    write_stage(cfg.path("results_dir"), case_id, "search", result)
    return result


def _rule_rounds(lead_finder, provider, case, documents, logs, add, max_results, agent_cfg) -> None:
    """Rule-based baseline: rounds of proposed follow-ups until nothing new turns up."""
    done_urls = set(documents)
    done_searches: set[str] = set()
    for round_no in range(1, int(agent_cfg.get("max_rounds", 0)) + 1):
        leads = lead_finder.propose(list(documents.values()), case, done_urls, done_searches,
                                    int(agent_cfg.get("max_actions_per_round", 5)))
        if not leads:
            break
        new_this_round = 0
        for lead in leads:
            before = len(documents)
            try:
                if lead.action == "follow_link":
                    done_urls.add(lead.target)
                    ids = add([provider.fetch(lead.target, case)])
                    logs.append(QueryLog(query=lead.target, term="(follow link)", hit_doc_ids=ids,
                                         round=round_no, action="follow_link", reason=lead.reason))
                else:
                    done_searches.add(lead.target)
                    q = SearchQuery(text=f"{lead.target} {' '.join(lead.patterns)}".strip(), entity=lead.target,
                                    patterns=lead.patterns, term="(follow-up search)")
                    ids = add(provider.search(q, case, max_results))
                    logs.append(QueryLog(query=q.text, term="(follow-up search)", hit_doc_ids=ids,
                                         round=round_no, action="search", reason=lead.reason))
            except Exception as exc:
                logs.append(QueryLog(query=lead.target, term=f"({lead.action.replace('_', ' ')})", round=round_no,
                                     action=lead.action, reason=lead.reason, error=f"{type(exc).__name__}: {exc}"))
            new_this_round += len(documents) - before
            done_urls.update(documents)
        if new_this_round == 0:
            break  # Nothing new: the agent has run out of useful leads.
