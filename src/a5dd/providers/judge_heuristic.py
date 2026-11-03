"""
Offline question judge (DEMO implementation): guide 1.4 rules written as code,
using an "attack from two sides" method for every question.

THE METHOD
  For each of the six template b questions the judge:
    1. builds the case FOR a risk ("yes") and the case AGAINST ("no") from the evidence;
    2. tries to ELIMINATE each answer with one specific disqualifier, e.g.
         "only an unproven allegation from a single low-reliability source"
         "the company named is not confirmed as part of the organisation"
         "outside the 10-year window"
         "no source links the organisation to these sectors"
    3. keeps the answer that survives:
         yes      -> "no" was eliminated and "yes" was not
         no       -> nothing supports "yes" (stated as "in the sources searched")
         unclear  -> BOTH survive elimination: a person must decide, and the staff
                     question is whatever would break the tie.
  The report shows both sides and every elimination reason.

KEY RULES (from the brief)
  - An allegation alone never makes "yes" unless credible or corroborated.
  - A similar name is never treated as the organisation.
  - Absence of evidence is never proof of no risk.

SIMPLIFIED: fixed rules per question; cannot weigh how serious an issue is.
BETTER: judge_llm.py argues both sides with an LLM, same output fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..schemas import (
    EntityMatch,
    EvidenceItem,
    EvidenceStatus,
    IntakeResult,
    QuestionAnswer,
    Reliability,
    RiskCall,
)
from .base import QuestionJudge

CONFIRMED = (EntityMatch.EXACT, EntityMatch.ALIAS)
NEGATIVE_TOPICS = {"water", "environment", "wages", "health_safety", "corruption"}
SEARCHED = "in the sources searched"


@dataclass
class Verdict:
    """Working result for one question, before it becomes a QuestionAnswer."""
    call: RiskCall
    response: str                      # One or two short sentences for template b.
    for_risk: str = ""
    against_risk: str = ""
    why_not_yes: str = ""
    why_not_no: str = ""
    ids: list[str] = field(default_factory=list)
    staff: list[str] = field(default_factory=list)


class HeuristicJudge(QuestionJudge):

    def judge(self, question: dict[str, Any], evidence: list[EvidenceItem], intake: IntakeResult) -> QuestionAnswer:
        handler = getattr(self, f"_{question['id'].lower()}")
        relevant = [e for e in evidence if question["id"] in e.question_ids and e.entity_match != EntityMatch.NONE]
        v: Verdict = handler(relevant, evidence, intake)
        summary = {RiskCall.YES: "Risk indicated; 'no' was ruled out.",
                   RiskCall.NO: f"Nothing points to a risk {SEARCHED}.",
                   RiskCall.UNCLEAR: "Neither answer could be ruled out: a person must decide."}[v.call]
        return QuestionAnswer(
            question_id=question["id"], question_text=question["text"], response=v.response,
            risk_call=v.call, rationale=f"{summary} Rule: {question['risk_rule']}",
            evidence_ids=list(dict.fromkeys(v.ids)), staff_questions=v.staff, judged_by=self.name,
            case_for_risk=v.for_risk, case_against_risk=v.against_risk,
            why_not_yes=v.why_not_yes, why_not_no=v.why_not_no,
        )

    # ------------------------------------------------------------------ helpers

    def _within_lookback(self, item: EvidenceItem) -> bool:
        if not item.published_date:
            return True  # Undated: keep it rather than silently drop it.
        today: date = self.cfg.assessment_date
        return item.published_date >= today.replace(year=today.year - self.cfg.criteria["lookback_years"])

    @staticmethod
    def _src(items: list[EvidenceItem]) -> str:
        """Compact citation: 'OEFA (Aug 2025) [E-C3]; Agua Viva Collective (Sep 2025) [E-C4]'."""
        def one(e):
            when = e.published_date.strftime("%b %Y") if e.published_date else "undated"
            return f"{e.publisher} ({when}) [{e.evidence_id}]"
        return "; ".join(one(e) for e in items)

    @staticmethod
    def _ids(*groups: list[EvidenceItem]) -> list[str]:
        return [e.evidence_id for g in groups for e in g]

    @staticmethod
    def _factual(items: list[EvidenceItem]) -> list[EvidenceItem]:
        """Items that can support a factual statement (an allegation cannot)."""
        return [e for e in items if e.status != EvidenceStatus.ALLEGATION]

    @staticmethod
    def _related_responses(issues: list[EvidenceItem], responses: list[EvidenceItem], days: int = 90):
        """Responses that answer an issue: shared topic, or published within `days` after it."""
        def topics(e):
            return {t for t in e.topics if not t.startswith(("country:", "sector:"))}
        out = []
        for r in responses:
            for i in issues:
                close = (r.published_date and i.published_date and 0 <= (r.published_date - i.published_date).days <= days)
                if topics(r) & topics(i) or close:
                    out.append(r)
                    break
        return out

    # ------------------------------------------------------------------ Q1: high-risk sectors

    def _q1(self, relevant, evidence, intake: IntakeResult) -> Verdict:
        sectors = self.cfg.active_sectors()
        linked = [e for e in self._factual(relevant)
                  if e.entity_match in CONFIRMED and any(t.startswith("sector:") for t in e.topics)]
        found = sorted({t.split(":", 1)[1] for e in linked for t in e.topics if t.startswith("sector:")})
        found += [f for f in intake.excluded_industry_flags + intake.high_risk_area_flags if f not in found]
        if not found:
            return Verdict(RiskCall.NO, f"No links to excluded or high-risk sectors were found {SEARCHED}.",
                           against_risk="No source links the organisation or its subsidiaries to arms, gambling, "
                                        "pornography, tobacco, extractives or other listed sectors.",
                           why_not_yes=f"No sector link appears {SEARCHED}.")
        labels = ", ".join((sectors[f]["label"] if f in sectors else f).lower() for f in found)
        a_or_an = "an" if labels[0] in "aeiou" else "a"
        divest = [e for e in relevant if "divestment" in e.topics and e.entity_match in CONFIRMED]
        staff = []
        if any(sectors.get(f, {}).get("class") == "excluded" for f in found):
            staff.append(f"An EXCLUDED industry link was found ({labels}). Direct partnerships are barred (policy 0.0 "
                         "s3.3); indirect links are case by case. How direct is this link?")
        for_risk = f"The group's own documents show {a_or_an} {labels} business: {self._src([e for e in linked if e not in divest])}."
        if divest:
            staff.append("Is the sale of the high-risk business complete, and on what date? Ask for the closing "
                         "documents or a registry extract.")
            return Verdict(RiskCall.UNCLEAR,
                           f"The group has had {a_or_an} {labels} business; whether it still owns it is disputed.",
                           for_risk=for_risk,
                           against_risk=f"Sources describe a sale of that business: {self._src(divest)}.",
                           why_not_yes="Ownership is disputed: the organisation says the business was sold, while "
                                       "other sources say the sale is not complete.",
                           why_not_no="The link existed and its end is not confirmed by any independent source.",
                           ids=self._ids(linked, divest), staff=staff)
        return Verdict(RiskCall.YES, f"Links to {labels} were found.", for_risk=for_risk,
                       against_risk="No source indicates the link has ended.",
                       why_not_no=f"A confirmed link to {labels}.", ids=self._ids(linked), staff=staff)

    # ------------------------------------------------------------------ Q2: impact on WASH

    def _q2(self, relevant, evidence, intake) -> Verdict:
        concerns = [e for e in relevant if e.status in
                    (EvidenceStatus.ALLEGATION, EvidenceStatus.INVESTIGATION, EvidenceStatus.FINDING)]
        if not concerns:
            return Verdict(RiskCall.NO, f"No evidence of harm to community water, sanitation or hygiene was found {SEARCHED}.",
                           against_risk="No water or environmental concerns appear in any source.",
                           why_not_yes=f"No water or environmental concern appears {SEARCHED}.")
        established = [e for e in concerns if e.status == EvidenceStatus.FINDING
                       and e.entity_match in CONFIRMED and self._within_lookback(e)]
        if established:
            return Verdict(RiskCall.YES, "An established finding relates to water or environmental harm.",
                           for_risk=f"Established finding: {self._src(established)}.",
                           why_not_no="An established finding of water or environmental harm by the organisation.",
                           ids=self._ids(established),
                           staff=["Does this create or uphold a barrier to WASH access (Ethical Standard 1)?"])
        reasons = []
        if any(e.status == EvidenceStatus.ALLEGATION for e in concerns):
            reasons.append("the allegations are unproven")
        if any(e.status == EvidenceStatus.INVESTIGATION for e in concerns):
            reasons.append("the investigation has no outcome yet")
        if any(e.entity_match == EntityMatch.UNCERTAIN for e in concerns):
            reasons.append("the company named is not confirmed as part of the organisation")
        return Verdict(RiskCall.UNCLEAR, "Water concerns have been raised but none is established.",
                       for_risk=f"Concerns raised: {self._src(concerns)}.",
                       against_risk="Nothing is established: " + "; ".join(reasons) + ".",
                       why_not_yes="Nothing is established: " + "; ".join(reasons) + ".",
                       why_not_no="Unresolved water concerns have been raised by an authority or the community.",
                       ids=self._ids(concerns),
                       staff=["Do these unproven water concerns affect Ethical Standard 1? Ask the organisation or "
                              "the regulator for the outcome."])

    # ------------------------------------------------------------------ Q3: LMIC operations

    def _q3(self, relevant, evidence, intake: IntakeResult) -> Verdict:
        lmic = set(self.cfg.criteria["low_middle_income_countries"])
        confirmed = [e for e in self._factual(relevant) if e.entity_match in CONFIRMED]
        from_card = {c for c in intake.case.countries_of_operation if c in lmic}
        from_evidence = {t.split(":", 1)[1] for e in confirmed for t in e.topics if t.startswith("country:")}
        countries = sorted(from_card | from_evidence)
        listed = ", ".join(intake.case.countries_of_operation) or "none listed"
        if not countries:
            return Verdict(RiskCall.NO, f"Operations found only in {listed} {SEARCHED}.",
                           against_risk=f"Sources show operations only in {listed}.",
                           why_not_yes=f"No low- or middle-income country appears {SEARCHED}.")
        staff = []
        missing = sorted(from_evidence - set(intake.case.countries_of_operation))
        if missing:
            staff.append(f"The case card does not list {', '.join(missing)}, but sources do. Update the case card?")
        return Verdict(RiskCall.YES, f"Operations or supply chain in {', '.join(countries)}.",
                       for_risk=f"Confirmed by {self._src(confirmed)}.",
                       against_risk=(f"The case card lists only {listed}." if missing else ""),
                       why_not_no=f"Guide 1.4 counts operations in {', '.join(countries)} as a potential risk, "
                                  "and the organisation's own or independent sources confirm them.",
                       ids=self._ids(confirmed), staff=staff)

    # ------------------------------------------------------------------ Q4: investigations (10 years)

    def _q4(self, relevant, evidence, intake) -> Verdict:
        official = [e for e in relevant if e.status in (EvidenceStatus.INVESTIGATION, EvidenceStatus.FINDING)]
        in_window = [e for e in official if self._within_lookback(e)]
        too_old = [e for e in official if not self._within_lookback(e)]
        responses = self._related_responses(in_window, [e for e in relevant if e.status == EvidenceStatus.RESPONSE])
        confirmed = [e for e in in_window if e.entity_match in CONFIRMED]
        years = self.cfg.criteria["lookback_years"]
        old_note = f" Older items outside the {years}-year window: {self._src(too_old)}." if too_old else ""
        if confirmed:
            resolved = [r for r in responses if r.resolved]
            staff = (["The organisation says the matter is resolved. Does a resolved finding still weigh on this "
                      "decision?"] if resolved else [])
            first = confirmed[0]
            when = first.published_date.strftime("%B %Y") if first.published_date else "an unknown date"
            return Verdict(RiskCall.YES,
                           f"{first.status.value.capitalize()} by {first.publisher} in {when}"
                           f"{'; the organisation says it has resolved the matter' if resolved else ''}.",
                           for_risk=f"Within {years} years: {self._src(confirmed)}.",
                           against_risk=(f"Organisation's response: {self._src(responses)}. A response does not "
                                         f"remove a case from the {years}-year window." if responses else "") + old_note,
                           why_not_no=f"A confirmed investigation or finding within {years} years.",
                           ids=self._ids(confirmed, responses), staff=staff)
        if in_window:
            names = ", ".join(sorted({e.named_entity for e in in_window if e.named_entity}))
            return Verdict(RiskCall.UNCLEAR,
                           f"An investigation concerns '{names}'; its link to the organisation is not confirmed.",
                           for_risk=f"Investigation: {self._src(in_window)}.",
                           against_risk=(f"The organisation says the company is no longer part of the group: "
                                         f"{self._src(responses)}." if responses else "") + old_note,
                           why_not_yes=f"'{names}' is not confirmed as part of the organisation.",
                           why_not_no="An open investigation exists into a company with a closely matching name.",
                           ids=self._ids(in_window, responses),
                           staff=["Is the investigated company part of the organisation today? Check the company registry."])
        return Verdict(RiskCall.NO, f"No criminal or regulatory investigations within {years} years were found {SEARCHED}.",
                       against_risk=f"None found within {years} years.{old_note}",
                       why_not_yes=f"No investigation or finding within {years} years appears {SEARCHED}.",
                       ids=self._ids(too_old))

    # ------------------------------------------------------------------ Q5: media / NGO allegations

    def _q5(self, relevant, evidence, intake) -> Verdict:
        allegations = [e for e in relevant if e.status == EvidenceStatus.ALLEGATION and not e.resolved]
        if not allegations:
            return Verdict(RiskCall.NO, f"No media or campaign allegations were found {SEARCHED}.",
                           against_risk="No allegations appear in any source.",
                           why_not_yes=f"No allegation or campaign appears {SEARCHED}.")
        responses = self._related_responses(allegations, [e for e in relevant if e.status == EvidenceStatus.RESPONSE])
        official = [e for e in evidence if e.status in (EvidenceStatus.INVESTIGATION, EvidenceStatus.FINDING)
                    and e.entity_match != EntityMatch.NONE]

        def corroboration(a: EvidenceItem) -> list[EvidenceItem]:
            return [o for o in official if NEGATIVE_TOPICS & set(a.topics) & set(o.topics)]

        strong = [a for a in allegations if a.reliability != Reliability.LOW or corroboration(a)]
        against = f"The allegations are unproven. Organisation's response: {self._src(responses)}." if responses \
            else "The allegations are unproven, and no response from the organisation was found."
        if strong:
            backing = [o for a in strong for o in corroboration(a)]
            return Verdict(RiskCall.YES, "An active campaign or allegation targets the organisation; it is unproven.",
                           for_risk=f"Allegation: {self._src(strong)}."
                                    + (f" Corroborated by an official process on the same issue: {self._src(backing)}."
                                       if backing else " From a credible source."),
                           against_risk=against,
                           why_not_no="The campaign is active, unresolved, and backed by a credible source or an "
                                      "official process on the same issue.",
                           ids=self._ids(strong, responses),
                           staff=["The allegations are NOT established facts. How serious is the reputational exposure "
                                  "(Ethical Standard 2)?"])
        return Verdict(RiskCall.UNCLEAR, "One uncorroborated allegation from a low-reliability source.",
                       for_risk=f"Allegation: {self._src(allegations)}.",
                       against_risk=against,
                       why_not_yes="Single low-reliability source, not corroborated by any official process.",
                       why_not_no="The allegation exists and has not been answered or resolved.",
                       ids=self._ids(allegations, responses),
                       staff=["Is this allegation minor or relevant? Should the organisation be asked to respond?"])

    # ------------------------------------------------------------------ Q6: anything else

    def _q6(self, relevant, evidence, intake) -> Verdict:
        serious = [e for e in relevant if e.entity_match in CONFIRMED and {"corruption", "political"} & set(e.topics)
                   and e.status in (EvidenceStatus.INVESTIGATION, EvidenceStatus.FINDING)]
        if serious:
            return Verdict(RiskCall.YES, "Corruption or political issues involve the organisation.",
                           for_risk=f"{self._src(serious)}.", why_not_no="A confirmed corruption or political finding.",
                           ids=self._ids(serious), staff=["Assess against both ethical standards."])
        uncertain = [e for e in relevant if e.entity_match == EntityMatch.UNCERTAIN]
        if uncertain:
            names = ", ".join(sorted({e.named_entity for e in uncertain if e.named_entity}))
            return Verdict(RiskCall.UNCLEAR, f"Sources name '{names}', which may or may not belong to the organisation.",
                           for_risk=f"Unconfirmed related company: {self._src(uncertain)}.",
                           against_risk="No other issue was found.",
                           why_not_yes="Only an unconfirmed name match; no other issue was found.",
                           why_not_no="The group's structure is unresolved, which affects how other answers should be read.",
                           ids=self._ids(uncertain),
                           staff=["Confirm the current group structure (subsidiaries and former names)."])
        return Verdict(RiskCall.NO, f"Nothing else relevant was found {SEARCHED}.",
                       against_risk="No other issues appear in any source.",
                       why_not_yes=f"No other relevant issue appears {SEARCHED}.")
