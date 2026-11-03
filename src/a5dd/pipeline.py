"""
Command-line entry point: runs the stages for one case or all cases.

Examples (from the project root):
    python -m a5dd.pipeline --list                       # show available cases
    python -m a5dd.pipeline --case case_c_corvane        # run all six stages for one case
    python -m a5dd.pipeline --all                        # run every case in data/cases
    python -m a5dd.pipeline --case case_c_corvane --from 4   # re-run stages 4-6 only
    python -m a5dd.pipeline --case case_c_corvane --only 3   # re-run stage 3 only
    python -m a5dd.pipeline --case case_c_corvane --no-agent # fixed searches only (demo: see what is missed)
    python -m a5dd.pipeline --case case_d_barrick           # a real organisation: live web + AI agents
    python -m a5dd.pipeline --all --rules                    # rule-based baseline: no AI, no API key

MODES
    default  multi-agent AI workflow (researcher, analyst, judge, challenger). Needs an API key.
    --rules  rule-based baseline: same stages and outputs, keyword rules instead of AI agents.

Re-running from a later stage is possible because each stage reads its inputs
from the files the earlier stages saved.
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import Config, load_config
from .providers import search_choice
from .providers.llm_client import check_key
from .stages import ORDER

RULES = {"lead_finder": "heuristic", "extractor": "heuristic", "judge": "heuristic", "challenger": "none",
         "tracer": "none"}


def use_rules(cfg: Config) -> None:
    """Switch every AI agent to its rule-based baseline (no API key needed)."""
    cfg.settings["providers"].update(RULES)


def uses_llm(cfg: Config) -> bool:
    p = cfg.settings["providers"]
    return "llm" in (p.get("lead_finder"), p.get("extractor"), p.get("judge"), p.get("challenger"), p.get("tracer"))


def list_cases(cfg: Config) -> list[str]:
    """Every folder in data/cases that contains a case.json."""
    return sorted(p.parent.name for p in cfg.path("cases_dir").glob("*/case.json"))


def offline_cases(cfg: Config) -> list[str]:
    """The fictional cases that ship with an offline test web (used by tests and evaluate.py)."""
    return [c for c in list_cases(cfg) if (cfg.path("cases_dir") / c / "web" / "index.json").exists()]


def run_case(cfg: Config, case_id: str, start: int = 1, end: int = 6, quiet: bool = False) -> None:
    for number, name, module in ORDER:
        if start <= number <= end:
            t0 = time.perf_counter()
            module.run(cfg, case_id)
            if not quiet:
                print(f"  [{number}/6] {name:<8} done in {time.perf_counter() - t0:.2f}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A5 due-diligence pipeline (Team Glacier)")
    parser.add_argument("--case", help="Case folder name in data/cases")
    parser.add_argument("--all", action="store_true", help="Run every case")
    parser.add_argument("--list", action="store_true", help="List available cases")
    parser.add_argument("--from", dest="start", type=int, default=1, help="First stage to run (1-6)")
    parser.add_argument("--only", type=int, help="Run a single stage (1-6)")
    parser.add_argument("--settings", default="config/settings.yaml", help="Settings file to use")
    parser.add_argument("--no-agent", action="store_true",
                        help="Switch off the follow-up research for this run (lead_finder: none)")
    parser.add_argument("--rules", action="store_true",
                        help="Rule-based baseline instead of the AI agents (no API key needed)")
    args = parser.parse_args(argv)

    cfg = load_config(settings_file=args.settings)
    if args.rules:
        use_rules(cfg)
    if args.no_agent:
        cfg.settings["providers"]["lead_finder"] = "none"
    cases = list_cases(cfg)

    if args.list or not (args.case or args.all):
        print("Available cases:\n  " + "\n  ".join(cases))
        return 0

    targets = cases if args.all else [args.case]
    first_stage = args.only or args.start
    if uses_llm(cfg) and first_stage <= 4:  # Stages 5-6 never call an AI.
        try:
            check_key(cfg)
        except Exception as exc:
            print(f"STOPPED: {exc}", file=sys.stderr)
            return 1
    failed = []
    for case_id in targets:
        if case_id not in cases:
            print(f"Unknown case {case_id!r}. Use --list.", file=sys.stderr)
            return 1
        start, end = (args.only, args.only) if args.only else (args.start, 6)
        p = cfg.settings["providers"]
        mode = "AI agents" if uses_llm(cfg) else "rule-based baseline"
        print(f"{case_id}  [{mode}]  pages={search_choice(cfg, case_id)}, researcher={p.get('lead_finder')}, "
              f"analyst={p.get('extractor')}, tracer={p.get('tracer', 'none')}, judge={p.get('judge')}, "
              f"challenger={p.get('challenger', 'none')}")
        try:
            run_case(cfg, case_id, start, end)
        except Exception as exc:  # Show a readable message instead of a long traceback; go on to the next case.
            print(f"\n  STOPPED: {exc}", file=sys.stderr)
            failed.append(case_id)
            continue
        print(f"  -> results in {cfg.path('results_dir') / case_id}")
    if failed:
        print(f"\nNot completed: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
