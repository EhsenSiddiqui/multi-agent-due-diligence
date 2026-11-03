"""Every file must say who prepared it: partner, third party, Water Care staff or the AI agent."""

import csv
import json

from docx import Document
from pypdf import PdfReader

from a5dd.config import load_config
from a5dd.io_utils import STAGE_FILES
from a5dd.pipeline import run_case, offline_cases

ORIGINS = {"partner_organisation", "third_party_public_source", "water_care_staff", "ai_agent"}


def test_stage_outputs_are_labelled_as_agent_drafts(cfg):
    run_case(cfg, "case_c_corvane", quiet=True)
    folder = cfg.path("results_dir") / "case_c_corvane"
    for name in STAGE_FILES.values():
        prov = json.loads((folder / name).read_text())["provenance"]
        assert prov["origin"] == "ai_agent" and prov["human_reviewed"] is False

    evidence = {e["doc_id"]: e["origin"] for e in json.loads((folder / "03_evidence.json").read_text())["items"]}
    assert evidence["C5"] == "partner_organisation"          # the company's own statement
    assert evidence["C3"] == "third_party_public_source"     # the regulator's notice

    rows = list(csv.DictReader(open(folder / "06_evidence.csv", encoding="utf-8")))
    assert all(r["source_published_by"] and r["row_prepared_by"].startswith("AI agent") for r in rows)

    form = Document(folder / "06_template_c.docx")
    assert "PROVENANCE" in form.paragraphs[1].text
    filled_by = {row.cells[2].text for t in form.tables for row in t.rows[1:]}
    assert filled_by == {"AI agent (draft)", "Water Care staff"}

    manifest = json.loads((folder / "06_manifest.json").read_text())
    assert set(manifest["file_provenance"]) == set(manifest["files"])
    assert "Who prepared this" in (folder / "06_assessment.html").read_text(encoding="utf-8")


def test_inputs_are_labelled():
    cfg = load_config()
    for case_id in offline_cases(cfg):
        case_dir = cfg.path("cases_dir") / case_id
        assert json.loads((case_dir / "case.json").read_text())["provenance"]["origin"] == "water_care_staff"
        key = cfg.path("answer_keys_dir") / f"{case_id}.json"
        assert json.loads(key.read_text())["provenance"]["origin"] == "water_care_staff"
        for page in json.loads((case_dir / "web" / "index.json").read_text())["pages"]:
            assert page["origin"] in ORIGINS
            path = case_dir / "web" / page["file"]
            if path.suffix == ".pdf":
                assert "PROVENANCE" in (PdfReader(str(path)).metadata.get("/Subject") or "")
            else:
                assert "<!-- PROVENANCE:" in path.read_text(encoding="utf-8")
    for f in ("criteria_file", "sources_file"):
        assert json.loads(cfg.path(f).read_text())["_provenance"]["origin"] == "water_care_staff"
