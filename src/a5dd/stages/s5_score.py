"""
STAGE 5: Score and route.  Reads 04_answers.json  ->  writes 05_score.json

Plain, deterministic code on purpose (no LLM): the same answers always give the
same score, which is what "apply agreed criteria consistently" requires.

    0 'yes' answers  -> low      (guide 1.4)
    1-2              -> medium
    3 or more        -> high

It also reports the worst case (if every 'unclear' turned out to be 'yes'), so a
reviewer can see how much the result depends on open questions.
"""

from __future__ import annotations

from ..config import Config
from ..io_utils import read_stage, write_stage
from ..schemas import QuestionAnswers, RiskCall, RiskLevel, ScoreResult

STAGE = "05_score"


def level_for(count: int) -> RiskLevel:
    """Guide 1.4 counting rule."""
    if count == 0:
        return RiskLevel.LOW
    if count <= 2:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def run(cfg: Config, case_id: str) -> ScoreResult:
    results_dir = cfg.path("results_dir")
    answers = read_stage(results_dir, case_id, "answers", QuestionAnswers)

    yes = sum(a.risk_call == RiskCall.YES for a in answers.answers)
    unclear = sum(a.risk_call == RiskCall.UNCLEAR for a in answers.answers)
    level = level_for(yes)
    route = cfg.criteria["routing"][level.value]

    result = ScoreResult(
        stage=STAGE,
        case_id=case_id,
        producer="s5_score.rules",
        fictional_data=answers.fictional_data,
        risk_answers_count=yes,
        unclear_count=unclear,
        risk_level=level,
        risk_level_if_unclear_were_yes=level_for(yes + unclear),
        suggested_next_step=route["next_step"],
        suggested_decision_maker=route["decision_maker"],
        escalation_note=cfg.criteria["routing"]["escalation"],
        verification_note=("Not required: answers came from desk-based research, not from the partner (guide 1.2). "
                           "A reviewer should still check the evidence table."),
    )
    write_stage(results_dir, case_id, "score", result)
    return result
