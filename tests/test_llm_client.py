"""
The LLM client's translation to the Anthropic and OpenAI request formats, checked against
stub SDK clients (no key, no network), plus the researcher's budget guard rail.
"""

import json
from types import SimpleNamespace as NS

import pytest

from a5dd.providers.llm_client import LLMClient

HISTORY = [
    {"role": "user", "content": "Research Barrick."},
    {"role": "assistant", "text": "Searching.", "tool_calls": [{"id": "c1", "name": "web_search",
                                                              "input": {"query": "Barrick", "reason": "start"}}]},
    {"role": "tool", "results": [{"id": "c1", "content": "[results]"}]},
]
TOOLS = [{"name": "web_search", "description": "Search", "parameters": {"type": "object", "properties": {}}}]


def test_anthropic_format_round_trip(cfg):
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return NS(content=[NS(type="text", text="Next."),
                           NS(type="tool_use", id="c2", name="open_page", input={"url": "https://x"})])

    client = LLMClient(cfg, role="researcher")
    client._client = NS(messages=NS(create=create))
    reply = client._anthropic("sys", HISTORY, TOOLS, "claude-x", 100)
    assert sent["system"] == "sys" and sent["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert sent["messages"][1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "web_search",
                                                 "input": {"query": "Barrick", "reason": "start"}}
    assert sent["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "c1", "content": "[results]"}
    assert reply == {"text": "Next.", "tool_calls": [{"id": "c2", "name": "open_page", "input": {"url": "https://x"}}]}


def test_openai_format_round_trip(cfg):
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        call = NS(id="c2", function=NS(name="finish", arguments=json.dumps({"summary": "done"})))
        return NS(choices=[NS(message=NS(content=None, tool_calls=[call]))])

    client = LLMClient(cfg)
    client._client = NS(chat=NS(completions=NS(create=create)))
    reply = client._openai("sys", HISTORY, TOOLS, "gpt-x", 100)
    msgs = sent["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "web_search"
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "[results]"}
    assert sent["tools"][0]["type"] == "function" and "response_format" not in sent
    assert reply["tool_calls"] == [{"id": "c2", "name": "finish", "input": {"summary": "done"}}]


def test_research_session_enforces_budget_and_known_urls(cfg):
    from a5dd.providers.search_simweb import SimulatedWebSearch
    from a5dd.stages.s1_intake import load_case
    from a5dd.stages.s2_search import ResearchSession

    cfg.settings["agent"]["max_searches"] = 1
    case = load_case(cfg, "case_c_corvane")
    session = ResearchSession(cfg, SimulatedWebSearch(cfg), case, {}, [])
    assert session.search("Corvane water", "test")
    with pytest.raises(RuntimeError, match="budget"):
        session.search("Corvane again", "test")
    with pytest.raises(PermissionError):
        session.open("https://never-shown.example/x", "test")
