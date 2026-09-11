"""Tests for evidence model guards."""

import pytest

from ai_lab.core.enums import EvidenceKind
from ai_lab.core.models import Claim, ConfidenceBreakdown


def test_fact_requires_source_or_evidence() -> None:
    with pytest.raises(ValueError, match="FACT requires"):
        Claim(statement="Material withstands 2.1 GPa", kind=EvidenceKind.FACT)


def test_fact_with_source_ok() -> None:
    claim = Claim(
        statement="Material withstands 2.1 GPa",
        kind=EvidenceKind.FACT,
        source="doi:10.example/silk",
        conditions={"temperature_C": 25, "humidity_pct": 50},
    )
    assert claim.kind == EvidenceKind.FACT


def test_assumption_does_not_become_fact() -> None:
    claim = Claim(
        statement="Spinning is the bottleneck",
        kind=EvidenceKind.ASSUMPTION,
        assumptions=["No primary plant data"],
    )
    assert claim.kind == EvidenceKind.ASSUMPTION


def test_confidence_score_penalizes_contradictions() -> None:
    low = ConfidenceBreakdown(
        source_quality=0.8,
        independent_confirmations=0.8,
        compute_check=0.8,
        contradictions=0.9,
        assumption_quality=0.8,
    )
    high = ConfidenceBreakdown(
        source_quality=0.8,
        independent_confirmations=0.8,
        compute_check=0.8,
        contradictions=0.0,
        assumption_quality=0.8,
    )
    assert low.score() < high.score()
