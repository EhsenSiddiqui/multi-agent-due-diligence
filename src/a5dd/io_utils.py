"""
Reading and writing stage outputs.

Every stage saves exactly one JSON file in results/<case_id>/, named after the
stage (e.g. 03_evidence.json). Reading goes through `read_stage`, which
validates the JSON against the expected schema. So if an improved stage ever
writes something malformed, the NEXT stage fails loudly with a clear message
instead of producing a quietly wrong report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .schemas import SCHEMA_VERSION

T = TypeVar("T", bound=BaseModel)

# Single source of truth for file names. Stages refer to these keys, never to raw strings.
STAGE_FILES = {
    "intake": "01_intake.json",
    "search": "02_search.json",
    "evidence": "03_evidence.json",
    "answers": "04_answers.json",
    "score": "05_score.json",
    "manifest": "06_manifest.json",
}


class ContractError(RuntimeError):
    """Raised when a stage file is missing, from another schema version, or invalid."""


def case_dir(results_dir: Path, case_id: str) -> Path:
    """results/<case_id>/, created if needed."""
    path = results_dir / case_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_stage(results_dir: Path, case_id: str, key: str, model: BaseModel) -> Path:
    """Save one stage's output as pretty JSON (easy to read and diff by humans)."""
    path = case_dir(results_dir, case_id) / STAGE_FILES[key]
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return path


def read_stage(results_dir: Path, case_id: str, key: str, model_cls: type[T]) -> T:
    """Load and validate one stage's output. Fails with a helpful message if anything is off."""
    path = results_dir / case_id / STAGE_FILES[key]
    if not path.exists():
        raise ContractError(f"{path} not found. Run the earlier stage first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ContractError(
            f"{path} has schema_version {version!r}, expected {SCHEMA_VERSION!r}. "
            "Re-run the earlier stage or update the reader."
        )
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ContractError(f"{path} does not match {model_cls.__name__}:\n{exc}") from exc
