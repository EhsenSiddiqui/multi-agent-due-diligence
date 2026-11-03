"""
Small, dependency-free text helpers shared by several providers.

Kept separate so the offline extractor and the LLM extractor apply the SAME
entity-matching and quote rules, which keeps results consistent.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schemas import CaseCard, EntityMatch

# Words that often follow a company's short name but are not part of a different entity.
_GENERIC_FOLLOWERS = {"group", "ltd", "inc", "holdings", "s.a.", "co", "company"}


def contains_any(text: str, patterns: list[str]) -> bool:
    """
    Case-insensitive: does any pattern appear at the START of a word in text?
    Patterns are word stems ("pollut" matches "polluted"). Matching only at word
    starts stops false hits inside names, e.g. "stream" inside "Northstream".
    """
    return any(re.search(r"(?<![a-z0-9])" + re.escape(p.lower().strip()), text.lower()) for p in patterns if p.strip())


# Company-name abbreviations that end in a full stop but do not end a sentence.
_ABBREV_END = re.compile(r"(?:\b(?:[A-Z]\.){1,4}[A-Z]?\.?|\b(?:Ltd|Inc|Co|Corp|Mr|Ms|Dr|St|No))\.?$")


def split_sentences(text: str) -> list[str]:
    """
    Simple sentence splitter for short news-style text. It does not split after
    company abbreviations such as "S.A.", "S.A.C." or "Ltd.", so quotes keep the full name.
    """
    sentences: list[str] = []
    for block in text.split("\n"):  # Parsed documents keep one paragraph or heading per line.
        glued: list[str] = []
        for piece in (p for p in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", block) if p.strip()):
            if glued and _ABBREV_END.search(glued[-1]):
                glued[-1] = f"{glued[-1]} {piece.strip()}"  # Glue back the wrongly split part.
            else:
                glued.append(piece.strip())
        sentences += glued
    return sentences


def best_quote(text: str, weighted_patterns: list[tuple[int, list[str]]], fallback_terms: list[str]) -> str:
    """
    Pick the most informative sentence as the supporting quote.

    `weighted_patterns` is a list of (points, words): a sentence earns the points of every
    group it matches. The extractor gives status words (fined, investigation, alleges) the
    most points, then topic words, then place names. A sentence also earns +1 for naming
    the organisation and +1 for being a real sentence (40+ characters ending in . ! ?), so
    headings, letterheads and table labels lose to full sentences.
    Ties go to the earliest sentence. The quote is always an exact substring of the source.
    """
    sentences = split_sentences(text)
    if not sentences:
        return text[:200]

    def score(s: str) -> int:
        points = sum(w for w, words in weighted_patterns if words and contains_any(s, words))
        points += 1 if fallback_terms and contains_any(s, fallback_terms) else 0
        points += 1 if len(s) >= 40 and s.rstrip().endswith((".", "!", "?", '"')) else 0
        return points

    return max(sentences, key=score)  # max() keeps the first of equal scores.


def _known_names(case: CaseCard) -> list[str]:
    return [case.organisation_name, *case.aliases]


def match_entity(text: str, case: CaseCard) -> tuple[EntityMatch, str]:
    """
    Decide whether a document is about the organisation we are checking.

    Order of checks (first match wins):
      1. Full legal name appears                     -> EXACT
      2. A multi-word alias appears                  -> ALIAS
      3. A one-word alias appears on its own
         (not followed by another capitalised word,
          e.g. "Corvane said" but not "Corvane Dental") -> ALIAS
      4. A phrase starting with the short name looks
         similar to a known name ("Corvane Minerales"
         vs "Corvane Minerals S.A.")                 -> UNCERTAIN
      5. Only different-looking names               -> NONE (a namesake)

    Returns (match, the name or phrase that decided it).
    DEMO SIMPLIFICATION: a production version would use a company registry
    (e.g. OpenCorporates) and identifiers, not string similarity.
    """
    if case.organisation_name.lower() in text.lower():
        return EntityMatch.EXACT, case.organisation_name

    multi = [a for a in case.aliases if " " in a]
    single = [a for a in case.aliases if " " not in a]
    for alias in multi:
        if alias.lower() in text.lower():
            return EntityMatch.ALIAS, alias
    for alias in single:
        # Bare short name not followed by a capitalised word (which would make it a different name).
        for m in re.finditer(rf"\b{re.escape(alias)}\b(?:'s)?(\s+([A-Z][\w.]*))?", text):
            follower = (m.group(2) or "").lower()
            if not m.group(2) or follower in _GENERIC_FOLLOWERS:
                return EntityMatch.ALIAS, alias

    # Look for "<ShortName> <Capitalised words>" phrases and compare to known names.
    short = case.organisation_name.split()[0]
    phrases = [m.group(0) for m in re.finditer(rf"\b{re.escape(short)}(?:\s+[A-Z][\w.]*){{1,3}}", text)]
    best_name, best_score = "", 0.0
    for phrase in phrases:
        for known in _known_names(case):
            score = SequenceMatcher(None, phrase.lower(), known.lower()).ratio()
            if score > best_score:
                best_name, best_score = phrase, score
    if phrases and best_score >= 0.8:
        return EntityMatch.UNCERTAIN, best_name
    if phrases:
        return EntityMatch.NONE, phrases[0]
    return EntityMatch.NONE, ""


def mentions_identity(text: str, case: CaseCard) -> bool:
    """Loose check used by search: does the text mention the name, an alias or the short name?"""
    low = text.lower()
    short = case.organisation_name.split()[0].lower()
    return short in low or any(n.lower() in low for n in _known_names(case))
