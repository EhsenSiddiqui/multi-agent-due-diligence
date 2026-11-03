"""
AGENT 4: the Judge (an LLM).  Switch: providers.judge: llm

Answers one template b question at a time from the evidence table, attacking from two sides,
then (if the challenger objects) reads the objection and either holds its call with a
rebuttal or changes it. Same output as the rule-based judge.

GUARD RAILS (in code): only evidence IDs that exist can be cited; unknown IDs are dropped.
Scoring (stage 5) stays deterministic code whichever judge is used.
"""

from __future__ import annotations

import json
from typing import Any

from ..schemas import EntityMatch, EvidenceItem, IntakeResult, QuestionAnswer, RiskCall
from .base import QuestionJudge
from .llm_client import LLMClient

SYSTEM = """You help staff at a water charity complete an ethical due-diligence form.
Answer ONE question using ONLY the evidence items given. Attack from two sides:
1. Build the case FOR a risk and the case AGAINST it from the evidence.
2. Try to eliminate each answer with ONE specific disqualifier (e.g. "unproven allegation from a
   single low-reliability source", "named company not confirmed as part of the organisation",
   "outside the 10-year window", "no source mentions it").
3. Keep the answer that survives: "yes" if "no" is eliminated; "no" if nothing supports "yes";
   "unclear" if neither can be eliminated. Then write the staff question that would break the tie.
Only infer what each source safely supports (see each item's safe_to_infer / not_safe_to_infer):
allegations are not facts, investigations are not findings, company statements are claims.
If there is no evidence, say "in the sources searched", never "no risk".
Use the issue timelines: judge each issue by its CURRENT status and its trajectory. Many articles repeating one
report are one source. An open investigation or an unresolved serious allegation weighs more than an old issue
that was settled and remediated; an issue with "no outcome found" is not resolved, it is unknown.
Keep "response" to one or two short sentences. Cite evidence IDs in square brackets.
Return JSON: {"response": "...", "risk_call": "yes|no|unclear", "rationale": "...",
 "case_for_risk": "...", "case_against_risk": "...", "why_not_yes": "...", "why_not_no": "...",
 "evidence_ids": [...], "staff_questions": [...]}"""


REBUT = """You are the judge in a due-diligence team. You proposed an answer; a challenger objects.
Read the objection and the evidence again. Change your call ONLY if the objection shows your reason for
ruling out the other answer was wrong. If neither side can now be ruled out, the call is "unclear".
Return JSON: {"keep": true|false, "rebuttal": "one or two sentences", "response": "...",
 "risk_call": "yes|no|unclear", "rationale": "...", "case_for_risk": "...", "case_against_risk": "...",
 "why_not_yes": "...", "why_not_no": "...", "evidence_ids": [...], "staff_questions": [...]}
If keep is true, the other fields may repeat your original answer."""

SEVERITY = {"finding": 0, "investigation": 1, "allegation": 2, "response": 3, "context": 4}
RELIABILITY = {"high": 0, "medium": 1, "low": 2}


def relevant_evidence(question: dict[str, Any], evidence: list[EvidenceItem], limit: int = 40) -> list[EvidenceItem]:
    """Evidence for one question, strongest first, capped so the prompt stays readable."""
    items = [e for e in evidence if question["id"] in e.question_ids and e.entity_match != EntityMatch.NONE]
    items.sort(key=lambda e: (SEVERITY[e.status.value], RELIABILITY[e.reliability.value]))
    return items[:limit]


def issues_view(issues, question_id: str) -> list[dict]:
    """Issue timelines relevant to one question, compact: what it is, where it stands, how it got there."""
    from ..issues import status_label
    return [{"issue_id": i.issue_id, "label": i.label, "current_status": status_label(i.current_status),
             "outcome_found": i.outcome_found, "first_reported": i.first_reported,
             "independent_sources": i.independent_sources, "traced": i.traced, "trace_notes": i.trace_notes,
             "timeline": [f"{e.date or 'undated'}: {status_label(e.event_type)} ({e.publisher}) [{e.evidence_id}]"
                          for e in i.events]}
            for i in issues if question_id in i.question_ids]


def evidence_view(items: list[EvidenceItem]) -> list[dict]:
    return [e.model_dump(mode="json", exclude={"extracted_by", "source_title", "standards"}) for e in items]


class LLMJudge(QuestionJudge):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llm = LLMClient(cfg, role="judge")

    def judge(self, question: dict[str, Any], evidence: list[EvidenceItem], intake: IntakeResult) -> QuestionAnswer:
        relevant = relevant_evidence(question, evidence)
        user = json.dumps({
            "question": question,
            "assessment_date": str(self.cfg.assessment_date),
            "lookback_years": self.cfg.criteria["lookback_years"],
            "case_card": intake.case.model_dump(mode="json"),
            "low_middle_income_countries": self.cfg.criteria["low_middle_income_countries"],
            "evidence": evidence_view(relevant),
            "issues": issues_view(self.issues, question["id"]),
        }, indent=1)
        data = self.llm.complete_json(SYSTEM, user)
        return self._answer(question, data, {e.evidence_id for e in relevant})

    def reconsider(self, question, answer, challenge, evidence, intake) -> QuestionAnswer:
        relevant = relevant_evidence(question, evidence)
        user = json.dumps({
            "question": question,
            "your_answer": answer.model_dump(mode="json", include={"response", "risk_call", "rationale", "case_for_risk",
                                                                   "case_against_risk", "why_not_yes", "why_not_no",
                                                                   "evidence_ids"}),
            "challenge": challenge,
            "evidence": evidence_view(relevant),
            "issues": issues_view(self.issues, question["id"]),
        }, indent=1)
        data = self.llm.complete_json(REBUT, user)
        if data.get("keep", True):
            revised = answer.model_copy()
        else:
            revised = self._answer(question, data, {e.evidence_id for e in relevant})
            revised.call_before_challenge = answer.risk_call.value
        revised.challenge = str(challenge.get("argument", ""))
        revised.challenger_call = str(challenge.get("proposed_call", ""))
        revised.rebuttal = str(data.get("rebuttal", ""))
        return revised

    def _answer(self, question: dict[str, Any], data: dict, known: set[str]) -> QuestionAnswer:
        call = str(data.get("risk_call", "unclear")).lower()
        return QuestionAnswer(
            question_id=question["id"],
            question_text=question["text"],
            response=data.get("response", ""),
            risk_call=RiskCall(call if call in ("yes", "no", "unclear") else "unclear"),
            rationale=data.get("rationale", ""),
            evidence_ids=[i for i in data.get("evidence_ids", []) if i in known],  # Drop invented IDs.
            staff_questions=list(data.get("staff_questions", [])),
            judged_by=f"{self.name}:{self.llm.model}",
            case_for_risk=data.get("case_for_risk", ""),
            case_against_risk=data.get("case_against_risk", ""),
            why_not_yes=data.get("why_not_yes", ""),
            why_not_no=data.get("why_not_no", ""),
        )
