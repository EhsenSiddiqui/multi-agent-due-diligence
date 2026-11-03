"""
STAGE 1: Intake.  Reads data/cases/<case_id>/case.json  ->  writes 01_intake.json

Decides whether the relationship must enter the ethical-check process (guide 1.1)
and flags excluded or high-risk sectors that staff already declared on the case card.

SIMPLIFIED: guide 1.1 has many special cases (legacies, governments, consortium partners).
Only the common partner types are encoded in criteria.json "entry_criteria".
BETTER: a small web form for staff, and every guide 1.1 row encoded and unit-tested.
"""

from __future__ import annotations

import json

from ..config import Config
from ..io_utils import write_stage
from ..schemas import CaseCard, IntakeResult
from ..text_utils import contains_any

STAGE = "01_intake"


def load_case(cfg: Config, case_id: str) -> CaseCard:
    """Read and validate the case card. Fails clearly if a field is missing or mistyped."""
    path = cfg.path("cases_dir") / case_id / "case.json"
    return CaseCard.model_validate(json.loads(path.read_text(encoding="utf-8")))


def entry_decision(cfg: Config, case: CaseCard) -> tuple[bool, str]:
    """Apply the guide 1.1 thresholds for this relationship type."""
    rules = cfg.criteria["entry_criteria"]
    rule = rules.get(case.relationship_type)
    if rule is None:
        return True, f"Relationship type '{case.relationship_type}' is not in the demo rules; checking by default."
    threshold = rule["threshold_usd"]
    if case.annual_value_usd > threshold:
        return True, f"Annual value US${case.annual_value_usd:,.0f} is above the US${threshold:,.0f} threshold (guide 1.1)."
    if rule["also_if_national_or_brand"] and (case.presence in ("national", "multinational") or case.brand_affiliation):
        return True, "Below the value threshold, but national/multinational presence or brand affiliation (guide 1.1)."
    return False, f"Below the US${threshold:,.0f} threshold with no other trigger (guide 1.1)."


def run(cfg: Config, case_id: str) -> IntakeResult:
    case = load_case(cfg, case_id)
    required, reason = entry_decision(cfg, case)

    # Flag sectors staff already declared (stage 4 separately looks for sector links in the evidence).
    declared = " ".join(case.declared_sectors)
    excluded, high_risk = [], []
    for key, sector in cfg.active_sectors().items():
        if contains_any(declared, sector["keywords"]):
            (excluded if sector["class"] == "excluded" else high_risk).append(key)

    result = IntakeResult(
        stage=STAGE,
        case_id=case_id,
        producer="s1_intake.rules",
        fictional_data=case.fictional,
        case=case,
        check_required=required,
        check_reason=reason,
        excluded_industry_flags=excluded,
        high_risk_area_flags=high_risk,
    )
    write_stage(cfg.path("results_dir"), case_id, "intake", result)
    return result
