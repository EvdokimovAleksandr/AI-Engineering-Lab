"""Uniaxial tension algebraic solver. In-process; no arbitrary Python, no Docker."""

from __future__ import annotations

from typing import Any

from ai_lab.checks.safe_eval import ForbiddenExpressionError, SafeExpressionEvaluator
from ai_lab.checks.units import (
    IncompatibleDimensionsError,
    UnitError,
    convert_to,
    from_pint,
    parse_quantity,
    to_pint,
    to_si,
)
from ai_lab.core.enums import (
    EvidenceConfidence,
    ModelValidity,
    NumericalCorrectness,
    ParameterTrust,
    PhysicalValidity,
    SimulationStatus,
)
from ai_lab.core.models import Quantity
from ai_lab.observability.logger import get_logger
from ai_lab.simulation.models import (
    ConvergenceMetadata,
    ScientificStatus,
    SimulationCheck,
    SimulationResult,
    SimulationSpec,
)
from ai_lab.simulation.protocol import SolverContext
from ai_lab.simulation.validate import validate_simulation_spec

logger = get_logger(__name__)

SOLVER_ID = "uniaxial_tension"
SOLVER_VERSION = "uniaxial-tension-v1"

# Physics the solver actually uses. Spec equations are evaluated and compared; they
# cannot silently replace this model.
REQUIRED_PARAMETERS = (
    "diameter",
    "length",
    "force",
    "extension",
    "youngs_modulus",
    "tensile_strength",
    "density",
)
REQUIRED_ASSUMPTIONS = frozenset(
    {
        "linear_elasticity",
        "circular_cross_section",
        "small_strain",
        "isotropic",
        "uniaxial",
    }
)
CANONICAL_EQUATIONS = {
    "area": "pi * diameter ** 2 / 4",
    "stress": "force / area",
    "strain": "extension / length",
    "elastic_prediction": "youngs_modulus * strain",
    "failure_margin": "tensile_strength / stress",
    "mass": "density * area * length",
}
# Closed forms for DeterministicVerifier (inputs are parameters only).
CLOSED_FORM_EQUATIONS = {
    "area": "pi * diameter ** 2 / 4",
    "stress": "force / (pi * diameter ** 2 / 4)",
    "strain": "extension / length",
    "elastic_prediction": "youngs_modulus * (extension / length)",
    "failure_margin": "tensile_strength / (force / (pi * diameter ** 2 / 4))",
    "mass": "density * (pi * diameter ** 2 / 4) * length",
}
SI_UNITS = {
    "area": "m**2",
    "stress": "Pa",
    "strain": "",
    "elastic_prediction": "Pa",
    "failure_margin": "",
    "mass": "kg",
    "force": "N",
    "length": "m",
    "diameter": "m",
    "youngs_modulus": "Pa",
    "density": "kg/m**3",
    "tensile_strength": "Pa",
    "extension": "m",
}
DEFAULT_CONSTRAINTS = {
    "diameter": "positive",
    "length": "positive",
    "force": "nonnegative",
    "extension": "nonnegative",
    "youngs_modulus": "positive",
    "tensile_strength": "positive",
    "density": "positive",
}
DEFAULT_DIMENSIONS = {
    "diameter": "m",
    "length": "m",
    "force": "N",
    "extension": "m",
    "youngs_modulus": "Pa",
    "tensile_strength": "Pa",
    "density": "kg/m**3",
}
# Small-strain domain: |ε| above this with small_strain assumption → OUT_OF_DOMAIN.
SMALL_STRAIN_LIMIT = 0.05


class UniaxialTensionSolver:
    """σ = F/A, ε = ΔL/L, σ_elastic = Eε. Discrepancies are recorded, never hidden."""

    solver_id = SOLVER_ID

    def solve(self, spec: SimulationSpec, context: SolverContext) -> SimulationResult:
        if spec.solver.solver_id != SOLVER_ID:
            logger.error("UniaxialTensionSolver got solver_id=%s", spec.solver.solver_id)
            return _error_result(
                spec,
                SimulationStatus.SOLVER_ERROR,
                [f"solver_id must be {SOLVER_ID!r}"],
            )
        filled = _apply_uniaxial_defaults(spec)
        validation = validate_simulation_spec(filled)
        if not validation.ok:
            return _error_result(filled, validation.status, validation.errors)

        missing_params = [p for p in REQUIRED_PARAMETERS if p not in filled.parameters]
        if missing_params:
            return _error_result(
                filled,
                SimulationStatus.INVALID_PARAMETERS,
                [f"missing required parameters: {missing_params}"],
            )
        present_assumptions = {a.id for a in filled.assumptions}
        missing_assumptions = sorted(REQUIRED_ASSUMPTIONS - present_assumptions)
        if missing_assumptions:
            return _error_result(
                filled,
                SimulationStatus.INVALID_MODEL,
                [
                    "explicit assumptions required (solver will not silently assume a material model): "
                    + ", ".join(missing_assumptions)
                ],
            )

        try:
            env = {name: to_pint(qty) for name, qty in filled.parameters.items()}
        except UnitError as exc:
            return _error_result(filled, SimulationStatus.INVALID_PARAMETERS, [str(exc)])

        evaluator = SafeExpressionEvaluator(max_ast_nodes=200, max_expression_chars=2000)
        checks: list[SimulationCheck] = []
        diagnostics: list[str] = []
        try:
            canonical = _eval_canonical(env, evaluator)
        except ZeroDivisionError as exc:
            logger.error("Uniaxial tension division by zero: %s", exc)
            return _error_result(
                filled, SimulationStatus.COMPUTATION_ERROR, [f"Division by zero: {exc}"]
            )
        except Exception as exc:
            logger.error("Uniaxial tension computation failed: %s", exc)
            return _error_result(filled, SimulationStatus.COMPUTATION_ERROR, [str(exc)])

        # Mutual check: spec equations vs canonical physics.
        eq_env = dict(env)
        for eq in filled.equations:
            try:
                value = evaluator.evaluate(eq.expression, eq_env)
                eq_env[eq.name] = value
                if eq.name in canonical:
                    ok, msg = _quantities_close(
                        value,
                        canonical[eq.name],
                        filled,
                    )
                    checks.append(
                        SimulationCheck(
                            name=f"equation:{eq.name}",
                            passed=ok,
                            message=msg,
                            layer="numerical",
                        )
                    )
                    if not ok:
                        diagnostics.append(msg)
            except ForbiddenExpressionError as exc:
                return _error_result(filled, SimulationStatus.INVALID_MODEL, [str(exc)])
            except IncompatibleDimensionsError as exc:
                return _error_result(filled, SimulationStatus.INVALID_MODEL, [str(exc)])
            except Exception as exc:
                logger.error("Equation %s failed: %s", eq.name, exc)
                return _error_result(filled, SimulationStatus.COMPUTATION_ERROR, [str(exc)])

        outputs = {name: _to_si_quantity(pq) for name, pq in canonical.items()}
        checks.extend(_sanity_checks(canonical, filled))
        elastic_ok, elastic_msg = _quantities_close(
            canonical["stress"],
            canonical["elastic_prediction"],
            filled,
        )
        checks.append(
            SimulationCheck(
                name="stress_vs_E_strain",
                passed=elastic_ok,
                message=elastic_msg,
                layer="numerical",
            )
        )
        if not elastic_ok:
            diagnostics.append(elastic_msg)

        strain_mag = abs(float(canonical["strain"].to_base_units().magnitude))
        domain_ok = strain_mag <= SMALL_STRAIN_LIMIT
        if "small_strain" in present_assumptions and not domain_ok:
            status = SimulationStatus.OUT_OF_DOMAIN
            checks.append(
                SimulationCheck(
                    name="small_strain_domain",
                    passed=False,
                    message=f"|strain|={strain_mag} exceeds small-strain limit {SMALL_STRAIN_LIMIT}",
                    layer="model_sanity",
                )
            )
        else:
            status = SimulationStatus.SUCCESS
            checks.append(
                SimulationCheck(
                    name="small_strain_domain",
                    passed=True,
                    message=f"|strain|={strain_mag} within small-strain limit {SMALL_STRAIN_LIMIT}",
                    layer="model_sanity",
                )
            )

        fail_if_above = bool(filled.metadata.get("fail_if_above_strength", False))
        stress_si = float(canonical["stress"].to("Pa").magnitude)
        strength_si = float(canonical["tensile_strength"].to("Pa").magnitude) if "tensile_strength" in canonical else float(env["tensile_strength"].to("Pa").magnitude)
        below_strength = stress_si <= strength_si + 1e-18
        checks.append(
            SimulationCheck(
                name="failure_condition",
                passed=below_strength,
                message=(
                    "stress <= tensile_strength"
                    if below_strength
                    else f"stress {stress_si} Pa exceeds tensile_strength {strength_si} Pa"
                ),
                layer="model_sanity",
            )
        )
        if fail_if_above and not below_strength and status == SimulationStatus.SUCCESS:
            # Configurable: treat overload as out-of-domain, not as "hypothesis is false".
            status = SimulationStatus.OUT_OF_DOMAIN

        numerical_ok = all(c.passed for c in checks if c.layer == "numerical")
        model_ok = all(c.passed for c in checks if c.layer == "model_sanity") or status == SimulationStatus.SUCCESS
        evidence = _evidence_from_provenance(filled)
        scientific = ScientificStatus(
            model_validity=ModelValidity.VALID,
            numerical_correctness=(
                NumericalCorrectness.CORRECT if numerical_ok else NumericalCorrectness.INCORRECT
            ),
            physical_validity=(
                PhysicalValidity.OUT_OF_DOMAIN
                if status == SimulationStatus.OUT_OF_DOMAIN
                else PhysicalValidity.IN_DOMAIN
                if model_ok
                else PhysicalValidity.UNASSESSED
            ),
            evidence_confidence=evidence,
        )
        units = {name: qty.unit for name, qty in outputs.items()}
        return SimulationResult(
            spec_id=filled.id,
            status=status,
            outputs=outputs,
            units=units,
            solver=SOLVER_ID,
            numerical_metadata=ConvergenceMetadata(
                converged=True,
                iterations=1,
                residual=0.0,
                tolerance=_relative_tol(filled),
            ),
            assumptions=list(filled.assumptions),
            checks=checks,
            provenance={
                "solver_id": SOLVER_ID,
                "solver_version": SOLVER_VERSION,
                "run_id": context.run_id,
                "task_id": context.task_id,
                "parameter_provenance": {
                    k: v.model_dump(mode="json") for k, v in filled.parameter_provenance.items()
                },
            },
            scientific_status=scientific,
            diagnostics=diagnostics,
        )


def _apply_uniaxial_defaults(spec: SimulationSpec) -> SimulationSpec:
    """Fill solver-owned constraints/dimensions. Does not invent missing physics assumptions."""
    constraints = dict(DEFAULT_CONSTRAINTS)
    constraints.update(spec.parameter_constraints)
    dimensions = dict(DEFAULT_DIMENSIONS)
    dimensions.update(spec.expected_dimensions)
    return spec.model_copy(update={"parameter_constraints": constraints, "expected_dimensions": dimensions})


def _eval_canonical(env: dict[str, Any], evaluator: SafeExpressionEvaluator) -> dict[str, Any]:
    names = dict(env)
    names["area"] = evaluator.evaluate(CANONICAL_EQUATIONS["area"], names)
    names["stress"] = evaluator.evaluate(CANONICAL_EQUATIONS["stress"], names)
    names["strain"] = evaluator.evaluate(CANONICAL_EQUATIONS["strain"], names)
    names["elastic_prediction"] = evaluator.evaluate(CANONICAL_EQUATIONS["elastic_prediction"], names)
    names["failure_margin"] = evaluator.evaluate(CANONICAL_EQUATIONS["failure_margin"], names)
    names["mass"] = evaluator.evaluate(CANONICAL_EQUATIONS["mass"], names)
    return {
        "area": names["area"],
        "stress": names["stress"],
        "strain": names["strain"],
        "elastic_prediction": names["elastic_prediction"],
        "failure_margin": names["failure_margin"],
        "mass": names["mass"],
        "force": names["force"],
        "length": names["length"],
        "diameter": names["diameter"],
        "youngs_modulus": names["youngs_modulus"],
        "density": names["density"],
        "tensile_strength": names["tensile_strength"],
        "extension": names["extension"],
    }


def _sanity_checks(canonical: dict[str, Any], spec: SimulationSpec) -> list[SimulationCheck]:
    checks: list[SimulationCheck] = []
    positive = ("area", "length", "diameter", "youngs_modulus", "density", "tensile_strength")
    for name in positive:
        mag = float(canonical[name].to_base_units().magnitude)
        ok = mag > 0
        checks.append(
            SimulationCheck(
                name=f"positive:{name}",
                passed=ok,
                message=f"{name} > 0" if ok else f"{name} is not > 0",
                layer="model_sanity",
            )
        )
    stress = float(canonical["stress"].to("Pa").magnitude)
    checks.append(
        SimulationCheck(
            name="stress_nonnegative",
            passed=stress >= 0,
            message="stress >= 0" if stress >= 0 else "stress is negative",
            layer="model_sanity",
        )
    )
    margin = float(canonical["failure_margin"].to_base_units().magnitude)
    checks.append(
        SimulationCheck(
            name="failure_margin_nonnegative",
            passed=margin >= 0,
            message="failure_margin >= 0" if margin >= 0 else "failure_margin is negative",
            layer="model_sanity",
        )
    )
    return checks


def _quantities_close(actual: Any, expected: Any, spec: SimulationSpec) -> tuple[bool, str]:
    """Reuse verifier policy: |Δ| <= atol + rtol*|expected| after unit conversion."""
    try:
        aligned = convert_to(actual if hasattr(actual, "units") else parse_quantity(float(actual), ""), expected)
    except IncompatibleDimensionsError as exc:
        return False, f"incompatible dimensions: {exc}"
    atol = 0.0
    ns = spec.numerical_settings
    if ns.absolute_tolerance is not None:
        try:
            atol = float(convert_to(to_pint(ns.absolute_tolerance), expected).magnitude)
        except (UnitError, IncompatibleDimensionsError):
            atol = 0.0
    rtol = float(ns.relative_tolerance or 0.0)
    delta = abs(float(aligned.magnitude) - float(expected.magnitude))
    threshold = atol + rtol * abs(float(expected.magnitude))
    if delta <= threshold:
        return True, f"delta={delta} <= threshold={threshold}"
    return False, f"delta={delta} > threshold={threshold} (atol={atol}, rtol={rtol})"


def _to_si_quantity(pq: Any) -> Quantity:
    return to_si(pq)


def _relative_tol(spec: SimulationSpec) -> float:
    return float(spec.numerical_settings.relative_tolerance or 0.0)


def _evidence_from_provenance(spec: SimulationSpec) -> EvidenceConfidence:
    trusts = [p.trust for p in spec.parameter_provenance.values()]
    if not trusts:
        return EvidenceConfidence.INPUT_UNVERIFIED
    if any(t == ParameterTrust.STUB for t in trusts):
        return EvidenceConfidence.STUB
    if any(t == ParameterTrust.INPUT_UNVERIFIED for t in trusts):
        return EvidenceConfidence.INPUT_UNVERIFIED
    return EvidenceConfidence.UNASSESSED


def _error_result(spec: SimulationSpec, status: SimulationStatus, errors: list[str]) -> SimulationResult:
    model_validity = (
        ModelValidity.INVALID
        if status in {SimulationStatus.INVALID_MODEL, SimulationStatus.INVALID_PARAMETERS}
        else ModelValidity.UNKNOWN
    )
    return SimulationResult(
        spec_id=spec.id,
        status=status,
        outputs={},
        units={},
        solver=SOLVER_ID,
        numerical_metadata=ConvergenceMetadata(converged=False, iterations=0),
        assumptions=list(spec.assumptions),
        checks=[],
        scientific_status=ScientificStatus(
            model_validity=model_validity,
            numerical_correctness=NumericalCorrectness.NOT_APPLICABLE,
            physical_validity=PhysicalValidity.UNASSESSED,
            evidence_confidence=_evidence_from_provenance(spec),
        ),
        diagnostics=errors,
    )
