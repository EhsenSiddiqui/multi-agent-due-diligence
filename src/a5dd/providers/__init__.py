"""
Provider factory: maps the names in settings.yaml to implementation classes.

    providers:
      search: simweb        -> SimulatedWebSearch
      lead_finder: heuristic -> HeuristicLeadFinder
      extractor: llm        -> LLMExtractor

To add a new implementation, write the class and add ONE line to a registry below.
Imports are lazy (inside the functions), so the offline demo never needs the
optional packages (httpx, anthropic, openai) that the online versions use.
"""

from __future__ import annotations

from importlib import import_module

from ..config import Config
from .base import Challenger, EvidenceExtractor, LeadFinder, QuestionJudge, SearchProvider

# name in settings.yaml -> "module:ClassName"
SEARCH = {
    "simweb": "search_simweb:SimulatedWebSearch",      # offline test web shipped with the fictional cases
    "tavily": "search_live:TavilySearch",              # live web, needs TAVILY_API_KEY
    "duckduckgo": "search_live:DuckDuckGoSearch",      # live web, no key
    "live": "search_live:auto_live_search",            # tavily if a key is set, else duckduckgo
}
LEAD_FINDER = {
    "heuristic": "leads_heuristic:HeuristicLeadFinder",  # rule-based baseline
    "llm": "leads_llm:LLMResearcher",                    # AI researcher with its own tool-use loop
    "none": "leads_llm:NoLeadFinder",
}
EXTRACTOR = {
    "heuristic": "extractor_heuristic:HeuristicExtractor",
    "llm": "extractor_llm:LLMExtractor",
}
JUDGE = {
    "heuristic": "judge_heuristic:HeuristicJudge",
    "llm": "judge_llm:LLMJudge",
}
CHALLENGER = {
    "llm": "challenger_llm:LLMChallenger",
    "none": None,
}
TRACER = {
    "llm": "tracer_llm:LLMTracer",
    "none": None,
}


def _build(registry: dict, choice: str, cfg: Config):
    """Instantiate the implementation registered under `choice`.
    A registry value is either "module:ClassName" (lazy import) or a class object."""
    if choice not in registry:
        raise ValueError(f"Unknown provider {choice!r}. Options: {sorted(registry)}")
    target = registry[choice]
    if target is None:
        return None
    if isinstance(target, type):
        return target(cfg)
    module_name, class_name = target.split(":")
    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, class_name)(cfg)


def search_choice(cfg: Config, case_id: str | None = None) -> str:
    """providers.search: auto -> the offline test web if the case ships one, otherwise the live web."""
    choice = cfg.provider("search")
    if choice == "auto":
        has_test_web = case_id and (cfg.path("cases_dir") / case_id / "web" / "index.json").exists()
        choice = "simweb" if has_test_web else cfg.settings["providers"].get("live_search", "live")
        if choice == "auto":
            choice = "live"
    return choice


def get_search(cfg: Config, case_id: str | None = None) -> SearchProvider:
    return _build(SEARCH, search_choice(cfg, case_id), cfg)


def get_lead_finder(cfg: Config) -> LeadFinder:
    return _build(LEAD_FINDER, cfg.settings["providers"].get("lead_finder", "none"), cfg)


def get_extractor(cfg: Config) -> EvidenceExtractor:
    return _build(EXTRACTOR, cfg.provider("extractor"), cfg)


def get_judge(cfg: Config) -> QuestionJudge:
    return _build(JUDGE, cfg.provider("judge"), cfg)


def get_tracer(cfg: Config):
    return _build(TRACER, cfg.settings["providers"].get("tracer", "none"), cfg)


def get_challenger(cfg: Config) -> Challenger | None:
    return _build(CHALLENGER, cfg.settings["providers"].get("challenger", "none"), cfg)
