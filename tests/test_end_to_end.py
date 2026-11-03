"""Run every fictional case and compare with the team's answer keys."""

import json

import pytest

from a5dd.config import load_config
from a5dd.io_utils import read_stage
from a5dd.pipeline import run_case, offline_cases
from a5dd.schemas import AssessmentManifest, QuestionAnswers, ScoreResult

CASES = offline_cases(load_config())


@pytest.mark.parametrize("case_id", CASES)
def test_case_matches_answer_key(cfg, case_id):
    key_path = cfg.path("answer_keys_dir") / f"{case_id}.json"
    if not key_path.exists():
        pytest.skip("no answer key")
    key = json.loads(key_path.read_text())

    run_case(cfg, case_id, quiet=True)
    res = cfg.path("results_dir")
    answers = read_stage(res, case_id, "answers", QuestionAnswers)
    score = read_stage(res, case_id, "score", ScoreResult)

    assert {a.question_id: a.risk_call.value for a in answers.answers} == key["expected_risk_calls"]
    assert score.risk_level.value == key["expected_risk_level"]


@pytest.mark.parametrize("case_id", CASES)
def test_every_no_is_hedged_and_outputs_exist(cfg, case_id):
    """Absence of evidence is never proof of no risk; every call says why the other answers were ruled out."""
    run_case(cfg, case_id, quiet=True)
    res = cfg.path("results_dir")
    answers = read_stage(res, case_id, "answers", QuestionAnswers)
    for a in answers.answers:
        if a.risk_call.value == "no":
            assert "in the sources searched" in a.response   # hedged, never "no risk"
            assert a.why_not_yes                              # two-sided: says why "yes" was ruled out
        if a.risk_call.value == "yes":
            assert a.why_not_no
        if a.risk_call.value == "unclear":
            assert a.why_not_yes and a.why_not_no             # neither side could be eliminated
    manifest = read_stage(res, case_id, "manifest", AssessmentManifest)
    for rel in manifest.files.values():
        assert (res / case_id / rel).exists()
    assert manifest.gaps, "Every assessment must list its gaps"
