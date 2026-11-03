"""
The tools AI agents may use on the web, with the guard rails built in. Used by the
researcher (stage 2) and the tracer (stage 3).

TOOLS
    search(query, reason, recent)   web search; recent=True keeps the last 12 months only
    open(url, reason)               read a page or PDF; ONLY URLs the agent was shown
                                    (search results, links on pages, results listed but not opened)
    search_found(query)             search INSIDE every document found so far (passage retrieval)
    overview()                      what has been found, including results listed but not opened,
                                    so one negative headline is not lost among positive ones

GUARD RAILS (code, not prompt): allowed URLs, search and page budgets, every action logged with
the agent's reason.
"""

from __future__ import annotations

from .config import Config
from .providers.base import ResearchTools, SearchHit, SearchProvider
from .retrieval import PassageIndex
from .schemas import CaseCard, QueryLog, SeenResult, SourceDocument


class ResearchSession(ResearchTools):

    def __init__(self, cfg: Config, provider: SearchProvider, case: CaseCard, documents: dict[str, SourceDocument],
                 logs: list[QueryLog], seen: list[SeenResult] | None = None, budget: dict | None = None,
                 step_offset: int = 0, term: str = "agent"):
        a = budget or cfg.settings.get("agent", {})
        r = cfg.settings.get("retrieval", {})
        self.provider, self.case, self.documents, self.logs = provider, case, documents, logs
        self.seen = seen or []
        self.max_searches, self.max_pages = int(a.get("max_searches", 12)), int(a.get("max_pages", 15))
        self.max_results = int(cfg.settings["search"].get("agent_results_per_search", 8))
        self.passages_k = int(r.get("passages_for_tool", 5))
        self.chunk = (int(r.get("chunk_chars", 1200)), int(r.get("chunk_overlap", 200)))
        self.searches = self.pages = 0
        self.step = step_offset
        self.term = term
        self._index: PassageIndex | None = None
        self._indexed = 0
        # URLs the agent is allowed to open: anything it has been shown.
        self.allowed: set[str] = (set(documents) | {l.url for d in documents.values() for l in d.links}
                                  | {s.url for s in self.seen})

    # ------------------------------------------------------------------ tools

    def search(self, query: str, reason: str, recent: bool = False) -> list[SearchHit]:
        if self.searches >= self.max_searches:
            raise RuntimeError("Search budget used up. Open pages you already found, or call finish.")
        self.searches += 1
        self.step += 1
        label = f"({self.term} search{', last 12 months' if recent else ''})"
        try:
            hits = self.provider.find(query, self.case, self.max_results, recent=recent)
        except Exception as exc:
            self.logs.append(QueryLog(query=query, term=label, round=self.step, action="search",
                                      reason=reason, error=f"{type(exc).__name__}: {exc}"))
            raise
        self.allowed.update(h.url for h in hits)
        known = [self.documents[h.url].doc_id for h in hits if h.url in self.documents]
        self.logs.append(QueryLog(query=query, term=label, round=self.step, action="search",
                                  reason=reason, hit_doc_ids=known))
        return hits

    def open(self, url: str, reason: str) -> SourceDocument:
        url = url.strip()
        if url not in self.allowed:
            raise PermissionError("That URL did not appear in any search result or page. Only shown URLs can be opened.")
        if url in self.documents:
            return self.documents[url]
        if self.pages >= self.max_pages:
            raise RuntimeError("Page budget used up. Call finish.")
        self.pages += 1
        self.step += 1
        try:
            doc = self.provider.fetch(url, self.case)
        except Exception as exc:
            self.logs.append(QueryLog(query=url, term="(open page)", round=self.step, action="follow_link",
                                      reason=reason, error=f"{type(exc).__name__}: {exc}"))
            raise
        self.documents.setdefault(url, doc)
        doc = self.documents[url]
        self.allowed.update(l.url for l in doc.links)
        self.logs.append(QueryLog(query=url, term="(open page)", round=self.step, action="follow_link",
                                  reason=reason, hit_doc_ids=[doc.doc_id]))
        return doc

    def search_found(self, query: str) -> list[dict]:
        """Passage retrieval over every document found so far (the per-case index)."""
        docs = list(self.documents.values())
        if self._index is None or self._indexed != len(docs):
            self._index = PassageIndex(*self.chunk)
            for d in docs:
                self._index.add(d.doc_id, f"{d.title}\n{d.text}")
            self._indexed = len(docs)
        by_id = {d.doc_id: d for d in docs}
        return [{"doc_id": p.doc_id, "title": by_id[p.doc_id].title, "url": by_id[p.doc_id].url,
                 "date": str(by_id[p.doc_id].published_date), "passage": p.text[:700]}
                for p in self._index.search(query, self.passages_k)]

    def overview(self) -> str:
        lines = ["Standard searches (term: number of pages opened):",
                 ", ".join(f"{q.term}: {len(q.hit_doc_ids)}" + (" [failed]" if q.error else "")
                           for q in self.logs if q.round == 0)]
        lines.append("\nPages already read (id | publisher | date | title | url | opening text):")
        for d in list(self.documents.values())[:80]:
            lines.append(f"{d.doc_id} | {d.publisher} | {d.published_date or 'undated'} | {d.title[:90]} | {d.url} | "
                         f"{d.text[:200].replace(chr(10), ' ')}")
        unopened = [s for s in self.seen if s.url not in self.documents]
        if unopened:
            lines.append("\nResults listed by the standard searches but NOT opened (title | url | snippet). "
                         "Scan these for critical headlines that positive coverage may have pushed down:")
            for s in unopened[:150]:
                lines.append(f"{s.title[:100]} | {s.url} | {s.snippet[:160].replace(chr(10), ' ')}")
        return "\n".join(lines)
