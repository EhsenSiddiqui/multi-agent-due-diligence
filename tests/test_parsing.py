"""The scraper must strip page furniture, find real dates, and drop PDF footers."""

import json
from pathlib import Path

from a5dd.config import load_config
from a5dd.parsing import build_document, find_date, load_sources
from a5dd.schemas import CaseCard


def _doc(case_id, doc_id):
    cfg = load_config()
    web = cfg.path("cases_dir") / case_id / "web"
    page = next(p for p in json.loads((web / "index.json").read_text())["pages"] if p["id"] == doc_id)
    case = CaseCard.model_validate_json((web.parent / "case.json").read_text())
    return build_document(doc_id, page["url"], page["content_type"], (web / page["file"]).read_bytes(),
                          case, load_sources(str(cfg.path("sources_file"))), "test")


def test_html_boilerplate_removed():
    doc = _doc("case_a_brightwell", "A2")
    # The "Related stories" sidebar mentions an investigation and bribery: it must not leak into the text.
    assert "investigation" not in doc.text.lower() and "bribery" not in doc.text.lower()
    assert "cookie" not in doc.text.lower() and "advertisement" not in doc.text.lower()
    assert doc.source_type == "news" and doc.publisher == "Mississauga Local Times"


def test_html_date_from_time_tag_when_metadata_missing():
    doc = _doc("case_b_northstream", "B6")
    assert str(doc.published_date) == "2024-10-03"
    assert any("<time>" in n for n in doc.parse_notes)


def test_pdf_date_from_text_not_metadata_and_footer_removed():
    doc = _doc("case_c_corvane", "C3")
    assert str(doc.published_date) == "2025-08-12"      # metadata says the build date
    assert "Page 1" not in doc.text and "Fictional page created" not in doc.text
    assert doc.source_type == "regulator" and doc.content_type == "application/pdf"


def test_own_website_classified_from_case_card():
    assert _doc("case_c_corvane", "C5").source_type == "company_statement"
    assert _doc("case_c_corvane", "C8").source_type == "annual_report"


def test_find_date_formats():
    assert str(find_date("Published 12 August 2025")) == "2025-08-12"
    assert str(find_date("Thursday, March 14, 2019")) == "2019-03-14"
    assert str(find_date("2024-10-03T07:30")) == "2024-10-03"
