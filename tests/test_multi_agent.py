"""
The multi-agent workflow (researcher, analyst, judge, challenger) run end to end with a
FAKE model: no API key, no network. A real model decides differently, but the plumbing,
the tool loop and every guard rail are exercised exactly as in a live run.
"""

import json
import re

from a5dd.io_utils import read_stage
from a5dd.pipeline import RULES, run_case
from a5dd.providers import llm_client
from a5dd.schemas import EvidenceTable, QuestionAnswers, SearchResults

OEFA = "https://oefa-notices.example/resoluciones/2025/rd-0457-2025.pdf"
INVENTED = "https://invented.example/secret-report"


def fake_model(system, messages, tools, model, max_tokens):
    """Scripted stand-in for Claude/OpenAI, answering by agent role."""
    user = messages[0]["content"]

    if "lead researcher" in system:                       # AGENT 1: tool loop
        turn = sum(1 for m in messages if m["role"] == "assistant")
        if turn == 0:
            return {"text": "", "tool_calls": [
                {"id": "t1", "name": "web_search", "input": {"query": "Corvane Minerales investigation",
                                                             "reason": "Check the regulator angle"}},
                {"id": "t2", "name": "open_page", "input": {"url": INVENTED, "reason": "made up"}}]}
        if turn == 1:
            return {"text": "", "tool_calls": [
                {"id": "t3", "name": "open_page", "input": {"url": OEFA, "reason": "Primary regulator notice"}}]}
        return {"text": "", "tool_calls": [{"id": "t4", "name": "finish", "input": {
            "summary": "Found a regulator investigation into a subsidiary.",
            "open_questions": ["Is the sale of the subsidiary complete?"]}}]}

    if "You are the tracer" in system:                    # AGENT 5: tool loop on one issue
        turn = sum(1 for m in messages if m["role"] == "assistant")
        if turn == 0:
            return {"text": "", "tool_calls": [
                {"id": "r1", "name": "web_search", "input": {"query": "Corvane Minerales OEFA resolution",
                                                             "reason": "Look for the outcome", "recent": True}},
                {"id": "r2", "name": "search_found_pages", "input": {"query": "investigation drilling water"}}]}
        return {"text": "", "tool_calls": [{"id": "r3", "name": "finish", "input": {
            "summary": "Origin is the OEFA notice; no decision published yet.", "origin_found": True,
            "outcome_found": False}}]}

    if "the analyst" in system:                           # AGENT 2: one source at a time
        doc = json.loads(user)["document"]
        sentence = next((s for s in re.split(r"(?<=[.!?])\s+", doc["text"]) if "Corvane" in s), "")
        if not sentence:
            return {"text": json.dumps({"about_organisation": False, "items": []}), "tool_calls": []}
        status = "investigation" if "investigation" in doc["text"].lower() else "context"
        items = [{"status": status, "quote": sentence, "question_ids": ["Q4" if status == "investigation" else "Q6", "Q99"],
                  "topics": ["water"], "resolved": None, "summary": "Reported issue.",
                  "issue": "Huarani camp: river pollution" if status == "investigation" else "",
                  "event_type": "investigation_opened" if status == "investigation" else "background",
                  "event_date": "2025-08"},
                 {"status": "finding", "quote": "This sentence is not in the source.", "question_ids": ["Q4"],
                  "topics": [], "summary": "Invented."}]
        return {"text": json.dumps({"about_organisation": True, "items": items}), "tool_calls": []}

    if "a challenger objects" in system:                  # AGENT 3, second turn: reply to the challenger
        return {"text": json.dumps({"keep": False, "rebuttal": "Fair point: the investigation is open.",
                                    "response": "An open investigation exists.", "risk_call": "yes",
                                    "why_not_no": "An authority opened an investigation.", "evidence_ids": []}),
                "tool_calls": []}

    if "the challenger" in system:                        # AGENT 4
        data = json.loads(user)
        if data["question"]["id"] == "Q4":
            return {"text": json.dumps({"object": True, "proposed_call": "yes",
                                        "argument": "An authority opened an investigation within 10 years.",
                                        "evidence_ids": ["E-INVENTED"]}), "tool_calls": []}
        return {"text": json.dumps({"object": False}), "tool_calls": []}

    data = json.loads(user)                               # AGENT 3: first answer
    ids = [e["evidence_id"] for e in data["evidence"]]
    return {"text": json.dumps({"response": "See the evidence.", "risk_call": "unclear",
                                "rationale": "Thin evidence.", "case_for_risk": "Some reports.",
                                "case_against_risk": "Nothing established.", "why_not_yes": "Unproven.",
                                "why_not_no": "Reported.", "evidence_ids": ids[:2] + ["E-INVENTED"],
                                "staff_questions": ["Ask the organisation."]}), "tool_calls": []}


def _ai_mode(cfg, monkeypatch):
    monkeypatch.setitem(llm_client.TRANSPORTS, "fake", fake_model)
    cfg.settings["llm"]["provider"] = "fake"
    cfg.settings["providers"].update({k: "llm" for k in RULES})


def test_multi_agent_pipeline_end_to_end(cfg, monkeypatch):
    _ai_mode(cfg, monkeypatch)
    run_case(cfg, "case_c_corvane", quiet=True)
    res = cfg.path("results_dir")
    search = read_stage(res, "case_c_corvane", "search", SearchResults)
    evidence = read_stage(res, "case_c_corvane", "evidence", EvidenceTable)
    answers = {a.question_id: a for a in read_stage(res, "case_c_corvane", "answers", QuestionAnswers).answers}

    # Researcher: reached the link-only regulator PDF by its own choice; could not open an invented URL.
    urls = {d.url for d in search.documents}
    assert OEFA in urls and INVENTED not in urls
    agent_actions = [q for q in search.queries if q.round > 0]
    assert agent_actions and all(q.reason for q in agent_actions)
    assert search.research_summary and search.open_questions

    # Analyst: invented quote rejected by the code gate; unknown question IDs removed.
    assert any("Quote not found" in r.reason for r in evidence.rejected)
    assert all("Q99" not in e.question_ids for e in evidence.items)
    assert any(e.status.value == "investigation" for e in evidence.items)

    # Judge and challenger: the debate is recorded; invented evidence IDs dropped.
    q4 = answers["Q4"]
    assert q4.challenge and q4.rebuttal and q4.call_before_challenge == "unclear" and q4.risk_call.value == "yes"
    assert all("E-INVENTED" not in a.evidence_ids for a in answers.values())
    assert not answers["Q1"].challenge  # Challenger agreed: nothing recorded.

    # Tracer: the open issue was traced; its actions are logged with reasons; the note is kept.
    traced = [i for i in evidence.issues if i.traced]
    assert traced and "OEFA" in traced[0].trace_notes and not traced[0].outcome_found
    assert evidence.trace_log and all(q.reason for q in evidence.trace_log if q.action == "search")
    assert any("last 12 months" in q.term for q in evidence.trace_log)

    html = (res / "case_c_corvane" / "06_assessment.html").read_text(encoding="utf-8")
    assert "Challenger" in html and "Research summary" in html
    assert "Issues over time" in html and "Tracer:" in html


def test_missing_api_key_stops_with_clear_message(cfg, monkeypatch, capsys):
    from a5dd import pipeline
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "load_config", lambda settings_file: cfg)
    cfg.settings["providers"].update({k: "llm" for k in RULES})
    cfg.settings["llm"]["provider"] = "anthropic"
    assert pipeline.main(["--case", "case_a_brightwell"]) == 1
    assert "--rules" in capsys.readouterr().err


def test_ablation_and_consistency_report(cfg, monkeypatch, tmp_path):
    """The evaluation harness runs set-ups side by side and measures run-to-run consistency."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("evaluate", cfg.root / "scripts" / "evaluate.py")
    evaluate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluate)
    monkeypatch.setitem(llm_client.TRANSPORTS, "fake", fake_model)
    cfg.settings["llm"]["provider"] = "fake"
    cfg.settings["paths"]["results_dir"] = str(tmp_path / "res")
    evaluate.ablate(cfg, ["full", "rules"], repeat=2)
    report = (tmp_path / "res" / "evaluation.md").read_text()
    assert "| full | 2 |" in report and "| rules | 1 | 33/33" in report
    assert "got the same call in all 2 runs (100%)" in report   # Scripted model: perfectly consistent.
