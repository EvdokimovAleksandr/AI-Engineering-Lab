"""Generic deterministic benchmark acceptance — not benchmark-name hardcoding.

Engineering PASS for a registered benchmark additionally requires this contract
when ``BenchmarkExpectation`` declares acceptance outputs/inputs.
"""

from __future__ import annotations

from ai_lab.benchmark.models import (
    AcceptanceReport,
    BenchmarkExpectation,
    InputAcceptance,
    OutputAcceptance,
)
from ai_lab.checks.units import (
    IncompatibleDimensionsError,
    UnitError,
    convert_magnitude,
    units_compatible,
)
from ai_lab.core.models import Claim, ComputationArtifact, DeterministicCheckReport, Quantity
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


def output_alias_map(expectation: BenchmarkExpectation) -> dict[str, list[str]]:
    """Map canonical output_id → aliases for coverage / policy checks."""
    out: dict[str, list[str]] = {}
    for rule in expectation.acceptance_outputs:
        out[rule.output_id] = list(rule.aliases)
        for alias in rule.aliases:
            out.setdefault(alias, [])
            if rule.output_id not in out[alias]:
                out[alias].append(rule.output_id)
    return out


def _names_for(rule: OutputAcceptance) -> list[str]:
    return [rule.output_id, *rule.aliases]


def _coerce_quantity(value: object) -> Quantity | None:
    if isinstance(value, Quantity):
        return value
    if isinstance(value, dict) and "value" in value:
        try:
            return Quantity(value=float(value["value"]), unit=str(value.get("unit") or ""))
        except (TypeError, ValueError):
            return None
    try:
        return Quantity(value=float(value), unit="")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _find_declared(
    computations: list[ComputationArtifact],
    names: list[str],
) -> tuple[ComputationArtifact | None, str | None, Quantity | None]:
    for comp in computations:
        declared = comp.declared_outputs or {}
        for name in names:
            if name not in declared:
                continue
            q = _coerce_quantity(declared[name])
            if q is not None:
                return comp, name, q
    return None, None, None


def _math_check_inputs(claims: list[Claim]) -> dict[str, float]:
    """Collect numeric inputs declared on quantitative claims' math_check."""
    found: dict[str, float] = {}
    for claim in claims:
        mc = claim.math_check
        if not isinstance(mc, dict):
            continue
        inputs = mc.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key, val in inputs.items():
            try:
                found[str(key)] = float(val)
            except (TypeError, ValueError):
                continue
    return found


def _check_input_rule(rule: InputAcceptance, observed: dict[str, float]) -> str | None:
    names = [rule.name, *rule.aliases]
    value = None
    matched = None
    for n in names:
        if n in observed:
            value = observed[n]
            matched = n
            break
    if value is None:
        if rule.required:
            return f"required acceptance input {rule.name!r} not found in math_check.inputs"
        return None
    abs_tol = rule.absolute_tolerance
    if abs_tol is None:
        abs_tol = abs(rule.value) * float(rule.relative_tolerance)
    if abs(value - rule.value) > abs_tol + 1e-12:
        return (
            f"acceptance input {matched!r}={value} outside tolerance of "
            f"{rule.value} (±{abs_tol})"
        )
    return None


def evaluate_acceptance(
    expectation: BenchmarkExpectation,
    *,
    computations: list[ComputationArtifact],
    claims: list[Claim] | None = None,
    check_report: DeterministicCheckReport | None = None,
) -> AcceptanceReport:
    """Deterministic oracle over declared outputs + optional input contract.

    Does not invent physics — only enforces what the expectation declares.
    """
    claims = claims or []
    reasons: list[str] = []
    covered: list[str] = []

    if not expectation.acceptance_outputs and not expectation.acceptance_inputs:
        return AcceptanceReport(
            passed=True, reasons=["no acceptance contract"], covered_outputs=[]
        )

    for rule in expectation.acceptance_outputs:
        names = _names_for(rule)
        comp, matched_name, quantity = _find_declared(computations, names)
        if comp is None or quantity is None or matched_name is None:
            reasons.append(
                f"acceptance output {rule.output_id!r} missing from declared_outputs "
                f"(aliases={rule.aliases})"
            )
            continue
        actual_unit = (quantity.unit or "").strip()
        if not units_compatible(rule.dimension_unit, actual_unit):
            reasons.append(
                f"acceptance output {rule.output_id!r} dimension mismatch: "
                f"need {rule.dimension_unit!r}, got {actual_unit!r}"
            )
            continue
        if rule.min_value is not None or rule.max_value is not None:
            band_unit = (rule.band_unit or rule.dimension_unit).strip()
            try:
                mag = convert_magnitude(float(quantity.value), actual_unit, band_unit)
            except (UnitError, IncompatibleDimensionsError) as exc:
                reasons.append(
                    f"acceptance output {rule.output_id!r} unit convert failed: {exc}"
                )
                continue
            if rule.min_value is not None and mag < rule.min_value:
                reasons.append(
                    f"acceptance output {rule.output_id!r}={mag} {band_unit} "
                    f"< min {rule.min_value}"
                )
                continue
            if rule.max_value is not None and mag > rule.max_value:
                reasons.append(
                    f"acceptance output {rule.output_id!r}={mag} {band_unit} "
                    f"> max {rule.max_value}"
                )
                continue
        covered.append(rule.output_id)

    observed_inputs = _math_check_inputs(claims)
    for comp in computations:
        meta = comp.metadata if isinstance(comp.metadata, dict) else {}
        raw_inputs = meta.get("inputs")
        if isinstance(raw_inputs, dict):
            for key, val in raw_inputs.items():
                if val is None:
                    continue
                try:
                    observed_inputs.setdefault(str(key), float(val))
                except (TypeError, ValueError):
                    continue

    for rule in expectation.acceptance_inputs:
        err = _check_input_rule(rule, observed_inputs)
        if err:
            reasons.append(err)

    passed = not reasons and (
        not expectation.acceptance_outputs
        or len(covered) == len(expectation.acceptance_outputs)
    )
    if not passed and not reasons:
        reasons.append("acceptance contract not satisfied")
    if not passed:
        logger.error("Benchmark acceptance failed: %s", reasons)
    return AcceptanceReport(
        passed=passed,
        reasons=reasons,
        covered_outputs=covered,
        details={"check_report_present": check_report is not None},
    )
