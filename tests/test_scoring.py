"""The guide 1.4 counting rule must never drift."""

import pytest

from a5dd.schemas import RiskLevel
from a5dd.stages.s5_score import level_for


@pytest.mark.parametrize("count,level", [(0, RiskLevel.LOW), (1, RiskLevel.MEDIUM), (2, RiskLevel.MEDIUM),
                                         (3, RiskLevel.HIGH), (6, RiskLevel.HIGH)])
def test_counting_rule(count, level):
    assert level_for(count) == level
