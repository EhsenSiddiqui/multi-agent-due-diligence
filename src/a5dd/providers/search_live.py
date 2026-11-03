"""
Live web search and page fetching, for real organisations.

TWO SEARCH ENGINES (same output, chosen in settings.yaml -> providers.live_search)
    tavily      Tavily search API. Best results; needs TAVILY_API_KEY (free tier available).
    duckduckgo  DuckDuckGo through the `ddgs` package. No key needed; slower and may rate-limit.
    auto        tavily if TAVILY_API_KEY is set, otherwise duckduckgo.

HOW A FIXED (guide 1.3) SEARCH WORKS
    1. Ask the engine for '"<search name>" <term>' and take the result list.
    2. Keep results whose title or snippet mentions the organisation (cuts obvious noise).
    3. Download the top few (search.fetch_per_query), in parallel, and scrape each one with
       parsing.build_document: the SAME scraper used on the offline test web.
    The AI researcher can also search freely (find) and open any page that appeared in a
    result or as a link (fetch).

RESPONSIBLE FETCHING (WebFetcher)
    Identifies itself in the User-Agent, obeys robots.txt, only downloads HTML, PDF or text,
    stops at 10 MB, and never logs in or bypasses paywalls. Paywalled pages come back short
    and are simply less useful; that shows up as a gap, not an error.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from ..parsing import build_document, load_sources
from ..schemas import CaseCard, SourceDocument
from ..text_utils import mentions_identity
from .base import SearchHit, SearchProvider, SearchQuery

USER_AGENT = ("Mozilla/5.0 (compatible; A5-DueDiligence/0.3; WaterAid ethical due-diligence research prototype; "
              "Team Glacier)")
ALLOWED_TYPES = ("text/html", "application/pdf", "application/xhtml", "text/plain")
MAX_BYTES = 10_000_000


class WebFetcher:
    """Downloads and scrapes pages. One instance per run; caches by URL; thread-safe."""

    def __init__(self, cfg, retrieved_by: str):
        import httpx  # Optional dependency: pip install -e ".[live]"
        self._http = httpx.Client(timeout=float(cfg.settings["search"].get("fetch_timeout_seconds", 20)),
                                  follow_redirects=True,
                                  headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"})
        self._sources = load_sources(str(cfg.path("sources_file")))
        self._respect_robots = bool(cfg.settings["search"].get("respect_robots_txt", True))
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._cache: dict[str, SourceDocument] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self.prefix = "W"
        self.retrieved_by = retrieved_by

    def _allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self._http.get(f"{host}/robots.txt", timeout=8)
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
                self._robots[host] = parser
            except Exception:
                self._robots[host] = None  # robots.txt unreachable: treat as no restrictions.
        parser = self._robots[host]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def fetch(self, url: str, case: CaseCard) -> SourceDocument:
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError(f"Not a web address: {url}")
        with self._lock:
            if url in self._cache:
                return self._cache[url]
        if not self._allowed(url):
            raise PermissionError(f"robots.txt asks crawlers not to open {url}")
        with self._http.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            if ctype and not any(t in ctype for t in ALLOWED_TYPES):
                raise ValueError(f"Unsupported content type {ctype!r}")
            raw = b""
            for chunk in resp.iter_bytes():
                raw += chunk
                if len(raw) > MAX_BYTES:
                    raise ValueError("Page larger than 10 MB")
            final_url = str(resp.url)
        with self._lock:
            if url in self._cache:
                return self._cache[url]
            self._counter += 1
            doc_id = f"{self.prefix}{self._counter:03d}"
        doc = build_document(doc_id, final_url, ctype, raw, case, self._sources, self.retrieved_by)
        if len(doc.text) < 200:
            doc.parse_notes.append("very little text extracted (paywall, script-only page or scanned PDF)")
        with self._lock:
            self._cache[url] = doc
            self._cache[final_url] = doc
        return doc


class LiveSearch(SearchProvider):
    """Shared logic for live engines. Subclasses implement `_engine(text, n) -> list[SearchHit]`."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.fetcher = WebFetcher(cfg, self.name)
        s = cfg.settings["search"]
        self.fetch_per_query = int(s.get("fetch_per_query", 3))
        self.results_per_query = int(s.get("live_results_per_query", 8))

    def _engine(self, text: str, n: int, recent: bool = False) -> list[SearchHit]:
        raise NotImplementedError

    def find(self, text: str, case: CaseCard, max_results: int, recent: bool = False) -> list[SearchHit]:
        return self._engine(text, max_results, recent)

    def set_id_prefix(self, prefix: str) -> None:
        self.fetcher.prefix = prefix

    def search(self, query: SearchQuery, case: CaseCard, max_results: int) -> list[SourceDocument]:
        name = query.entity or case.search_name or case.organisation_name
        extra = " ".join(query.patterns) if query.entity else query.term.replace(" / ", " OR ")
        hits = self._engine(f'"{name}" {extra}'.strip(), self.results_per_query)
        relevant = [h for h in hits if mentions_identity(f"{h.title} {h.snippet} {h.url}", case)
                    or (query.entity and query.entity.lower() in f"{h.title} {h.snippet}".lower())]
        chosen = relevant[: self.fetch_per_query]
        self._unopened = [h for h in hits if h not in chosen]  # Shown to the researcher later.
        with ThreadPoolExecutor(max_workers=max(1, len(chosen))) as pool:
            results = list(pool.map(lambda h: self._safe_fetch(h.url, case), chosen))
        return [d for d in results if d is not None]

    def _safe_fetch(self, url: str, case: CaseCard) -> SourceDocument | None:
        try:
            return self.fetcher.fetch(url, case)
        except Exception:
            return None  # One unreachable page does not fail the search; the query is still logged.

    def fetch(self, url: str, case: CaseCard) -> SourceDocument:
        return self.fetcher.fetch(url, case)

    def coverage_notes(self) -> list[str]:
        return [
            f"Live web search ({self.name}): paywalled articles, court databases and company registries "
            "are only covered where public pages link to them.",
            f"For each standard search, the top {self.fetch_per_query} relevant results were opened; "
            "guide 1.3 suggests scanning the first two pages of results.",
            "Sanctions and enforcement lists (e.g. OpenSanctions) were not checked directly.",
            "Non-English sources and scanned PDFs (which need OCR) may be missed.",
        ]


class TavilySearch(LiveSearch):
    URL = "https://api.tavily.com/search"

    def __init__(self, cfg):
        self.api_key = os.environ.get("TAVILY_API_KEY")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is not set. Set it, or use providers.live_search: duckduckgo.")
        super().__init__(cfg)

    def _engine(self, text: str, n: int, recent: bool = False) -> list[SearchHit]:
        body = {"query": text, "max_results": n, "search_depth": "basic"}
        if recent:
            body["time_range"] = "year"
        resp = self.fetcher._http.post(self.URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
        resp.raise_for_status()
        return [SearchHit(r.get("title", ""), r["url"], (r.get("content") or "")[:400])
                for r in resp.json().get("results", []) if r.get("url")]


class DuckDuckGoSearch(LiveSearch):
    """No key needed. DuckDuckGo limits request rates, so searches are spaced out."""

    _last = 0.0
    _lock = threading.Lock()

    def __init__(self, cfg):
        super().__init__(cfg)
        from ddgs import DDGS  # Optional dependency: pip install -e ".[live]"
        self._ddgs = DDGS()
        self._gap = float(cfg.settings["search"].get("duckduckgo_seconds_between_searches", 1.5))

    def _engine(self, text: str, n: int, recent: bool = False) -> list[SearchHit]:
        with self._lock:
            wait = self._gap - (time.monotonic() - DuckDuckGoSearch._last)
            if wait > 0:
                time.sleep(wait)
            DuckDuckGoSearch._last = time.monotonic()
        results = self._ddgs.text(text, max_results=n, timelimit="y" if recent else None)
        return [SearchHit(r.get("title", ""), r.get("href") or r.get("url", ""), (r.get("body") or "")[:400])
                for r in results if r.get("href") or r.get("url")]


def auto_live_search(cfg) -> LiveSearch:
    """providers.live_search: auto -> Tavily if a key is set, otherwise DuckDuckGo."""
    return TavilySearch(cfg) if os.environ.get("TAVILY_API_KEY") else DuckDuckGoSearch(cfg)
