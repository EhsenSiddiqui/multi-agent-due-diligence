"""
AGENT 2: the Analyst (an LLM).  Switch: providers.extractor: llm

Reads ONE source at a time and lists each distinct issue it reports about the organisation,
labelled as allegation, investigation, finding, organisation response or background, with the
issue it belongs to, what happened (event type) and when it happened (event date).
Long documents are cut down to their most relevant passages first (retrieval.focus_text), so
something on page 80 of a report is read, not just the opening pages. Handles
paraphrase, negation, several issues per article and long reports, which keyword rules cannot.

Same output as the rule-based extractor, so stages 4-6 do not change.

GUARD RAILS (in code)
  - who the source is about: the code's name check decides; if the code finds no match but
    the analyst says the source IS about the organisation, the item is kept as "not confirmed"
    for a person to check, never silently promoted;
  - reliability comes from config by source type, not from the model;
  - stage 3 rejects any item whose quote is not word for word in the source.
"""

from __future__ import annotations

import json

from ..issues import EVENT_TYPES
from ..retrieval import focus_text, risk_query
from ..schemas import CaseCard, EntityMatch, EvidenceItem, EvidenceStatus, Reliability, SourceDocument
from ..text_utils import match_entity
from .base import EvidenceExtractor
from .llm_client import LLMClient

SYSTEM = """You are the analyst in a due-diligence team at a water charity.
You read ONE source and list each distinct issue it reports about the organisation named
(or its subsidiaries, listed as aliases). Ignore issues about other companies.
If the source says nothing relevant about the organisation, return {"about_organisation": false, "items": []}.

For each issue:
- status, one of: allegation (claimed, not established), investigation (an authority is examining it),
  finding (established: fine, ruling, conviction, settlement, admitted breach), response (the organisation's
  own statement or reply), context (neutral facts: ownership, countries, sectors, policies).
  Never upgrade: an allegation reported by a newspaper is still an allegation. If unsure, choose the weaker.
- quote: ONE or two sentences copied EXACTLY, character for character, from the source text.
- question_ids: which of Q1..Q6 it bears on (definitions given). Use context items for Q1 (sectors) and
  Q3 (countries) too, e.g. "operates mines in Tanzania".
- topics: short tags such as water, pollution, tailings, labour, human_rights, corruption, tax, greenwash,
  health_safety, sector:<sector key>, country:<country>.
- event_year: the year the event happened if the source says so, else null.
- resolved: true if the source says it was resolved (ruling, settlement, closed), false if ongoing, else null.
- summary: one careful line in neutral words ("alleged", "investigated", "fined").
- issue: a short label naming the place or project AND the problem, so the same issue gets the same label in
  every source, e.g. "North Mara mine: security force violence", "Huarani camp: river pollution".
- event_type, one of: allegation, investigation_opened, charges_or_lawsuit, appeal, ruling, fine, settlement,
  dismissed, remediation, company_response, background.
- event_date: when the EVENT happened ("YYYY", "YYYY-MM" or "YYYY-MM-DD"), which may differ from the article's
  date. null if the source does not say.
Long documents are shortened to their most relevant passages, separated by "[...]". Quote only from the text given.
Return JSON: {"about_organisation": true|false, "items": [{"status", "quote", "question_ids", "topics",
 "issue", "event_type", "event_date", "resolved", "summary"}]}  At most 6 items, most important first."""


class LLMExtractor(EvidenceExtractor):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llm = LLMClient(cfg, role="analyst")
        self.max_chars = int(cfg.settings.get("llm", {}).get("analyst_max_chars", 15000))

    def extract(self, doc: SourceDocument, case: CaseCard) -> list[EvidenceItem]:
        entity, matched = match_entity(f"{doc.title}. {doc.text}", case)
        r = self.cfg.settings.get("retrieval", {})
        text, shortened = focus_text(doc.text, risk_query(self.cfg, case), self.max_chars,
                                     int(r.get("chunk_chars", 1200)), int(r.get("chunk_overlap", 200)))
        user = json.dumps({
            "organisation": case.organisation_name,
            "aliases": case.aliases,
            "questions": {q["id"]: q["text"] for q in self.cfg.questions},
            "sector_keys": list(self.cfg.active_sectors().keys()),
            "document": {"title": doc.title, "publisher": doc.publisher, "type": doc.source_type,
                         "date": str(doc.published_date), "url": doc.url, "text": text,
                         "shortened_to_relevant_passages": shortened},
        }, indent=1)
        data = self.llm.complete_json(SYSTEM, user)
        if not data.get("items"):
            return []
        if entity == EntityMatch.NONE and data.get("about_organisation"):
            entity = EntityMatch.UNCERTAIN  # The model thinks it is about us; code could not confirm. A person checks.

        items = []
        reliability = self.cfg.criteria["reliability_by_source_type"].get(doc.source_type, "low")
        valid = {q["id"] for q in self.cfg.questions}
        for i, raw in enumerate(data.get("items", [])[:6], start=1):
            try:
                status = EvidenceStatus(str(raw.get("status", "context")).lower())
            except ValueError:
                status = EvidenceStatus.CONTEXT
            qids = sorted(set(raw.get("question_ids", [])) & valid)
            topics = [str(t) for t in raw.get("topics", [])]
            event_type = str(raw.get("event_type") or "")
            event_date = str(raw.get("event_date") or raw.get("event_year") or "") or None
            items.append(EvidenceItem(
                evidence_id=f"E-{doc.doc_id}-{i}",
                doc_id=doc.doc_id,
                source_title=doc.title,
                source_url=doc.url,
                publisher=doc.publisher,
                source_type=doc.source_type,
                published_date=doc.published_date,
                reliability=Reliability(reliability),
                entity_match=entity,
                status=status,
                question_ids=qids,
                standards=sorted({s for q in qids for s in self.cfg.question(q)["standards"]}),
                topics=topics + (["identity_uncertain"] if entity == EntityMatch.UNCERTAIN else []),
                summary=str(raw.get("summary") or doc.title),
                quote=str(raw.get("quote", "")),
                resolved=raw.get("resolved"),
                extracted_by=f"{self.name}:{self.llm.model}",
                named_entity=matched or case.organisation_name,
                event_type=event_type if event_type in EVENT_TYPES else "",
                event_date=event_date,
                issue=str(raw.get("issue") or ""),
            ))
        return items
