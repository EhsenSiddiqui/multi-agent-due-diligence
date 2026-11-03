"""The agent's follow-up research must change the result where it matters."""

from a5dd.io_utils import read_stage
from a5dd.pipeline import run_case
from a5dd.schemas import QuestionAnswers, SearchResults


def _calls(cfg, case_id):
    run_case(cfg, case_id, quiet=True)
    res = cfg.path("results_dir")
    answers = read_stage(res, case_id, "answers", QuestionAnswers)
    return {a.question_id: a.risk_call.value for a in answers.answers}, read_stage(res, case_id, "search", SearchResults)


def test_agent_finds_regulator_notice_only_reachable_by_link(cfg):
    calls, search = _calls(cfg, "case_c_corvane")
    found = {d.doc_id for d in search.documents}
    assert {"C3", "C9"} <= found                      # link-only pages reached by the agent
    followed = [q for q in search.queries if q.action == "follow_link"]
    assert all(q.reason for q in followed)            # every agent action explains itself
    assert calls["Q4"] == "unclear"


def test_without_agent_the_investigation_is_missed(cfg):
    cfg.settings["providers"]["lead_finder"] = "none"
    calls, search = _calls(cfg, "case_c_corvane")
    assert "C3" not in {d.doc_id for d in search.documents}
    assert calls["Q4"] == "no"                        # false comfort: why the agent matters


def test_total_search_failure_stops_the_run(cfg, monkeypatch):
    """If nothing can be searched, the tool must stop, not report 'no risk'."""
    import pytest
    from a5dd.providers.search_simweb import SimulatedWebSearch
    from a5dd.stages.s2_search import SearchFailed

    def broken(self, *a, **k):
        raise ModuleNotFoundError("No module named 'bs4'")
    monkeypatch.setattr(SimulatedWebSearch, "search", broken)
    with pytest.raises(SearchFailed, match="pip install"):
        run_case(cfg, "case_a_brightwell", quiet=True)
    assert not (cfg.path("results_dir") / "case_a_brightwell" / "04_answers.json").exists()


def test_partial_search_failure_is_flagged_in_report(cfg, monkeypatch):
    from a5dd.providers.search_simweb import SimulatedWebSearch
    original = SimulatedWebSearch.search

    def flaky(self, query, case, max_results):
        if query.term == "Bribery":
            raise TimeoutError("site did not respond")
        return original(self, query, case, max_results)
    monkeypatch.setattr(SimulatedWebSearch, "search", flaky)
    run_case(cfg, "case_a_brightwell", quiet=True)
    report = (cfg.path("results_dir") / "case_a_brightwell" / "06_assessment.html").read_text(encoding="utf-8")
    assert "WARNING: 1 of" in report
