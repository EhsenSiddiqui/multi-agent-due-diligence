"""Passage retrieval inside long documents, and issue timelines (grouping, status, origin)."""

from datetime import date

from a5dd.issues import build_issues
from a5dd.retrieval import PassageIndex, chunk, focus_text, tokens
from a5dd.schemas import EntityMatch, EvidenceItem, EvidenceStatus, Reliability

FILLER = "The company reported steady revenue growth and opened new offices across several regions this year. " * 40
NEEDLE = "In March the regulator fined the subsidiary for a tailings spill that contaminated the river near the village."


def test_chunks_are_exact_slices_and_cover_the_text():
    text = FILLER + NEEDLE
    spans = chunk(text, 500, 100)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert all(text[s:e] for s, e in spans)


def test_focus_text_keeps_what_is_on_the_last_page():
    text = FILLER + "\n\n" + NEEDLE
    focused, shortened = focus_text(text, tokens("fined tailings spill river contaminated regulator"), 1500, 500, 100)
    assert shortened and len(focused) <= 1600
    assert NEEDLE in focused                      # The relevant passage at the end was kept...
    assert focused.startswith(FILLER[:50])        # ...and so was the opening (who and what).


def test_index_ranks_the_relevant_passage_first():
    index = PassageIndex(400, 50)
    index.add("D1", FILLER)
    index.add("D2", NEEDLE)
    assert index.search("tailings spill river", 1)[0].doc_id == "D2"


def _item(n, event, when, publisher, issue, rel=Reliability.MEDIUM, status=EvidenceStatus.ALLEGATION):
    return EvidenceItem(evidence_id=f"E{n}", doc_id=f"D{n}", source_title="t", source_url=f"https://x/{n}",
                        publisher=publisher, source_type="news", published_date=date(2025, 1, 1), reliability=rel,
                        entity_match=EntityMatch.EXACT, status=status, question_ids=["Q2", "Q4"], standards=["S1"],
                        summary=f"{event} {when}", quote="q", extracted_by="test", event_type=event,
                        event_date=when, issue=issue)


def test_issue_timeline_status_origin_and_independent_sources():
    items = [
        _item(1, "allegation", "2019-03", "NGO Watch", "North Mara mine: water pollution", Reliability.LOW),
        _item(2, "allegation", "2019-04", "Daily News", "Pollution at North Mara"),
        _item(3, "investigation_opened", "2021", "Regulator", "North Mara mine: river pollution", Reliability.HIGH),
        _item(4, "fine", "2023-06", "Regulator", "North Mara mine: water pollution", Reliability.HIGH),
        _item(5, "allegation", "2024-01", "Blog", "North Mara mine: water pollution", Reliability.LOW),
        _item(6, "allegation", "2018", "Paper", "Porgera: land dispute"),
    ]
    issues = {i.label: i for i in build_issues(items, "Barrick Mining Corporation", ["Barrick"], 2026, 10)}
    assert len(issues) == 2
    mara = next(i for label, i in issues.items() if "Mara" in label)
    assert [e.evidence_id for e in mara.events] == ["E1", "E2", "E3", "E4", "E5"]   # In the order things happened.
    assert mara.current_status == "fine"           # A later repeat of the allegation does not undo the fine.
    assert mara.outcome_found and mara.first_reported == "NGO Watch, 2019-03"
    assert mara.independent_sources == 4 and mara.serious


def test_appeal_after_outcome_reopens_the_issue():
    items = [_item(1, "fine", "2022", "Regulator", "Plant X: emissions"),
             _item(2, "appeal", "2023", "Court", "Plant X: emissions appeal")]
    [issue] = build_issues(items, "Org", [], 2026, 10)
    assert issue.current_status == "appeal"


def test_old_issue_is_not_serious():
    [issue] = build_issues([_item(1, "allegation", "2009", "Paper", "Site Y: spill"),
                            _item(2, "allegation", "2010", "Other paper", "Site Y: spill")], "Org", [], 2026, 10)
    assert not issue.serious
