"""PR-B Method/Domain Compatibility Gate — adversarial unit/integration tests."""

from __future__ import annotations

from ai_lab.checks.calculation_contract import evaluate_evidence_completeness
from ai_lab.checks.method_compatibility import (
    CODE_DOMAIN_MISMATCH,
    CODE_METHOD_MISMATCH,
    DOMAIN_BIOMATERIALS_FIBER,
    DOMAIN_MECHANICAL_ROD,
    DOMAIN_MECHANICAL_SHAFT,
    DOMAIN_THERMAL_HEATING,
    METHOD_FIBER_STRESS,
    METHOD_HEATER_POWER,
    METHOD_SHAFT_MECHANICS,
    ProblemEngineeringFrame,
    check_method_compatibility,
    classify_calculation_method,
    infer_problem_domain,
)
from ai_lab.core.enums import AdjudicationStatus
from ai_lab.core.models import (
    CalculationSpec,
    DeterministicCheckReport,
    MathCheckResult,
    VerificationPolicy,
)
from ai_lab.orchestrator.adjudication import adjudicate


def _fiber_spec(**overrides) -> CalculationSpec:
    data = {
        "objective": "calculate_fiber_stress",
        "required_inputs": ["force_n", "diameter_m"],
        "required_outputs": ["stress_gpa"],
        "expected_dimensions": {"stress_gpa": "GPa"},
        "domain": "mechanics",
        "task_id": "calculation",
    }
    data.update(overrides)
    return CalculationSpec.model_validate(data)


def _heater_spec(**overrides) -> CalculationSpec:
    data = {
        "objective": "calculate_heater_power",
        "required_inputs": [
            "water_volume_l",
            "initial_temperature_c",
            "target_temperature_c",
            "heating_time_s",
            "loss_fraction",
        ],
        "required_outputs": ["power"],
        "expected_dimensions": {"power": "W"},
        "domain": "thermal_heating",
        "task_id": "calculation",
    }
    data.update(overrides)
    return CalculationSpec.model_validate(data)


def _shaft_spec(**overrides) -> CalculationSpec:
    data = {
        "objective": "calculate_shaft_diameter",
        "required_inputs": ["power_w", "rpm", "allowable_tau", "safety_factor"],
        "required_outputs": ["diameter_m"],
        "expected_dimensions": {"diameter_m": "m"},
        "domain": "mechanical_shaft",
        "task_id": "calculation",
    }
    data.update(overrides)
    return CalculationSpec.model_validate(data)


def _stress_on_heater_spec() -> CalculationSpec:
    """Heater problem solved as mechanical stress — DOMAIN_MISMATCH."""
    return CalculationSpec.model_validate(
        {
            "objective": "calculate_bending_stress",
            "required_inputs": ["force", "diameter"],
            "required_outputs": ["stress"],
            "expected_dimensions": {"stress": "Pa"},
            "domain": "mechanics",
            "task_id": "calculation",
        }
    )


def _passed_checks() -> DeterministicCheckReport:
    return DeterministicCheckReport(
        results=[
            MathCheckResult(
                check_id="c1",
                expression="1",
                expected=1.0,
                actual=1.0,
                passed=True,
                discrepancy="",
            )
        ],
        verification_results=[],
        critical_failures=[],
    )


def test_infer_shaft_domain_from_problem_text() -> None:
    domain = infer_problem_domain(
        declared_domain=None,
        objective="Shaft preliminary diameter",
        original_problem=(
            "Определить предварительный минимальный диаметр стального круглого вала "
            "для передачи 10 kW при 1500 rpm"
        ),
    )
    assert domain == DOMAIN_MECHANICAL_SHAFT


def test_infer_rod_domain_from_problem_text() -> None:
    domain = infer_problem_domain(
        declared_domain="mechanics",
        objective="Propose how to strengthen the rod under axial loading",
        original_problem="Как сделать стержень прочнее?",
    )
    assert domain == DOMAIN_MECHANICAL_ROD


def test_shaft_plus_fiber_calculation_fails() -> None:
    frame = ProblemEngineeringFrame(
        domain=DOMAIN_MECHANICAL_SHAFT,
        objective="Shaft preliminary diameter",
        original_problem="круглого вала 10 kW 1500 rpm",
    )
    result = check_method_compatibility(_fiber_spec(), frame)
    assert result.compatible is False
    assert classify_calculation_method(_fiber_spec()) == METHOD_FIBER_STRESS
    assert CODE_METHOD_MISMATCH in result.codes or CODE_DOMAIN_MISMATCH in result.codes


def test_rod_plus_fiber_calculation_fails() -> None:
    frame = ProblemEngineeringFrame(
        domain=DOMAIN_MECHANICAL_ROD,
        objective="Strengthen rod under axial load",
        original_problem="Как сделать стержень прочнее?",
    )
    result = check_method_compatibility(_fiber_spec(), frame)
    assert result.compatible is False
    assert any(c in result.codes for c in (CODE_METHOD_MISMATCH, CODE_DOMAIN_MISMATCH))


def test_heater_plus_stress_calculation_fails() -> None:
    frame = ProblemEngineeringFrame(
        domain=DOMAIN_THERMAL_HEATING,
        objective="Calculate required heater power",
        original_problem="Нагреть 20 L воды от 20 до 80 C за 30 минут",
        required_outputs=["power"],
    )
    result = check_method_compatibility(_stress_on_heater_spec(), frame)
    assert result.compatible is False
    assert CODE_DOMAIN_MISMATCH in result.codes or CODE_METHOD_MISMATCH in result.codes


def test_valid_shaft_calculation_allowed() -> None:
    frame = ProblemEngineeringFrame(
        domain=DOMAIN_MECHANICAL_SHAFT,
        objective="Shaft preliminary diameter",
        original_problem="круглого вала torsional",
    )
    result = check_method_compatibility(_shaft_spec(), frame)
    assert result.compatible is True
    assert classify_calculation_method(_shaft_spec()) == METHOD_SHAFT_MECHANICS
    assert result.codes == []


def test_valid_heater_calculation_allowed() -> None:
    frame = ProblemEngineeringFrame(
        domain=DOMAIN_THERMAL_HEATING,
        objective="Calculate required heater power",
        original_problem="heater water volume 20 L",
        required_outputs=["power"],
    )
    result = check_method_compatibility(_heater_spec(), frame)
    assert result.compatible is True
    assert classify_calculation_method(_heater_spec()) == METHOD_HEATER_POWER


def test_biomaterials_fiber_method_allowed() -> None:
    frame = ProblemEngineeringFrame(
        domain=DOMAIN_BIOMATERIALS_FIBER,
        objective="Assess industrial spider-silk production",
        original_problem="spider silk fiber spinning",
    )
    result = check_method_compatibility(_fiber_spec(), frame)
    assert result.compatible is True


def test_completeness_and_adjudication_block_pass_on_domain_mismatch() -> None:
    """Fiber CalculationSpec на shaft frame → method_compatible=False → не PASS."""
    completeness = evaluate_evidence_completeness(
        calculation_specs=[_fiber_spec()],
        computations=[],
        check_report=_passed_checks(),
        claims=[],
        verification_policy=VerificationPolicy(
            verification_required=True,
            calculation_required=True,
            minimum_checks=1,
        ),
        require_calculation=True,
        original_problem=(
            "Определить предварительный минимальный диаметр стального круглого вала "
            "для передачи 10 kW при 1500 rpm"
        ),
        problem_objective="Shaft preliminary diameter",
        declared_domain=None,
    )
    assert completeness.method_compatible is False
    assert completeness.is_complete is False
    assert any("MISMATCH" in r for r in completeness.reasons)

    adj = adjudicate(
        check_report=_passed_checks(),
        verification=None,
        red_team=None,
        require_independent_review=False,
        require_red_team=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.engineering_outcome != AdjudicationStatus.PASS
    assert adj.status != AdjudicationStatus.PASS


def test_heater_fixture_path_still_method_compatible() -> None:
    completeness = evaluate_evidence_completeness(
        calculation_specs=[_heater_spec()],
        computations=[],
        check_report=_passed_checks(),
        claims=[],
        verification_policy=VerificationPolicy(
            verification_required=True,
            calculation_required=True,
            minimum_checks=1,
            required_outputs=["power"],
        ),
        require_calculation=True,
        original_problem="Нагреть 20 L воды heater power",
        problem_objective="Calculate required heater power",
        declared_domain="thermal",
        required_contract_outputs=["power"],
    )
    # Нет computation → incomplete, но method_compatible должен быть True.
    assert completeness.method_compatible is True
    assert not any("MISMATCH" in r for r in completeness.reasons)
