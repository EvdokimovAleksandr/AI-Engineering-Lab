"""PR-02: typed Dimension model vs Pint units / legacy SI letters."""

from __future__ import annotations

from ai_lab.checks.calculation_contract import (
    validate_calculation_spec,
    validate_computation_against_spec,
)
from ai_lab.checks.units import (
    Dimension,
    dimension_quotient,
    dimensions_equivalent,
    is_parseable_unit,
    is_valid_expected_dimension,
    parse_quantity,
    resolve_dimension_token,
    unit_matches_dimension,
    units_compatible,
)
from ai_lab.core.models import CalculationSpec, ComputationArtifact, Quantity


def test_length_not_volume() -> None:
    assert not dimensions_equivalent(Dimension.LENGTH, Dimension.VOLUME)
    assert not units_compatible(Dimension.LENGTH.value, "m**3")
    assert not units_compatible(Dimension.LENGTH.value, "L")  # Pint litre = volume
    assert units_compatible(Dimension.VOLUME.value, "m**3")
    assert units_compatible(Dimension.VOLUME.value, "liter")


def test_area_not_length() -> None:
    assert not dimensions_equivalent(Dimension.AREA, Dimension.LENGTH)
    assert not units_compatible(Dimension.AREA.value, "m")
    assert units_compatible(Dimension.AREA.value, "m**2")
    assert units_compatible(Dimension.LENGTH.value, "m")


def test_stress_is_force_over_area() -> None:
    """Stress ≡ pressure ≡ force / area; Pa compatible with PRESSURE."""
    derived = dimension_quotient(Dimension.FORCE, Dimension.AREA)
    assert derived is Dimension.PRESSURE
    assert unit_matches_dimension("Pa", Dimension.PRESSURE)
    assert unit_matches_dimension("MPa", Dimension.PRESSURE)
    assert units_compatible(Dimension.PRESSURE.value, "Pa")
    # Pint algebra: N/m**2 ≡ Pa
    assert units_compatible("N/m**2", "Pa")


def test_power_is_energy_over_time() -> None:
    derived = dimension_quotient(Dimension.ENERGY, Dimension.TIME)
    assert derived is Dimension.POWER
    assert units_compatible(Dimension.POWER.value, "W")
    assert units_compatible("J/s", "W")


def test_regression_length_dimension_accepts_meter() -> None:
    """Chief-style length / legacy L must match actual m (sofa/rod false FAIL)."""
    assert units_compatible("length", "m")
    assert units_compatible("length", "mm")
    assert units_compatible(Dimension.LENGTH.value, "m")
    assert units_compatible("L", "m")
    assert units_compatible("L", "cm")
    # Litre as *actual* unit is volume — not compatible with length expectation.
    assert not units_compatible("length", "liter")
    assert not units_compatible("L", "liter")


def test_regression_area_legacy_l2_accepts_m2() -> None:
    assert units_compatible("area", "m**2")
    assert units_compatible("L**2", "m**2")
    assert units_compatible("L^2", "mm**2")
    # Actual litre / litre² must not satisfy AREA (no legacy remap on actual side).
    assert not units_compatible(Dimension.AREA.value, "L")
    assert not units_compatible(Dimension.AREA.value, "liter")
    assert not units_compatible(Dimension.AREA.value, "m")


def test_legacy_si_resolution_documented() -> None:
    assert resolve_dimension_token("L") is Dimension.LENGTH
    assert resolve_dimension_token("L**2") is Dimension.AREA
    assert resolve_dimension_token("L**3") is Dimension.VOLUME
    assert resolve_dimension_token("M") is Dimension.MASS
    assert resolve_dimension_token("T") is Dimension.TIME
    assert resolve_dimension_token("length") is Dimension.LENGTH
    # Actual-side: legacy SI disabled — L is not a Dimension token.
    assert resolve_dimension_token("L", allow_legacy_si=False) is None
    # Real units are not dimension tokens.
    assert resolve_dimension_token("m") is None
    assert resolve_dimension_token("liter") is None


def test_pint_still_treats_bare_l_as_litre_when_parsing() -> None:
    """parse_quantity must not remap L→length (fail-loud litre semantics)."""
    q = parse_quantity(1.0, "L")
    assert "liter" in str(q.units) or "litre" in str(q.units) or str(q.units) in {"L", "l"}
    assert unit_matches_dimension("L", Dimension.VOLUME)
    assert not unit_matches_dimension("L", Dimension.LENGTH)
    assert is_parseable_unit("L")  # litre remains a real Pint unit
    assert not is_parseable_unit("length")  # dimension name ≠ unit
    assert is_valid_expected_dimension("length")
    assert is_valid_expected_dimension("L")
    assert is_valid_expected_dimension("L**2")
    assert is_valid_expected_dimension("m")
    assert not is_valid_expected_dimension("force/area somehow")


def test_contract_relevance_legacy_l_vs_m() -> None:
    """CalculationSpec expected L / L**2 + declared m / m**2 → relevant."""
    spec = CalculationSpec(
        spec_id="cspec_dim",
        task_id="t1",
        run_id="r1",
        project_id="p1",
        objective="rod area and length",
        required_outputs=["A_m2", "d_m"],
        expected_dimensions={"A_m2": "L**2", "d_m": "L"},
    )
    assert validate_calculation_spec(spec) == []
    art = ComputationArtifact(
        artifact_id="comp_dim",
        task_id="t1",
        run_id="r1",
        project_id="p1",
        calculation_spec_id="cspec_dim",
        code="pass",
        stdout="",
        stderr="",
        returncode=0,
        status="ok",
        declared_outputs={
            "A_m2": Quantity(value=0.000491, unit="m**2"),
            "d_m": Quantity(value=0.025, unit="m"),
        },
    )
    rel = validate_computation_against_spec(art, spec)
    assert rel.relevant is True
    assert rel.dimension_mismatches == []


def test_contract_rejects_length_vs_volume_unit() -> None:
    spec = CalculationSpec(
        spec_id="cspec_bad",
        task_id="t1",
        run_id="r1",
        project_id="p1",
        objective="length vs litre trap",
        required_outputs=["width"],
        expected_dimensions={"width": "length"},
    )
    art = ComputationArtifact(
        artifact_id="comp_bad",
        task_id="t1",
        run_id="r1",
        project_id="p1",
        calculation_spec_id="cspec_bad",
        code="pass",
        stdout="",
        stderr="",
        returncode=0,
        status="ok",
        declared_outputs={"width": Quantity(value=2.0, unit="L")},  # litre
    )
    rel = validate_computation_against_spec(art, spec)
    assert rel.relevant is False
    assert rel.dimension_mismatches
