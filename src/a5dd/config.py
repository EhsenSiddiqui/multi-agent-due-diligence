"""
Loads config/settings.yaml and config/criteria.json into one object.

Every stage and provider receives this `Config` object instead of reading
files itself, so paths and switches live in exactly one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# Project root = two folders above this file (src/a5dd/config.py -> project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    """Everything a stage might need to know about how this run is set up."""
    root: Path
    settings: dict[str, Any]
    criteria: dict[str, Any]

    # ---- Convenience accessors (keep stage code short and readable) ----

    def path(self, key: str) -> Path:
        """Resolve a path from settings.yaml 'paths' relative to the project root."""
        return (self.root / self.settings["paths"][key]).resolve()

    def provider(self, step: str) -> str:
        """Which implementation to use for a step: e.g. provider('search') -> 'local'."""
        return self.settings["providers"][step]

    def switch(self, name: str) -> bool:
        """A policy switch from settings.yaml, e.g. 'fossil_fuels_excluded'."""
        return bool(self.settings.get("policy_switches", {}).get(name, False))

    @property
    def assessment_date(self) -> date:
        """Date used for 'last 10 years' checks. Fixed in settings for reproducible demos."""
        value = self.settings.get("assessment_date")
        return date.fromisoformat(value) if value else date.today()

    @property
    def questions(self) -> list[dict[str, Any]]:
        return self.criteria["questions"]

    def question(self, qid: str) -> dict[str, Any]:
        return next(q for q in self.questions if q["id"] == qid)

    def active_sectors(self) -> dict[str, dict[str, Any]]:
        """
        Sectors that count under the current policy switches, each with a
        resolved 'class' of either 'excluded' or 'high_risk'.
        A switched-off sector is dropped entirely.
        """
        result = {}
        for key, sector in self.criteria["sectors"].items():
            cls = sector["class"]
            if cls.endswith("_if_switch"):
                if not self.switch(sector["switch"]):
                    continue
                cls = cls.replace("_if_switch", "")
            result[key] = {**sector, "class": cls}
        return result


def load_config(root: Path | None = None, settings_file: str = "config/settings.yaml") -> Config:
    """Read settings + criteria. `root` lets tests point at a temporary copy of the project."""
    root = Path(root) if root else PROJECT_ROOT
    with open(root / settings_file, encoding="utf-8") as fh:
        settings = yaml.safe_load(fh)
    with open(root / settings["paths"]["criteria_file"], encoding="utf-8") as fh:
        criteria = json.load(fh)
    return Config(root=root, settings=settings, criteria=criteria)
