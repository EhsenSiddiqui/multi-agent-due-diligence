"""
Offline evidence extractor (DEMO implementation, no API key needed).

WHAT IT DOES
    For each source document it decides:
      - entity match: is this really about our organisation? (text_utils.match_entity)
      - status: allegation / investigation / finding / response / context
      - topics: water, wages, corruption, sector:extractives, country:Peru, ...
      - which core questions (Q1..Q6) the item bears on
      - a one-line summary in careful language, and an exact supporting quote

WHAT WAS SIMPLIFIED
    - Keyword rules instead of reading comprehension. It will miss paraphrases
      ("authorities are looking into") and can be fooled by negation ("was NOT fined").
    - One evidence item per document. A long article with three separate issues
      should really produce three items.

BETTER VERSION
    extractor_llm.py asks an LLM to fill the same EvidenceItem schema, with the same
    entity and quote rules enforced afterwards by stage 3. Switch with
    `providers: extractor: llm` in settings.yaml.
"""

from __future__ import annotations

from ..schemas import CaseCard, EntityMatch, EvidenceItem, EvidenceStatus, Reliability, SourceDocument
from ..text_utils import best_quote, contains_any, match_entity
from .base import EvidenceExtractor

# --- Status keywords (checked in this order: the strongest status wins) ---------------
FINDING_WORDS = ["was fined", "fined $", "ordered to pay", "convicted", "pleaded guilty", "penalty of", "found liable", "settled with"]
INVESTIGATION_WORDS = ["investigation", "investigating", "inquiry", "probe", "proceedings", "under review"]
# Note: plain "says" is NOT used: "the brand says demand is growing" is not an allegation.
ALLEGATION_WORDS = ["alleg", "accus", "claim that", "calls on", "call on", "campaign against"]

# --- Topic keywords -> tags. Questions are mapped from these tags below. ----------------
TOPIC_WORDS = {
    "water": ["water", "river", "stream", "tributary", "drinking"],
    "environment": ["pollut", "contaminat", "discharge", "spill", "emission", "toxic"],
    "health_safety": ["injur", "safety", "unguarded"],
    "wages": ["wage", "poverty pay"],
    "labour": ["worker", "labour", "labor", "overtime"],
    "corruption": ["brib", "corrupt", "fraud", "launder", "embezzl"],
    "political": ["political donation", "lobbying"],
    "divestment": ["sell ", "sold", "the sale", "divest", "acquisition of"],
}
RESOLVED_WORDS = ["resolved", "paid the fine in full", "remediat", "corrective action"]

# Short, careful labels for summaries. Never state an allegation as fact.
STATUS_PHRASE = {
    EvidenceStatus.ALLEGATION: "Allegation (not established)",
    EvidenceStatus.INVESTIGATION: "Investigation opened (no outcome reported)",
    EvidenceStatus.FINDING: "Established finding",
    EvidenceStatus.RESPONSE: "Organisation's response",
    EvidenceStatus.CONTEXT: "Background",
}


class HeuristicExtractor(EvidenceExtractor):

    def extract(self, doc: SourceDocument, case: CaseCard) -> list[EvidenceItem]:
        text = f"{doc.title}. {doc.text}"
        entity, matched_name = match_entity(text, case)

        status = self._status(doc, text)
        topics = self._topics(text, case)
        if entity == EntityMatch.UNCERTAIN:
            topics.append("identity_uncertain")
        question_ids = self._questions(status, topics, entity)
        standards = sorted({s for q in question_ids for s in self.cfg.question(q)["standards"]})

        # Quote: status words weigh most, then topic and sector words, then country names.
        sectors = self.cfg.active_sectors()
        status_words = self._status_words(status) + (RESOLVED_WORDS if status == EvidenceStatus.RESPONSE else [])
        weighted = [
            (4, status_words),
            (2, [w for t in topics for w in TOPIC_WORDS.get(t, [])]),
            (2, [w for t in topics if t.startswith("sector:") for w in sectors[t[7:]]["keywords"]]),
            (1, [t[8:] for t in topics if t.startswith("country:")]),
        ]
        quote = best_quote(doc.text, weighted, [matched_name or case.organisation_name])

        date_txt = doc.published_date.isoformat() if doc.published_date else "undated"
        summary = f"{STATUS_PHRASE[status]}: {doc.title} ({doc.publisher}, {date_txt})"
        if entity == EntityMatch.UNCERTAIN:
            summary += f". Names '{matched_name}'; link to the organisation not confirmed"

        reliability = self.cfg.criteria["reliability_by_source_type"].get(doc.source_type, "low")

        return [
            EvidenceItem(
                evidence_id=f"E-{doc.doc_id}",
                doc_id=doc.doc_id,
                source_title=doc.title,
                source_url=doc.url,
                publisher=doc.publisher,
                source_type=doc.source_type,
                published_date=doc.published_date,
                reliability=Reliability(reliability),
                entity_match=entity,
                status=status,
                question_ids=question_ids,
                standards=standards,
                topics=topics,
                summary=summary,
                quote=quote,
                resolved=True if contains_any(text, RESOLVED_WORDS) else None,
                extracted_by=self.name,
                named_entity=matched_name,
            )
        ]

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _status(doc: SourceDocument, text: str) -> EvidenceStatus:
        """Decide how established the content is, using source type first, then wording."""
        if doc.source_type == "company_statement":
            return EvidenceStatus.RESPONSE
        if doc.source_type in ("company_website", "annual_report"):
            return EvidenceStatus.CONTEXT
        if contains_any(text, FINDING_WORDS):
            return EvidenceStatus.FINDING
        if contains_any(text, INVESTIGATION_WORDS):
            return EvidenceStatus.INVESTIGATION
        if contains_any(text, ALLEGATION_WORDS):
            return EvidenceStatus.ALLEGATION
        return EvidenceStatus.CONTEXT

    @staticmethod
    def _status_words(status: EvidenceStatus) -> list[str]:
        return {
            EvidenceStatus.FINDING: FINDING_WORDS,
            EvidenceStatus.INVESTIGATION: INVESTIGATION_WORDS,
            EvidenceStatus.ALLEGATION: ALLEGATION_WORDS,
        }.get(status, [])

    def _topics(self, text: str, case: CaseCard) -> list[str]:
        """Tag the document with topics, high-risk sectors and low/middle-income countries."""
        topics = [t for t, words in TOPIC_WORDS.items() if contains_any(text, words)]
        for key, sector in self.cfg.active_sectors().items():
            if contains_any(text, sector["keywords"]):
                topics.append(f"sector:{key}")
        for country in self.cfg.criteria["low_middle_income_countries"]:
            if country.lower() in text.lower():
                topics.append(f"country:{country}")
        return topics

    @staticmethod
    def _questions(status: EvidenceStatus, topics: list[str], entity: EntityMatch) -> list[str]:
        """Map an item to the template b questions it is relevant to."""
        q = set()
        if any(t.startswith("sector:") for t in topics) or "divestment" in topics:
            q.add("Q1")
        if {"water", "environment"} & set(topics):
            q.add("Q2")
        if any(t.startswith("country:") for t in topics):
            q.add("Q3")
        if status in (EvidenceStatus.INVESTIGATION, EvidenceStatus.FINDING):
            q.add("Q4")
        if status == EvidenceStatus.ALLEGATION:
            q.add("Q5")
        if status == EvidenceStatus.RESPONSE:
            q.update({"Q4", "Q5"})  # A response is read alongside the issue it answers.
        if {"corruption", "political"} & set(topics) or entity == EntityMatch.UNCERTAIN:
            q.add("Q6")
        return sorted(q)
