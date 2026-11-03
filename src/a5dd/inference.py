"""
"Safe to infer" rules: what each piece of evidence licenses us to say, and what it does not.

Borrowed from reading-comprehension discipline (GRE "safe to infer" questions): a source
supports only the conclusion it actually states. Stage 3 applies these rules to EVERY
evidence row, whichever extractor produced it, so the limits of each source are always
written next to it in the report.

    allegation    -> safe: someone made this claim.          NOT safe: the claim is true.
    investigation -> safe: an authority opened a case.       NOT safe: wrongdoing occurred.
    finding       -> safe: an established outcome.           NOT safe: it is still ongoing (if resolved).
    response      -> safe: the organisation says this.       NOT safe: what it says is accurate.
    self-description (partner's own site)                    NOT safe: independently verified.
    similar name  -> NOT safe: that the named company is part of the organisation.

SIMPLIFIED: fixed sentence templates per status.
BETTER: an LLM drafts source-specific limits, checked against these same rules.
"""

from __future__ import annotations

from .schemas import CaseCard, EntityMatch, EvidenceItem, EvidenceStatus, Origin, Reliability


def apply_inference_rules(item: EvidenceItem, case: CaseCard, lookback_years: int, assessment_year: int) -> None:
    """Fill item.safe_to_infer and item.not_safe_to_infer in place (only if the extractor left them empty)."""
    if item.safe_to_infer:
        return
    org = case.organisation_name
    named = item.named_entity or org
    when = item.published_date.strftime("%B %Y") if item.published_date else "an unknown date"
    not_safe: list[str] = []

    if item.status == EvidenceStatus.ALLEGATION:
        safe = f"{item.publisher} made this claim in {when}."
        not_safe.append("That the claim is true or proven.")
        if item.reliability == Reliability.LOW:
            not_safe.append("That any independent source confirms it.")
    elif item.status == EvidenceStatus.INVESTIGATION:
        safe = f"{item.publisher} opened a case concerning {named} ({when})."
        not_safe.append("That any wrongdoing occurred: no outcome is reported.")
    elif item.status == EvidenceStatus.FINDING:
        safe = f"{item.publisher} reports an established outcome against {named} ({when})."
        if item.resolved:
            not_safe.append("That the issue is still ongoing.")
        if item.published_date and item.published_date.year < assessment_year - lookback_years:
            not_safe.append(f"That it falls within the {lookback_years}-year window.")
        not_safe.append("That it shows wider or repeated misconduct.")
    elif item.status == EvidenceStatus.RESPONSE:
        safe = f"{org} states this position ({when})."
        not_safe.append("That the statement is accurate: it is the organisation's own account.")
    elif item.origin == Origin.PARTNER:
        safe = f"{org} describes itself this way ({when})."
        not_safe.append("That this self-description has been independently verified.")
    else:
        safe = f"{item.publisher} reports this ({when})."
        if "divestment" in item.topics:
            not_safe.append("That the ownership change has been completed.")

    if item.entity_match == EntityMatch.UNCERTAIN:
        not_safe.insert(0, f"That '{named}' is part of {org}: the link is not confirmed.")

    item.safe_to_infer = safe
    item.not_safe_to_infer = not_safe
