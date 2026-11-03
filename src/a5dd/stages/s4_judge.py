"""
STAGE 4: Judge each question.  Reads 01_intake + 03_evidence  ->  writes 04_answers.json

Asks the configured judge to answer the six template b questions, one at a time.
In AI mode a second agent, the CHALLENGER, then tests each answer. If it objects, the
judge must reply: hold the call with a rebuttal, or change it. Both sides are saved and
shown to the reviewer. Also adds "policy questions": places where the manual itself is
unclear, so staff can see which rules the result depends on.
"""

from __future__ import annotations

from ..config import Config
from ..io_utils import read_stage, write_stage
from concurrent.futures import ThreadPoolExecutor

from ..providers import get_challenger, get_judge
from ..schemas import EvidenceTable, IntakeResult, QuestionAnswers

STAGE = "04_answers"


def policy_questions(cfg: Config) -> list[str]:
    """Known contradictions in the manual, with the setting this run used."""
    fossil = "excluded" if cfg.switch("fossil_fuels_excluded") else "not excluded"
    alcohol = "a risk area" if cfg.switch("alcohol_is_risk_area") else "not a risk area"
    return [
        f"Fossil fuels were treated as {fossil}. Policy 0.0 s3.3 excludes them, but guides 1.1 and 4.1 do not list them. "
        "Which applies?",
        f"Alcohol was treated as {alcohol}. Template a lists it; template b does not. Which applies?",
    ]


def run(cfg: Config, case_id: str) -> QuestionAnswers:
    results_dir = cfg.path("results_dir")
    intake = read_stage(results_dir, case_id, "intake", IntakeResult)
    evidence = read_stage(results_dir, case_id, "evidence", EvidenceTable)
    judge = get_judge(cfg)
    challenger = get_challenger(cfg)
    judge.issues = evidence.issues
    if challenger is not None:
        challenger.issues = evidence.issues

    def answer(q):
        a = judge.judge(q, evidence.items, intake)
        if challenger is not None:
            objection = challenger.challenge(q, a, evidence.items, intake)
            if objection:
                a = judge.reconsider(q, a, objection, evidence.items, intake)
        return a

    workers = int(cfg.settings.get("llm", {}).get("parallel_calls", 6)) if cfg.provider("judge") == "llm" else 1
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        answers = list(pool.map(answer, cfg.questions))

    result = QuestionAnswers(
        stage=STAGE,
        case_id=case_id,
        producer=judge.name + (f" + {challenger.name}" if challenger else ""),
        fictional_data=intake.fictional_data,
        answers=answers,
        policy_questions=policy_questions(cfg),
    )
    write_stage(results_dir, case_id, "answers", result)
    return result
