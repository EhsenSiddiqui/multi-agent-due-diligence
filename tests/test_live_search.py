"""
Live web search and fetching, tested against a fake internet (httpx MockTransport):
no network needed. Checks relevance filtering, robots.txt, content types and source labels.
"""

import pytest

httpx = pytest.importorskip("httpx")

from a5dd.parsing import classify_source, load_sources  # noqa: E402
from a5dd.providers.base import SearchHit, SearchQuery  # noqa: E402
from a5dd.providers.search_live import LiveSearch  # noqa: E402
from a5dd.schemas import CaseCard  # noqa: E402

CASE = CaseCard(case_id="x", organisation_name="Barrick Mining Corporation", search_name="Barrick",
                aliases=["Barrick Gold", "Barrick"], relationship_type="corporate_donor",
                websites=["barrick.com"], fictional=False)

PAGES = {
    "https://www.reuters.com/a": ("text/html", "<html><head><title>Barrick fined</title>"
                                  "<meta property='article:published_time' content='2024-05-02'></head>"
                                  "<body><article><p>Barrick Gold was fined by a regulator in 2024 for a "
                                  "spill.</p></article></body></html>"),
    "https://www.barrick.com/news/statement": ("text/html", "<html><body><article><p>Barrick says it complies with "
                                               "all permits.</p></article></body></html>"),
    "https://blocked.example/page": ("text/html", "<p>Barrick</p>"),
    "https://files.example/video.mp4": ("video/mp4", "binary"),
}


def handler(request):
    url = str(request.url)
    if url.endswith("/robots.txt"):
        body = "User-agent: *\nDisallow: /" if "blocked.example" in url else ""
        return httpx.Response(200, text=body)
    if url in PAGES:
        ctype, body = PAGES[url]
        return httpx.Response(200, headers={"content-type": ctype}, text=body)
    return httpx.Response(404)


class FakeEngine(LiveSearch):
    def _engine(self, text, n, recent=False):
        return [SearchHit("Barrick fined", "https://www.reuters.com/a", "Barrick Gold was fined"),
                SearchHit("Gold prices rise", "https://other.example/gold", "Markets news"),  # not about Barrick
                SearchHit("Barrick statement", "https://www.barrick.com/news/statement", "Barrick says")]


@pytest.fixture
def engine(cfg):
    e = FakeEngine(cfg)
    e.fetcher._http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return e


def test_search_keeps_relevant_results_and_labels_sources(engine):
    docs = engine.search(SearchQuery(text="Barrick Fraud", term="Fraud"), CASE, 10)
    by_url = {d.url: d for d in docs}
    assert set(by_url) == {"https://www.reuters.com/a", "https://www.barrick.com/news/statement"}
    news = by_url["https://www.reuters.com/a"]
    assert news.publisher == "Reuters" and news.source_type == "news" and str(news.published_date) == "2024-05-02"
    own = by_url["https://www.barrick.com/news/statement"]
    assert own.origin.value == "partner_organisation" and own.source_type == "company_statement"


def test_fetcher_obeys_robots_and_rejects_non_documents(engine):
    with pytest.raises(PermissionError):
        engine.fetch("https://blocked.example/page", CASE)
    with pytest.raises(ValueError):
        engine.fetch("https://files.example/video.mp4", CASE)
    with pytest.raises(ValueError):
        engine.fetch("file:///etc/passwd", CASE)


def test_subdomains_match_known_sources(cfg):
    sources = load_sources(str(cfg.path("sources_file")))
    assert classify_source("https://www.theglobeandmail.com/business/x", "", CASE, sources)[1] == "news"
    assert classify_source("https://www.sec.gov/Archives/x.htm", "", CASE, sources)[1] == "official_filing"
    assert classify_source("https://www.business-humanrights.org/en/x", "", CASE, sources)[1] == "ngo"


def test_results_not_opened_are_shown_to_the_researcher_and_can_be_opened(cfg, engine):
    """One critical headline below the cut-off must not be lost among positive coverage."""
    from a5dd.research_tools import ResearchSession
    from a5dd.schemas import SeenResult

    engine.fetch_per_query = 1
    docs = engine.search(SearchQuery(text="Barrick Fraud", term="Fraud"), CASE, 10)
    unopened = engine.take_unopened()
    assert len(docs) == 1 and "https://www.barrick.com/news/statement" in {h.url for h in unopened}
    seen = [SeenResult(title=h.title, url=h.url, snippet=h.snippet) for h in unopened]
    session = ResearchSession(cfg, engine, CASE, {d.url: d for d in docs}, [], seen=seen)
    assert "Barrick statement" in session.overview()
    assert session.open("https://www.barrick.com/news/statement", "critical headline").publisher == "Barrick Mining Corporation"
    assert session.search_found("regulator spill fined")[0]["doc_id"] == docs[0].doc_id
