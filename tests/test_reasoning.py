"""'Safe to infer' limits on every source, and the effort model."""

import sys
from pathlib import Path

from a5dd.io_utils import read_stage
from a5dd.pipeline import run_case
from a5dd.schemas import EvidenceTable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_every_source_states_what_it_does_not_prove(cfg):
    run_case(cfg, "case_c_corvane", quiet=True)
    items = {e.doc_id: e for e in read_stage(cfg.path("results_dir"), "case_c_corvane", "evidence", EvidenceTable).items}
    assert all(e.safe_to_infer for e in items.values())
    assert any("true or proven" in n for n in items["C4"].not_safe_to_infer)        # allegation is not a fact
    assert any("not confirmed" in n for n in items["C3"].not_safe_to_infer)          # similar name is not the org
    assert any("wrongdoing" in n for n in items["C3"].not_safe_to_infer)             # investigation is not a finding
    assert any("own account" in n for n in items["C5"].not_safe_to_infer)            # company statement is a claim


def test_effort_model_scales_with_risk(cfg):
    import effort_estimate
    for case in ("case_a_brightwell", "case_c_corvane"):
        run_case(cfg, case, quiet=True)
    clean = effort_estimate.estimate(cfg, "case_a_brightwell")
    messy = effort_estimate.estimate(cfg, "case_c_corvane")
    assert clean["review_minutes"] < messy["review_minutes"] < cfg.settings["effort"]["manual_baseline_minutes"][1]
    assert clean["manual_result_pages"] == 132
