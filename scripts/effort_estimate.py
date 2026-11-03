"""
Modelled effort and running-cost estimate per case (no human timing needed).

    python scripts/effort_estimate.py          # every case that has results (incl. real organisations)
    python scripts/effort_estimate.py --run    # runs the fictional test cases first (rule-based baseline)

Writes results/effort_estimate.md and results/effort_estimate.csv, and prints the table.

THE MODEL (assumptions are in config/settings.yaml -> effort)
  Manual baseline (from the manual, not invented):
      guide 1.3 says an initial desk check takes 1-2 hours, scanning
      (searches x 4 result pages) = 33 x 4 = 132 result pages, then reading and summarising sources.
  With the tool, a reviewer:
      reads the report's core text (answers, staff questions, next step)  -> words / reading speed
      spot-checks every source cited by a "yes" or "unclear" answer     -> sources x minutes per check
      + a fixed overhead per case (open the report, record the decision)
  Running cost (when switched to live search and an LLM):
      search calls = standard searches + agent follow-up searches; page downloads = sources opened;
      LLM calls ~ one per source (extraction) + 6 (judge) + one per agent round (planner).

WHY ACTIONS, NOT WORD COUNTS OF SOURCES
  The demo sources are short, so comparing their length with the report's length would be
  misleading. The manual effort is dominated by searching and scanning result pages, which
  the tool removes; the review effort scales with how many answers need checking.

This is a MODEL, not a measurement. A pilot timing staff on real past cases would confirm it.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a5dd.config import load_config  # noqa: E402
from a5dd.io_utils import read_stage  # noqa: E402
from a5dd.pipeline import list_cases, offline_cases, run_case, use_rules  # noqa: E402
from a5dd.schemas import EvidenceTable, QuestionAnswers, RiskCall, ScoreResult, SearchResults  # noqa: E402


def words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def estimate(cfg, case_id: str) -> dict:
    res = cfg.path("results_dir")
    search = read_stage(res, case_id, "search", SearchResults)
    evidence = read_stage(res, case_id, "evidence", EvidenceTable)
    answers = read_stage(res, case_id, "answers", QuestionAnswers)
    score = read_stage(res, case_id, "score", ScoreResult)
    e = cfg.settings["effort"]

    standard = sum(1 for q in search.queries if q.round == 0)
    agent_searches = sum(1 for q in search.queries if q.round > 0 and q.action == "search")
    agent_links = sum(1 for q in search.queries if q.round > 0 and q.action == "follow_link")

    # What a reviewer reads in the report before deciding (the always-visible core).
    core_text = " ".join([a.response for a in answers.answers]
                         + [q for a in answers.answers for q in a.staff_questions]
                         + [score.suggested_next_step, score.suggested_decision_maker])
    flagged = [a for a in answers.answers if a.risk_call != RiskCall.NO]
    to_check = sorted({i for a in flagged for i in a.evidence_ids})

    read_min = words(core_text) / e["reading_words_per_minute"]
    review_min = read_min + len(to_check) * e["minutes_per_source_check"] + e["minutes_overhead_per_case"]
    lo, hi = e["manual_baseline_minutes"]

    # AI mode: one analyst call per source, researcher and tracer turns, judge + challenger (+ rebuttal) per question.
    llm_calls = (len(search.documents) + len(evidence.added_documents) + agent_searches + agent_links
                 + len(evidence.trace_log) + 3 * len(answers.answers))
    search_calls = standard + agent_searches

    def price(n, key):
        p = e.get(key)
        return f"${n * p:.2f}" if p is not None else "price not set"

    return {
        "case": case_id,
        "manual_result_pages": standard * e["result_pages_per_search"],
        "manual_minutes": f"{lo}-{hi}",
        "agent_follow_ups": agent_searches + agent_links,
        "sources_used": len(evidence.items),
        "answers_to_review": len(flagged),
        "sources_to_spot_check": len(to_check),
        "report_core_words": words(core_text),
        "review_minutes": round(review_min),
        "time_saved_minutes": f"{max(0, round(lo - review_min))}-{max(0, round(hi - review_min))}",
        "search_calls": search_calls,
        "llm_calls_if_on": llm_calls,
        "search_cost": price(search_calls, "price_per_search_usd"),
        "llm_cost": price(llm_calls, "price_per_llm_call_usd"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Run the pipeline first")
    args = parser.parse_args()
    cfg = load_config()
    rows = []
    if args.run:
        use_rules(cfg)
        for case_id in offline_cases(cfg):
            run_case(cfg, case_id, quiet=True)
    for case_id in list_cases(cfg):
        if (cfg.path("results_dir") / case_id / "05_score.json").exists():
            rows.append(estimate(cfg, case_id))

    out = cfg.path("results_dir")
    with open(out / "effort_estimate.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    e = cfg.settings["effort"]
    lines = [
        "# Modelled review effort and running cost",
        "",
        "| Case | Manual: result pages to scan | Manual time (guide 1.3) | Answers to review | Sources to spot-check "
        "| Modelled review time | Time saved | Search calls | LLM calls (if on) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['case']} | {r['manual_result_pages']} | {r['manual_minutes']} min | {r['answers_to_review']} "
                     f"| {r['sources_to_spot_check']} | ~{r['review_minutes']} min | {r['time_saved_minutes']} min "
                     f"| {r['search_calls']} ({r['search_cost']}) | {r['llm_calls_if_on']} ({r['llm_cost']}) |")
    lines += ["", "**Assumptions** (config/settings.yaml, `effort`):",
              f"- Manual baseline {e['manual_baseline_minutes'][0]}-{e['manual_baseline_minutes'][1]} minutes and "
              f"{e['result_pages_per_search']} result pages per search: from guide 1.3.",
              f"- Reading speed {e['reading_words_per_minute']} words/minute; {e['minutes_per_source_check']} minutes to "
              f"spot-check one source; {e['minutes_overhead_per_case']} minutes overhead per case: team estimates.",
              "- A modelled estimate, not a measurement. Confirm with a pilot timing staff on real past cases."]
    (out / "effort_estimate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
