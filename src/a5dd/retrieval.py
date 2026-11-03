"""
Passage retrieval inside the documents found for ONE case (an on-the-fly RAG index).

WHY
    The agents find documents on the live web, but a 200-page sustainability report does not
    fit in one model call. Reading only the opening pages misses what is on page 80.
    Once a document is downloaded we know exactly what is in it, so we can index it and
    retrieve the passages that matter.

HOW
    chunk()          split text into overlapping passages of about 1,200 characters, on paragraph
                     and sentence boundaries; every passage is an exact slice of the original text,
                     so quotes taken from it still pass the word-for-word check.
    PassageIndex     BM25 ranking (the classic keyword relevance formula used by search engines).
    focus_text()     for the analyst: if a document is too long, keep its opening passage (who and
                     what) plus the passages most relevant to the six questions, in document order.
    search()         for the agents' search_found_pages tool: "tailings Tanzania" -> best passages
                     from any document found so far.

SIMPLIFIED
    Keyword relevance (BM25), no embeddings, so it is free, offline and the same every run.
BETTER
    Hybrid retrieval: BM25 plus embeddings (semantic match: "spill" ~ "discharge"), and a reranker.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

_WORD = re.compile(r"[a-zà-ÿ0-9]+")
STOP = set("""the and for that with this from have has had was were are been will would could should their they them
his her its our your which who whom what when where while into onto about after before over under than then also
such these those there here not but all any can may more most other some only very said says per one two""".split())

# Words that make a passage relevant to the six questions (on top of criteria.json search patterns).
RISK_VOCAB = """water river stream lake groundwater drinking sanitation hygiene tailings spill spills leak discharge
contamination contaminated pollution polluted toxic cyanide mercury dam community communities villagers displacement
resettlement land indigenous protest protests violence killings security human rights abuse abuses labour labor
forced child wages strike union safety injury fatality death deaths investigation investigated probe inquiry regulator
court lawsuit lawsuits sued ruling judgment judgement appeal fined fine penalty penalties settlement settled sanction
sanctions convicted charges charged prosecutors allegation allegations alleged accused complaint campaign ngo boycott
bribery bribe corruption fraud laundering tax evasion greenwashing misleading remediation compensation""".split()


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in STOP]


def chunk(text: str, size: int = 1200, overlap: int = 200) -> list[tuple[int, int]]:
    """Split into (start, end) spans of about `size` characters, ending on a paragraph or sentence break."""
    spans, start, n = [], 0, len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > size // 2:
                end = start + cut + 1
        spans.append((start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return spans


@dataclass
class Passage:
    doc_id: str
    start: int
    end: int
    text: str
    score: float = 0.0


class PassageIndex:
    """BM25 over passages from one or more documents."""

    def __init__(self, size: int = 1200, overlap: int = 200, k1: float = 1.5, b: float = 0.75):
        self.size, self.overlap, self.k1, self.b = size, overlap, k1, b
        self.passages: list[Passage] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()
        self._lengths: list[int] = []

    def add(self, doc_id: str, text: str) -> None:
        for s, e in chunk(text, self.size, self.overlap):
            words = tokens(text[s:e])
            self.passages.append(Passage(doc_id, s, e, text[s:e]))
            tf = Counter(words)
            self._tf.append(tf)
            self._df.update(tf.keys())
            self._lengths.append(len(words))

    def scores(self, query_words: list[str]) -> list[float]:
        n = len(self.passages)
        if not n:
            return []
        avg = sum(self._lengths) / n or 1
        q = Counter(query_words)
        out = []
        for tf, length in zip(self._tf, self._lengths):
            s = 0.0
            for w, qn in q.items():
                f = tf.get(w, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[w] + 0.5) / (self._df[w] + 0.5))
                s += qn * idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / avg))
            out.append(s)
        return out

    def search(self, query: str, k: int = 5) -> list[Passage]:
        ranked = sorted(zip(self.scores(tokens(query)), self.passages), key=lambda p: -p[0])
        return [Passage(p.doc_id, p.start, p.end, p.text, round(s, 2)) for s, p in ranked[:k] if s > 0]


def risk_query(cfg, case) -> list[str]:
    """The words that make a passage relevant: question vocabulary, template b search patterns,
    sector keywords, low/middle-income countries, and the organisation's names."""
    words = list(RISK_VOCAB)
    for t in cfg.criteria["search_terms"]:
        words += tokens(" ".join(t["patterns"]))
    for sector in cfg.active_sectors().values():
        words += tokens(" ".join(sector["keywords"]))
    words += tokens(" ".join(cfg.criteria["low_middle_income_countries"]))
    words += tokens(" ".join([case.organisation_name, *case.aliases]))
    return words


def focus_text(text: str, query_words: list[str], max_chars: int, size: int = 1200,
               overlap: int = 200) -> tuple[str, bool]:
    """Fit a long document into `max_chars`: the opening passage plus the most relevant passages,
    in document order, separated by '[...]'. Returns (text, was_shortened)."""
    if len(text) <= max_chars:
        return text, False
    index = PassageIndex(size, 0)  # No overlap here: passages must not repeat each other.
    index.add("doc", text)
    scored = sorted(enumerate(index.scores(query_words)), key=lambda p: -p[1])
    chosen, used = {0}, len(index.passages[0].text)
    for i, _ in scored:
        if i in chosen:
            continue
        length = len(index.passages[i].text) + 7
        if used + length > max_chars:
            continue
        chosen.add(i)
        used += length
    return "\n[...]\n".join(index.passages[i].text for i in sorted(chosen)), True
