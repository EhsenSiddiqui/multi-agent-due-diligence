"""
Offline "simulated web" search engine (DEMO implementation).

The fictional web for each case lives in data/cases/<case_id>/web/:
    index.json            every URL that exists, its file, its type, and whether a
                          search engine has indexed it
    <domain>/<path>.html  realistic web pages (menus, cookie banners, ads, related stories)
    <domain>/<path>.pdf   realistic PDFs (letterheads, footers, page numbers)

HOW IT WORKS
    search(): looks through INDEXED pages only, like a search engine. A page is a hit if
              it mentions the entity's short name and (for a template b term) one of the
              term's patterns. Results are ranked by how often the patterns appear.
    fetch():  opens ANY page in the index, indexed or not, like clicking a link. That is
              how the agent reaches documents a search engine does not surface (e.g. a
              regulator's PDF notice linked from an NGO post).
    Both return pages parsed by parsing.build_document: the same unstructured-to-
    structured scraping a live provider uses.

SIMPLIFIED
    No network, a handful of pages, keyword ranking. Real engines rank by relevance and
    return far more noise.

BETTER
    search_tavily.py: live search + live fetching of real pages, same parser, same output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..parsing import build_document, load_sources
from ..schemas import CaseCard, SourceDocument
from .base import SearchHit, SearchProvider, SearchQuery


def short_name(entity: str) -> str:
    """The distinctive first word of a name, e.g. 'Altiplano' from 'Altiplano Resources Inc.'."""
    return entity.split()[0]


def _mentions(text: str, word: str) -> int:
    """How many times a word stem appears at word starts (case-insensitive)."""
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(word.lower()), text.lower()))


class SimulatedWebSearch(SearchProvider):

    def __init__(self, cfg):
        super().__init__(cfg)
        self._sources = load_sources(str(cfg.path("sources_file")))
        self._cache: dict[str, SourceDocument] = {}  # url -> parsed document

    # ------------------------------------------------------------------ the fictional web

    def _web(self, case: CaseCard) -> tuple[Path, list[dict]]:
        web = self.cfg.path("cases_dir") / case.case_id / "web"
        index = json.loads((web / "index.json").read_text(encoding="utf-8"))["pages"]
        return web, index

    def _open(self, page: dict, web: Path, case: CaseCard) -> SourceDocument:
        """Read the raw file and scrape it (cached, so each file is parsed once per run)."""
        if page["url"] not in self._cache:
            raw = (web / page["file"]).read_bytes()
            self._cache[page["url"]] = build_document(page["id"], page["url"], page["content_type"], raw,
                                                      case, self._sources, self.name)
        return self._cache[page["url"]]

    # ------------------------------------------------------------------ interface

    def search(self, query: SearchQuery, case: CaseCard, max_results: int) -> list[SourceDocument]:
        web, index = self._web(case)
        name = short_name(query.entity or case.organisation_name)
        scored = []
        for page in index:
            if not page["indexed"]:
                continue  # Not discoverable by search: only reachable by following a link.
            doc = self._open(page, web, case)
            haystack = f"{doc.title}\n{doc.text}"
            if not _mentions(haystack, name):
                continue
            score = sum(_mentions(haystack, p) for p in query.patterns) if query.patterns else 1
            if score:
                scored.append((score, doc))
        scored.sort(key=lambda pair: -pair[0])
        return [doc for _, doc in scored[:max_results]]

    def fetch(self, url: str, case: CaseCard) -> SourceDocument:
        web, index = self._web(case)
        for page in index:
            if page["url"] == url:
                return self._open(page, web, case)
        raise LookupError(f"404 Not Found: {url}")

    def find(self, text: str, case: CaseCard, max_results: int, recent: bool = False) -> list[SearchHit]:
        """Free-text search for the AI researcher: the first word is the name to look for,
        the other words rank the results (like a search engine, roughly)."""
        words = [w.strip('"').lower() for w in text.split() if len(w.strip('"')) > 2]
        if not words:
            return []
        docs = self.search(SearchQuery(text=text, entity=words[0], patterns=words[1:]), case, max_results)
        if not docs and len(words) > 1:  # Nothing matched every word: fall back to the name alone.
            docs = self.search(SearchQuery(text=text, entity=words[0]), case, max_results)
        if recent:  # Last 12 months before the assessment date.
            from datetime import timedelta
            cutoff = self.cfg.assessment_date - timedelta(days=365)
            docs = [d for d in docs if d.published_date and d.published_date >= cutoff]
        return [SearchHit(d.title, d.url, d.text[:300]) for d in docs]

    def coverage_notes(self) -> list[str]:
        return [
            "Court databases, company registries and sanctions lists were not searched directly.",
            "Up to 10 results per search were reviewed; guide 1.3 suggests the first two pages of 'All' and 'News'.",
            "Non-English sources and scanned PDFs (which need OCR) were not covered.",
        ]
