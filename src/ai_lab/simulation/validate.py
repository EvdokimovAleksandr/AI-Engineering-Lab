"""Deterministic SimulationSpec validation. Must run before any solver execution."""

from __future__ import annotations

from typing import Any

from ai_lab.checks.safe_eval import ForbiddenExpressionError, SafeExpressionEvaluator
from ai_lab.checks.units import IncompatibleDimensionsError, UnitError, parse_quantity, to_pint
from ai_lab.core.enums import SimulationStatus
from ai_lab.observability.logger import get_logger
from ai_lab.simulation.models import (
    ModelValidationResult,
    SimulationSpec,
)
from ai_lab.simulation.registry import known_solver_ids

logger = get_logger(__name__)

_EVALUATOR = SafeExpressionEvaluator(max_ast_nodes=200, max_expression_chars=2000)


def validate_simulation_spec(
    spec: SimulationSpec,
    *,
    allowed_solvers: frozenset[str] | None = None,
) -> ModelValidationResult:
    """Return ok=False with INVALID_MODEL / INVALID_PARAMETERS; never calls a solver."""
    errors: list[str] = []
    status = SimulationStatus.INVALID_MODEL

    allowed = allowed_solvers if allowed_solvers is not None else known_solver_ids()
    if spec.solver.solver_id not in allowed:
        errors.append(f"solver_id {spec.solver.solver_id!r} is not in the trusted registry")
        return ModelValidationResult(ok=False, status=SimulationStatus.INVALID_MODEL, errors=errors)

    param_errors, param_status = _validate_parameters(spec)
    errors.extend(param_errors)
    if param_errors and param_status == SimulationStatus.INVALID_PARAMETERS:
        status = SimulationStatus.INVALID_PARAMETERS

    errors.extend(_validate_equations(spec))
    errors.extend(_validate_boundary_conditions(spec))
    errors.extend(_validate_numerical_settings(spec))

    if errors:
        logger.error("SimulationSpec %s invalid: %s", spec.id, errors)
        return ModelValidationResult(ok=False, status=status, errors=errors)
    return ModelValidationResult(ok=True, status=SimulationStatus.SUCCESS, errors=[])


def _validate_parameters(spec: SimulationSpec) -> tuple[list[str], SimulationStatus]:
    errors: list[str] = []
    for name, qty in spec.parameters.items():
        try:
            pq = to_pint(qty)
        except UnitError as exc:
            errors.append(f"parameter {name}: {exc}")
            continue
        constraint = spec.parameter_constraints.get(name, "any")
        mag = float(pq.to_base_units().magnitude)
        if constraint == "positive" and mag <= 0:
            errors.append(f"parameter {name} must be > 0 (got {qty.value} {qty.unit})")
        elif constraint == "nonnegative" and mag < 0:
            errors.append(f"parameter {name} must be >= 0 (got {qty.value} {qty.unit})")
        elif constraint == "nonzero" and mag == 0:
            errors.append(f"parameter {name} must be nonzero")
        expected_unit = spec.expected_dimensions.get(name)
        if expected_unit:
            try:
                expected = parse_quantity(1.0, expected_unit)
            except UnitError as exc:
                errors.append(f"parameter {name} expected_dimension invalid: {exc}")
                continue
            if pq.dimensionality != expected.dimensionality:
                errors.append(
                    f"parameter {name}: incompatible dimensions "
                    f"{pq.dimensionality} vs expected {expected.dimensionality}"
                )
    status = SimulationStatus.INVALID_PARAMETERS if errors else SimulationStatus.SUCCESS
    return errors, status


def _validate_equations(spec: SimulationSpec) -> list[str]:
    errors: list[str] = []
    builtins = {"pi", "e", "true", "false", "sqrt", "abs", "min", "max", "round", "log", "exp"}
    known = set(spec.parameters)
    names: dict[str, Any] = {}
    for pname, qty in spec.parameters.items():
        try:
            names[pname] = to_pint(qty)
        except UnitError:
            continue
    for eq in spec.equations:
        if eq.name in spec.parameters:
            errors.append(f"equation {eq.name} collides with a parameter name")
            continue
        referenced = _referenced_names(eq.expression)
        missing = referenced - known - builtins
        if missing:
            errors.append(f"equation {eq.name}: unknown names {sorted(missing)}")
            continue
        try:
            names[eq.name] = _EVALUATOR.evaluate(eq.expression, names)
        except ForbiddenExpressionError as exc:
            errors.append(f"equation {eq.name}: forbidden expression: {exc}")
            continue
        except IncompatibleDimensionsError as exc:
            errors.append(f"equation {eq.name}: incompatible dimensions: {exc}")
            continue
        except ZeroDivisionError:
            errors.append(f"equation {eq.name}: division by zero")
            continue
        except Exception as exc:
            errors.append(f"equation {eq.name}: {exc}")
            continue
        known.add(eq.name)
    return errors


def _referenced_names(expression: str) -> set[str]:
    import ast

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def _validate_boundary_conditions(spec: SimulationSpec) -> list[str]:
    errors: list[str] = []
    seen: dict[str, str] = {}
    for bc in spec.boundary_conditions:
        try:
            to_pint(bc.value)
        except UnitError as exc:
            errors.append(f"boundary condition {bc.name}: {exc}")
        if bc.variable in seen and seen[bc.variable] != bc.name:
            errors.append(
                f"boundary condition conflict on variable {bc.variable!r}: "
                f"{seen[bc.variable]!r} vs {bc.name!r}"
            )
        seen[bc.variable] = bc.name
    return errors


def _validate_numerical_settings(spec: SimulationSpec) -> list[str]:
    errors: list[str] = []
    ns = spec.numerical_settings
    if ns.absolute_tolerance is not None:
        try:
            to_pint(ns.absolute_tolerance)
        except UnitError as exc:
            errors.append(f"numerical_settings.absolute_tolerance: {exc}")
    if ns.relative_tolerance is None and ns.absolute_tolerance is None:
        errors.append("numerical_settings requires absolute_tolerance and/or relative_tolerance")
    return errors
