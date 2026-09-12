"""Schema drift from real LLM providers must not break Claim / MathCheckRequest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_lab.agents.base import coerce_str_list
from ai_lab.checks import run_deterministic_checks
from ai_lab.checks.math_check import normalize_math_check_payload
from ai_lab.core.enums import EvidenceKind
from ai_lab.core.models import BlindClaimView, Claim, MathCheckRequest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm_schema_drift"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_coerce_str_list_from_chief_unknown_objects() -> None:
    payload = _load("chief_unknowns_objects.json")
    assumptions = coerce_str_list(payload["unknowns"], limit=5)
    assert assumptions == [
        "Потери сверху трактуются как запас по мощности.",
        "Теплоёмкость воды при 20 C, погрешность <1%.",
    ]
    claim = Claim(
        statement=str(payload["understanding"]),
        kind=EvidenceKind.INFERENCE,
        assumptions=assumptions,
    )
    assert all(isinstance(a, str) for a in claim.assumptions)


def test_coerce_str_list_accepts_plain_strings() -> None:
    assert coerce_str_list(["a", "b"]) == ["a", "b"]
    assert coerce_str_list("solo") == ["solo"]
    assert coerce_str_list(None) == []


def test_normalize_math_check_discards_bare_units_string() -> None:
    raw = _load("math_check_units_string.json")
    normalized = normalize_math_check_payload(raw)
    assert normalized is not None
    assert normalized["units"] == {}
    assert normalized["expected"] == 127.32
    assert normalized["tolerance"] == 0.001
    req = MathCheckRequest.model_validate(normalized)
    assert req.units == {}
    assert req.required_units["force_n"] == "N"


def test_normalize_math_check_coerces_inputs_list() -> None:
    raw = _load("math_check_inputs_list.json")
    normalized = normalize_math_check_payload(raw)
    assert normalized is not None
    assert normalized["inputs"] == {"m": 10.0, "cp": 4184.0, "dT": 10.0}
    req = MathCheckRequest.model_validate(normalized)
    assert req.inputs["cp"] == 4184.0


@pytest.mark.asyncio
async def test_deterministic_checks_accept_drifted_math_check() -> None:
    """Regression: bare units string must not crash deterministic_verify."""
    raw = _load("math_check_units_string.json")
    # After normalize at claim-build time, units are {}. required_units remain → unit FAIL
    # is OK; crash is not.
    normalized = normalize_math_check_payload(raw)
    claim = BlindClaimView(
        claim_id="claim_drift",
        statement="stress",
        kind=EvidenceKind.CALCULATION,
        math_check=normalized,
    )
    report = await run_deterministic_checks([claim])
    assert report.results
    # Missing units for required keys → FAIL, not ValidationError
    assert report.results[0].passed is False
    assert report.critical_failures
