"""
Proves the decoupling promise: a NEW implementation can replace one step without
changing any other file, and the quality gates still apply to it.
"""

from datetime import date

import pytest

from a5dd import providers
from a5dd.io_utils import ContractError, read_stage
from a5dd.pipeline import run_case
from a5dd.providers.base import EvidenceExtractor, SearchProvider
from a5dd.schemas import (EntityMatch, EvidenceItem, EvidenceStatus, EvidenceTable, Reliability, ScoreResult,
                          SourceDocument)


# --- A brand-new search provider, defined only in this test --------------------------------
class OneDocSearch(SearchProvider):
    """Pretends to be a web API that returns a single finding."""

    def search(self, query, case, max_results):
        return [SourceDocument(
            doc_id="X1", title="Regulator fines Brightwell Office Supplies Ltd.", url="https://reg.example/x1",
            publisher="Test Regulator", source_type="regulator", published_date=date(2024, 1, 1),
            text="Brightwell Office Supplies Ltd. was fined $10,000 for dumping waste into a river.",
            retrieved_by=self.name)]


def test_new_search_provider_needs_no_other_changes(cfg, monkeypatch):
    # Register the new provider (one registry line) and select it in settings. Nothing else changes.
    monkeypatch.setitem(providers.SEARCH, "onedoc", OneDocSearch)
    cfg.settings["providers"]["search"] = "onedoc"

    run_case(cfg, "case_a_brightwell", quiet=True)

    results = cfg.path("results_dir")
    evidence = read_stage(results, "case_a_brightwell", "evidence", EvidenceTable)
    score = read_stage(results, "case_a_brightwell", "score", ScoreResult)
    assert evidence.items[0].status == EvidenceStatus.FINDING
    assert score.risk_answers_count >= 1          # Downstream stages used the new data unchanged.


# --- An extractor that invents a quote must be caught by stage 3 ---------------------------
class LyingExtractor(EvidenceExtractor):
    def extract(self, doc, case):
        return [EvidenceItem(
            evidence_id=f"E-{doc.doc_id}", doc_id=doc.doc_id, source_title=doc.title, source_url=doc.url,
            publisher=doc.publisher, source_type=doc.source_type, published_date=doc.published_date,
            reliability=Reliability.HIGH, entity_match=EntityMatch.EXACT, status=EvidenceStatus.FINDING,
            question_ids=["Q4"], standards=["S2"], summary="made up", quote="This sentence is not in the source.",
            extracted_by="LyingExtractor")]


def test_invented_quotes_are_rejected(cfg, monkeypatch):
    monkeypatch.setitem(providers.EXTRACTOR, "liar", LyingExtractor)
    cfg.settings["providers"]["extractor"] = "liar"
    run_case(cfg, "case_b_northstream", start=1, end=3, quiet=True)
    evidence = read_stage(cfg.path("results_dir"), "case_b_northstream", "evidence", EvidenceTable)
    assert evidence.items == []
    assert all("Quote not found" in r.reason for r in evidence.rejected)


# --- Malformed files fail loudly -------------------------------------------------------------
def test_wrong_schema_version_fails_loudly(cfg):
    run_case(cfg, "case_a_brightwell", end=1, quiet=True)
    path = cfg.path("results_dir") / "case_a_brightwell" / "01_intake.json"
    path.write_text(path.read_text().replace('"schema_version": "1.0"', '"schema_version": "0.9"'))
    from a5dd.schemas import IntakeResult
    with pytest.raises(ContractError):
        read_stage(cfg.path("results_dir"), "case_a_brightwell", "intake", IntakeResult)
