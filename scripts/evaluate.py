"""
Evaluate the tool against ground truth: the team's answer keys (data/answer_keys/<case_id>.json).

    python scripts/evaluate.py                         # check whatever results exist
    python scripts/evaluate.py --run                   # run the rule-based baseline first, then check
    python scripts/evaluate.py --run --agents          # run the AI agents first (needs an API key)
    python scripts/evaluate.py --ablate                # compare set-ups: full AI, without each agent, rules
    python scripts/evaluate.py --ablate --repeat 3     # ...each run 3 times, to measure consistency

WHAT IS MEASURED (each against labels written by people BEFORE running the tool)
    Checks                pass/fail on: each question's call, the risk level, the worst case, sources set
                          aside, the label of key sources, the current status of key issues.
    Call accuracy         share of the six questions with the expected call.
    Retrieval recall      share of must-find sources the research actually reached (incl. link-only pages).
    Label accuracy        share of key sources given the right label (allegation, investigation, finding...).
    Consistency           (--repeat) share of questions that got the same call in every run.
                          AI output varies between runs; this says how much.

ABLATION (--ablate): runs the same cases with one agent switched off at a time. If removing an
agent changes nothing, it is not earning its cost; if removing it breaks a check, that is the
evidence it matters. Results go to results/_evaluation/ and results/evaluation.md.

Only the fictional test cases have answer keys; real organisations are not scored here.
Exit code 1 if any check fails, so it can run before a demo or in CI.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from a5dd.config import load_config  # noqa: E402
from a5dd.io_utils import read_stage  # noqa: E402
from a5dd.pipeline import RULES, offline_cases, run_case, use_rules  # noqa: E402
from a5dd.providers.llm_client import check_key  # noqa: E402
from a5dd.schemas import EvidenceTable, QuestionAnswers, ScoreResult, SearchResults  # noqa: E402

AI = {k: "llm" for k in RULES}
CONFIGS = {
    "full": AI,
    "no_researcher": {**AI, "lead_finder": "none"},
    "no_tracer": {**AI, "tracer": "none"},
    "no_challenger": {**AI, "challenger": "none"},
    "rules": RULES,
    "rules_no_researcher": {**RULES, "lead_finder": "none"},
}


def evaluate_case(cfg, case_id: str, key: dict) -> tuple[list[tuple[str, str, str, bool]], dict]:
    """Return (rows of (check, expected, actual, passed), metrics)."""
    res = cfg.path("results_dir")
    search = read_stage(res, case_id, "search", SearchResults)
    evidence = read_stage(res, case_id, "evidence", EvidenceTable)
    answers = read_stage(res, case_id, "answers", QuestionAnswers)
    score = read_stage(res, case_id, "score", ScoreResult)
    rows = []

    calls = {a.question_id: a.risk_call.value for a in answers.answers}
    for qid, expected in key["expected_risk_calls"].items():
        rows.append((f"{qid} risk call", expected, calls.get(qid, "-"), calls.get(qid) == expected))
    rows.append(("risk level", key["expected_risk_level"], score.risk_level.value,
                 score.risk_level.value == key["expected_risk_level"]))
    if "expected_risk_level_if_unclear_were_yes" in key:
        exp, act = key["expected_risk_level_if_unclear_were_yes"], score.risk_level_if_unclear_were_yes.value
        rows.append(("worst-case level", exp, act, exp == act))

    rejected = sorted({r.doc_id for r in evidence.rejected if "namesake" in r.reason})
    expected_rej = sorted(key.get("expected_rejected_doc_ids", []))
    rows.append(("namesakes set aside", ",".join(expected_rej) or "none", ",".join(rejected) or "none",
                 rejected == expected_rej))

    # Strongest label per source (a source can hold several items).
    order = ["finding", "investigation", "allegation", "response", "context"]
    status_by_doc: dict[str, str] = {}
    for e in evidence.items:
        cur = status_by_doc.get(e.doc_id)
        if cur is None or order.index(e.status.value) < order.index(cur):
            status_by_doc[e.doc_id] = e.status.value
    for doc_id, expected in key.get("must_have_status", {}).items():
        rows.append((f"{doc_id} label", expected, status_by_doc.get(doc_id, "-"), status_by_doc.get(doc_id) == expected))

    for doc_id, expected in key.get("expected_issue_status", {}).items():
        issue = next((i for i in evidence.issues if any(e.evidence_id.startswith(f"E-{doc_id}") for e in i.events)), None)
        actual = issue.current_status if issue else "-"
        rows.append((f"issue with {doc_id}", expected, actual, actual == expected))

    found = {d.doc_id for d in search.documents} | {d.doc_id for d in evidence.added_documents}
    must = key.get("must_find_doc_ids", [])
    labelled = key.get("must_have_status", {})
    metrics = {
        "calls": {q: calls.get(q) for q in key["expected_risk_calls"]},
        "call_accuracy": sum(calls.get(q) == v for q, v in key["expected_risk_calls"].items()) / 6,
        "recall": (sum(d in found for d in must) / len(must)) if must else None,
        "missed": [d for d in must if d not in found],
        "label_accuracy": (sum(status_by_doc.get(d) == v for d, v in labelled.items()) / len(labelled)) if labelled else None,
    }
    return rows, metrics


def evaluate_all(cfg, run: bool, quiet: bool = False) -> tuple[int, int, dict]:
    keys_dir = cfg.path("answer_keys_dir")
    total = passed = 0
    per_case = {}
    for case_id in offline_cases(cfg):
        key_file = keys_dir / f"{case_id}.json"
        if not key_file.exists():
            continue
        if run:
            run_case(cfg, case_id, quiet=True)
        rows, metrics = evaluate_case(cfg, case_id, json.loads(key_file.read_text()))
        per_case[case_id] = metrics
        total += len(rows)
        passed += sum(r[3] for r in rows)
        if not quiet:
            print(f"\n{case_id}")
            print(f"  {'check':<22}{'expected':<22}{'actual':<22}")
            for check, exp, act, ok in rows:
                print(f"  {check:<22}{exp:<22}{act:<22}{'PASS' if ok else 'FAIL'}")
            rec = "n/a" if metrics["recall"] is None else f"{metrics['recall']:.0%}"
            print(f"  call accuracy {metrics['call_accuracy']:.0%} · retrieval recall {rec}"
                  + (f" (missed {', '.join(metrics['missed'])})" if metrics["missed"] else ""))
    return passed, total, per_case


def ablate(base_cfg, names: list[str], repeat: int) -> int:
    out_root = base_cfg.path("results_dir") / "_evaluation"
    lines = ["# Evaluation: ablation and consistency", "",
             "| Set-up | Run | Checks passed | Call accuracy | Retrieval recall | Label accuracy |",
             "|---|---|---|---|---|---|"]
    consistency_lines = []
    for name in names:
        overrides = CONFIGS[name]
        rule_based = name.startswith("rules")
        runs = 1 if rule_based else repeat
        calls_by_run = []
        for r in range(1, runs + 1):
            cfg = copy.deepcopy(base_cfg)
            cfg.settings["providers"].update(overrides)
            cfg.settings["paths"]["results_dir"] = str(out_root / f"{name}_run{r}")
            # A fresh cache per repeat, so each run really asks the model again.
            cfg.settings["llm"]["cache_dir"] = str(out_root / f"_cache_{name}_run{r}") if repeat > 1 else \
                base_cfg.settings["llm"]["cache_dir"]
            if not rule_based:
                try:
                    check_key(cfg)
                except Exception as exc:
                    print(f"{name}: skipped ({exc})")
                    break
            print(f"Running {name} (run {r}/{runs})...")
            passed, total, per_case = evaluate_all(cfg, run=True, quiet=True)
            n = len(per_case) or 1
            acc = sum(m["call_accuracy"] for m in per_case.values()) / n
            recs = [m["recall"] for m in per_case.values() if m["recall"] is not None]
            labs = [m["label_accuracy"] for m in per_case.values() if m["label_accuracy"] is not None]
            lines.append(f"| {name} | {r} | {passed}/{total} | {acc:.0%} | "
                         f"{(sum(recs) / len(recs)) if recs else 0:.0%} | {(sum(labs) / len(labs)) if labs else 0:.0%} |")
            calls_by_run.append({c: m["calls"] for c, m in per_case.items()})
        if len(calls_by_run) > 1:
            same = total_q = 0
            for case_id in calls_by_run[0]:
                for q in calls_by_run[0][case_id]:
                    total_q += 1
                    same += len({run[case_id][q] for run in calls_by_run}) == 1
            consistency_lines.append(f"- **{name}:** {same}/{total_q} questions got the same call in all "
                                     f"{len(calls_by_run)} runs ({same / total_q:.0%}).")
    if consistency_lines:
        lines += ["", "## Consistency across repeated runs", "", *consistency_lines]
    lines += ["", "Ground truth: data/answer_keys/ (written by the team before running the tool).",
              "Set-ups: full = all AI agents; no_X = full minus one agent; rules = no AI; "
              "rules_no_researcher = no AI and no follow-up research."]
    report = "\n".join(lines) + "\n"
    (base_cfg.path("results_dir") / "evaluation.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Run the pipeline before evaluating")
    parser.add_argument("--agents", action="store_true", help="With --run: use the AI agents, not the rule baseline")
    parser.add_argument("--ablate", nargs="?", const=",".join(CONFIGS), default=None,
                        help=f"Compare set-ups (comma-separated, default all): {', '.join(CONFIGS)}")
    parser.add_argument("--repeat", type=int, default=1, help="With --ablate: runs per AI set-up (consistency)")
    args = parser.parse_args()

    cfg = load_config()
    if args.ablate is not None:
        names = [n.strip() for n in args.ablate.split(",") if n.strip()]
        unknown = [n for n in names if n not in CONFIGS]
        if unknown:
            print(f"Unknown set-up(s): {', '.join(unknown)}. Options: {', '.join(CONFIGS)}")
            return 1
        return ablate(cfg, names, max(1, args.repeat))

    if not args.agents:
        use_rules(cfg)
    passed, total, _ = evaluate_all(cfg, run=args.run)
    print(f"\nOverall: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
