"""
Rule-based lead finder: the agent's "what should I look at next?" (DEMO implementation).

After the fixed guide 1.3 searches, stage 2 asks this class for follow-up actions,
runs them, and asks again, until nothing new turns up or the budget runs out.

TWO KINDS OF LEAD
  1. Follow a link to a PRIMARY SOURCE. A news story or NGO post that links to a PDF
     notice, court bulletin, filing, registry or press release: open the original rather
     than rely on someone's summary of it.
  2. Search a COUNTERPARTY. When a page about the organisation names another company in
     an ownership change ("sold to Altiplano Resources"), search that company together
     with our organisation's name to find the other side of the story.

WHAT WAS SIMPLIFIED
  Keyword rules decide which links matter and which names are counterparties.

BETTER
  leads_llm.py: an LLM reads the pages found so far and proposes the next actions,
  with a reason for each. Same output (a list of Lead objects), so stage 2 is unchanged.
"""

from __future__ import annotations

import re

from ..schemas import CaseCard, SourceDocument
from ..text_utils import contains_any, mentions_identity
from .base import Lead, LeadFinder

# Words in a link's text or address that suggest a primary source.
PRIMARY_SOURCE_HINTS = ["notice", "resolution", "resolucion", "bulletin", "court", "judgment", "ruling", "decision",
                        "filing", "registry", "register", "press release", "investor update", "statement",
                        "annual review", "annual report", "regulator"]
# Words that signal an ownership change, which makes the other company worth searching.
OWNERSHIP_WORDS = ["sell", "sold", "the sale", "acquisition", "acquire", "subsidiary", "parent", "owned by", "merger"]
# A company-like name: capitalised words ending in a corporate word.
COMPANY_NAME = re.compile(r"\b((?:[A-Z][a-zA-Z]+\s){1,3}(?:Resources|Holdings|Group|Mining|Minerals|Capital|"
                          r"Partners|Energy|Inc\.?|Ltd\.?|S\.A\.C?\.?))")


class HeuristicLeadFinder(LeadFinder):

    def propose(self, documents: list[SourceDocument], case: CaseCard, done_urls: set[str],
                done_searches: set[str], limit: int) -> list[Lead]:
        leads: list[Lead] = []
        our_short = case.organisation_name.split()[0].lower()

        for doc in documents:
            if not mentions_identity(f"{doc.title} {doc.text}", case):
                continue  # Only chase leads from pages about the organisation.

            # 1. Links to primary sources.
            for link in doc.links:
                if link.url in done_urls or any(lead.target == link.url for lead in leads):
                    continue
                label = f"{link.text} {link.url}".lower()
                if link.url.lower().endswith(".pdf") or contains_any(label, PRIMARY_SOURCE_HINTS):
                    leads.append(Lead("follow_link", link.url,
                                      f"Primary source linked from {doc.doc_id} ({doc.publisher}): '{link.text}'"))

            # 2. Counterparties in ownership changes.
            for sentence in re.split(r"(?<=[.!?])\s+|\n", doc.text):
                if not contains_any(sentence, OWNERSHIP_WORDS):
                    continue
                for name in COMPANY_NAME.findall(sentence):
                    name = name.strip()
                    if name.split()[0].lower() == our_short or name in done_searches:
                        continue
                    if any(lead.target == name for lead in leads):
                        continue
                    leads.append(Lead("search", name,
                                      f"Named as a party to an ownership change in {doc.doc_id}: search it with "
                                      f"'{case.organisation_name.split()[0]}'",
                                      patterns=[case.organisation_name.split()[0]]))
        return leads[:limit]
