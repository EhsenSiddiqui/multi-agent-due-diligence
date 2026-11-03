"""
AGENT 5: the Challenger (an LLM).  Switch: providers.challenger: llm

Reads the judge's answer and the same evidence, and argues for a DIFFERENT call when it can
make a case the judge did not rule out. It looks for: an allegation treated as a finding (or the
reverse), a source about a different company, evidence outside the 10-year window, a "no" that
rests on an empty search, and evidence the judge ignored.

If it objects, the judge must reply (hold with a rebuttal, or change the call). Both sides
are shown to the reviewer. This is the "attack from two sides" done by two separate agents,
so one model's single reading never goes unchecked.

It may only cite evidence IDs that exist; unknown IDs are dropped in code.
"""

from __future__ import annotations

import json
from typing import Any

from ..schemas import EvidenceItem, IntakeResult, QuestionAnswer
from .base import Challenger
from .judge_llm import evidence_view, issues_view, relevant_evidence
from .llm_client import LLMClient

SYSTEM = """You are the challenger in a due-diligence team at a water charity. A judge answered one question.
Your job is to test that answer, not to agree politely. Check:
- Is an allegation being treated as established, or an established finding being played down?
- Is any source really about a different company, or about a former subsidiary?
- Is the evidence outside the 10-year window, or resolved?
- Does a "no" rest only on finding nothing? (That supports "no in the sources searched", not certainty.)
- Did the judge ignore evidence that points the other way?
- Is an issue judged by an old headline instead of its current status, or treated as resolved when no outcome was
  found? Is one report, repeated by many outlets, being counted as many?
If you can make a specific case for a different call ("yes", "no" or "unclear") that the judge's reasons do not
answer, object. Otherwise agree.
Return JSON: {"object": true|false, "proposed_call": "yes|no|unclear", "argument": "two or three sentences",
 "evidence_ids": [...]}"""


class LLMChallenger(Challenger):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llm = LLMClient(cfg, role="challenger")

    def challenge(self, question: dict[str, Any], answer: QuestionAnswer, evidence: list[EvidenceItem],
                  intake: IntakeResult) -> dict[str, Any] | None:
        relevant = relevant_evidence(question, evidence)
        user = json.dumps({
            "question": question,
            "assessment_date": str(self.cfg.assessment_date),
            "organisation": intake.case.organisation_name,
            "aliases": intake.case.aliases,
            "judge_answer": answer.model_dump(mode="json", include={"response", "risk_call", "rationale",
                                                                    "case_for_risk", "case_against_risk",
                                                                    "why_not_yes", "why_not_no", "evidence_ids"}),
            "evidence": evidence_view(relevant),
            "issues": issues_view(self.issues, question["id"]),
        }, indent=1)
        data = self.llm.complete_json(SYSTEM, user)
        proposed = str(data.get("proposed_call", "")).lower()
        if not data.get("object") or proposed not in ("yes", "no", "unclear") or proposed == answer.risk_call.value:
            return None
        known = {e.evidence_id for e in relevant}
        return {"proposed_call": proposed, "argument": str(data.get("argument", "")),
                "evidence_ids": [i for i in data.get("evidence_ids", []) if i in known]}
