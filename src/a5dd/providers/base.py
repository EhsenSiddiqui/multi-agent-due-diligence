"""
Interfaces (abstract base classes) for the swappable parts of the pipeline.

A stage never knows WHICH implementation it is using. It asks the factory in
providers/__init__.py for "the search provider" and calls `.search(...)`.
To add a better implementation (e.g. a new search API), write a class that
inherits from the right base class below, then register it in
providers/__init__.py. No stage file changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..schemas import CaseCard, EvidenceItem, IntakeResult, QuestionAnswer, SourceDocument


@dataclass
class SearchQuery:
    """
    One search request from stage 2.
      text:     human-readable query, as a person would type it ("<name> Bribery").
      term:     the template b search term ("Bribery"), or "" for the profile query.
      patterns: word stems for offline matching (from criteria.json).
    Each provider uses whichever fields it needs.
    """
    text: str
    term: str = ""
    patterns: list[str] = field(default_factory=list)
    entity: str = ""   # Who to search for; empty = the organisation being checked.


@dataclass
class SearchHit:
    """One search-engine result before the page is opened: what a person sees in the result list."""
    title: str
    url: str
    snippet: str = ""


class SearchProvider(ABC):
    """Step 2: turns a query into source documents."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def search(self, query: SearchQuery, case: CaseCard, max_results: int) -> list[SourceDocument]:
        """Return documents for one query. Raise an exception if the source is unreachable."""

    def fetch(self, url: str, case: CaseCard) -> SourceDocument:
        """Open one URL (used when the agent follows a link). Raise if it cannot be reached."""
        raise NotImplementedError(f"{self.name} cannot open links")

    def find(self, text: str, case: CaseCard, max_results: int, recent: bool = False) -> list[SearchHit]:
        """Free-text search returning the result list only (titles, URLs, snippets), without opening pages.
        Used by the AI agents, which decide themselves which results are worth opening.
        recent=True: only results from about the last 12 months."""
        raise NotImplementedError(f"{self.name} has no free-text search")

    def take_unopened(self) -> list[SearchHit]:
        """Results the last search() listed but did not open (live web only). Cleared when read."""
        hits, self._unopened = getattr(self, "_unopened", []), []
        return hits

    def set_id_prefix(self, prefix: str) -> None:
        """Prefix for IDs of newly fetched live pages, so stage 3's pages never clash with stage 2's."""

    def coverage_notes(self) -> list[str]:
        """Plain-language list of what this provider cannot see. Shown as gaps in the report."""
        return []


@dataclass
class Lead:
    """
    One next action proposed by a LeadFinder (the agent's "what should I look at next?").
      action: "follow_link" (open `target` as a URL) or "search" (search for `target` as an entity)
      patterns: extra words the search results must contain (search only)
      reason: plain-language reason, shown in the agent log for reviewers
    """
    action: str
    target: str
    reason: str
    patterns: list[str] = field(default_factory=list)


class LeadFinder(ABC):
    """Step 2 (agent part): reads what has been found so far and proposes what to do next."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def propose(self, documents: list[SourceDocument], case: CaseCard, done_urls: set[str],
                done_searches: set[str], limit: int) -> list[Lead]:
        """Return up to `limit` new leads. Return [] to stop."""


class ResearchTools(ABC):
    """What stage 2 lets an AI researcher do. Guard rails live in the implementation, not the model."""

    @abstractmethod
    def search(self, query: str, reason: str, recent: bool = False) -> list[SearchHit]:
        """Run a web search; returns the result list. recent=True: last 12 months only."""

    @abstractmethod
    def open(self, url: str, reason: str) -> SourceDocument:
        """Open a page that appeared in a search result or as a link. Raises for anything else."""

    @abstractmethod
    def search_found(self, query: str) -> list[dict]:
        """Search INSIDE the documents found so far; returns the best passages."""

    @abstractmethod
    def overview(self) -> str:
        """Plain-text summary of everything found so far (for the agent's first prompt)."""


class ToolResearcher(ABC):
    """Step 2 (agent part), the AI version: runs its own tool-use loop instead of proposing rounds of leads."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def research(self, case: CaseCard, tools: ResearchTools) -> tuple[str, list[str]]:
        """Research until done or out of budget. Returns (summary of what was found, open questions)."""


class Tracer(ABC):
    """Stage 3 agent: traces one serious issue back to its origin and forward to its outcome."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def trace(self, issue, case: CaseCard, tools: ResearchTools) -> str:
        """Search for the issue's origin and outcome. Returns a short note of what was found."""


class EvidenceExtractor(ABC):
    """Step 3: turns one source document into zero or more labelled evidence items."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def extract(self, doc: SourceDocument, case: CaseCard) -> list[EvidenceItem]:
        """Return evidence items. Stage 3 re-checks every item, whatever produced it."""


class QuestionJudge(ABC):
    """Step 4: answers one core question (template b) from the evidence table."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.issues: list = []  # Issue timelines from stage 3, set by stage 4 before judging.

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def judge(
        self,
        question: dict[str, Any],
        evidence: list[EvidenceItem],
        intake: IntakeResult,
    ) -> QuestionAnswer:
        """Return the answer, the proposed risk call and staff questions for one question."""

    def reconsider(self, question: dict[str, Any], answer: QuestionAnswer, challenge: dict[str, Any],
                   evidence: list[EvidenceItem], intake: IntakeResult) -> QuestionAnswer:
        """Answer a challenger's objection. Default (rule-based judge): keep the answer unchanged."""
        return answer


class Challenger(ABC):
    """Step 4, second agent: argues against the judge's call, so a single reading cannot go unchecked."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.issues: list = []

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def challenge(self, question: dict[str, Any], answer: QuestionAnswer, evidence: list[EvidenceItem],
                  intake: IntakeResult) -> dict[str, Any] | None:
        """Return {"proposed_call", "argument", "evidence_ids"} to object, or None to agree."""
