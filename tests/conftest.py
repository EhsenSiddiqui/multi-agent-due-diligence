"""Shared test fixtures: a Config whose results go to a temporary folder.

Tests run the RULE-BASED baseline by default (no API key, no network, same result every time).
Tests of the AI agents switch them on explicitly and use a fake model (see test_multi_agent.py).
"""

import pytest

from a5dd.config import load_config
from a5dd.pipeline import use_rules


@pytest.fixture
def cfg(tmp_path):
    config = load_config()
    # Absolute path: Config.path() joins it onto the project root, which keeps it absolute.
    config.settings["paths"]["results_dir"] = str(tmp_path / "results")
    config.settings["llm"]["cache_dir"] = str(tmp_path / "llm_cache")
    use_rules(config)
    return config
