"""Deterministic engineering verifier: units → compute → tolerance → bounds → sanity.

LLM proposes a VerificationSpec. This module is the only authority for CheckStatus.
Numerical policy is documented on NUMERICAL_POLICY / provenance.numerical_policy.
"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from ai_lab.checks.safe_eval import (
    EvaluationTimeoutError,
    ForbiddenExpressionError,
    SafeExpressionEvaluator,
)
from ai_lab.checks.units import (
    IncompatibleDimensionsError,
    UnitError,
    convert_to,
    from_pint,
    parse_quantity,
    to_pint,
    to_si,
)
from ai_lab.core.enums import CheckStatus
from ai_lab.core.models import (
    CheckStepResult,
    MathCheckRequest,
    Quantity,
    VerificationLimits,
    VerificationProvenance,
    VerificationResult,
    VerificationSpec,
)
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

VERIFIER_VERSION = "deterministic-verifier-v1"

# IEEE-754 float64 after Pint conversion; no implicit epsilon.
NUMERICAL_POLICY = (
    "IEEE-754 float64; Pint converts actual into expected.units; "
    "PASS iff abs(actual-expected) <= absolute + relative*abs(expected); "
    "no implicit epsilon; Python ** is right-associative; "
    "integer |exponent| capped at 1000"
)

# Statuses the VerificationAgent LLM must not rewrite into PASS (or invent as PASS).
def _spec_hash(spec: VerificationSpec) -> str:
    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_pint_quantity(value: Any) -> Any:
    """Wrap a bare float/int as dimensionless so comparison always goes through Pint."""
    if hasattr(value, "units"):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Expression did not produce a numeric quantity, got {type(value).__name__}")
    return parse_quantity(float(value), "")


# Statuses the VerificationAgent LLM must not rewrite into PASS.
NON_OVERRIDABLE_FAIL_STATUSES = frozenset(
    {
        CheckStatus.FAIL,
        CheckStatus.INCOMPATIBLE_DIMENSIONS,
        CheckStatus.OUT_OF_BOUNDS,
        CheckStatus.INVALID_INPUT,
        CheckStatus.EVALUATION_ERROR,
        CheckStatus.TIMEOUT,
    }
)


def limits_from_config(verification_cfg: dict[str, Any] | None) -> VerificationLimits:
    """Read verification.limits from LabConfig.verification (research-limits analogue)."""
    raw = dict(verification_cfg or {})
    limits = dict(raw.get("limits") or {})
    return VerificationLimits(
        max_computation_seconds=float(limits.get("max_computation_seconds", 2.0)),
        max_expression_chars=int(limits.get("max_expression_chars", 2000)),
        max_ast_nodes=int(limits.get("max_ast_nodes", 200)),
        max_quantities=int(limits.get("max_quantities", 32)),
    )


def spec_from_math_check(req: MathCheckRequest) -> VerificationSpec:
    """Adapt legacy MathCheckRequest into a dimensionless VerificationSpec.

    Input unit *tags* stay on metadata; Pint is not applied here so existing
    GPa-as-bare-float checks keep working.
    """
    if req.expected is None:
        raise ValueError("MathCheckRequest.expected is required to build VerificationSpec")
    if not req.expression:
        raise ValueError("MathCheckRequest.expression is required to build VerificationSpec")
    inputs = {name: Quantity(value=float(val), unit="") for name, val in req.inputs.items()}
    return VerificationSpec(
        spec_id=req.check_id,
        claim_id=req.claim_id,
        inputs=inputs,
        expression=req.expression,
        expected=Quantity(value=float(req.expected), unit=""),
        tolerance={"absolute": {"value": float(req.tolerance), "unit": ""}},
        metadata={
            "legacy_math_check": True,
            "units": req.units,
            "required_units": req.required_units,
        },
    )


class DeterministicVerifier:
    """Single in-process verifier. Not a sandbox subprocess and not an LLM."""

    def __init__(self, limits: VerificationLimits | None = None) -> None:
        self.limits = limits or VerificationLimits()

    def verify(self, spec: VerificationSpec) -> VerificationResult:
        """Run the pipeline; identical spec → identical CheckStatus (float64+Pint)."""
        timeout = self.limits.max_computation_seconds
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="det-verify") as pool:
            future = pool.submit(self._verify_sync, spec)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout:
                logger.error(
                    "DeterministicVerifier timed out after %ss spec_id=%s",
                    timeout,
                    spec.spec_id,
                )
                return self._result(
                    spec,
                    CheckStatus.TIMEOUT,
                    diagnostics=[f"Verification exceeded {timeout}s"],
                    checks=[
                        CheckStepResult(
                            name="timeout",
                            passed=False,
                            status=CheckStatus.TIMEOUT,
                            message=f"max_computation_seconds={timeout}",
                        )
                    ],
                )

    def _verify_sync(self, spec: VerificationSpec) -> VerificationResult:
        steps: list[CheckStepResult] = []
        try:
            self._check_limits(spec)
        except ValueError as exc:
            return self._result(
                spec,
                CheckStatus.INVALID_INPUT,
                diagnostics=[str(exc)],
                checks=[
                    CheckStepResult(
                        name="limits",
                        passed=False,
                        status=CheckStatus.INVALID_INPUT,
                        message=str(exc),
                    )
                ],
            )

        deadline = time.monotonic() + self.limits.max_computation_seconds
        evaluator = SafeExpressionEvaluator(
            max_ast_nodes=self.limits.max_ast_nodes,
            max_expression_chars=self.limits.max_expression_chars,
            deadline_monotonic=deadline,
        )

        try:
            env = self._normalize_inputs(spec)
            if spec.actual is not None:
                to_pint(spec.actual)
            steps.append(CheckStepResult(name="normalize", passed=True, message="inputs parsed"))
        except UnitError as exc:
            logger.error("Verification invalid unit spec_id=%s: %s", spec.spec_id, exc)
            return self._result(
                spec,
                CheckStatus.INVALID_INPUT,
                diagnostics=[str(exc)],
                checks=[
                    CheckStepResult(
                        name="normalize",
                        passed=False,
                        status=CheckStatus.INVALID_INPUT,
                        message=str(exc),
                    )
                ],
            )

        try:
            expected_pq = to_pint(spec.expected)
        except UnitError as exc:
            return self._result(
                spec,
                CheckStatus.INVALID_INPUT,
                diagnostics=[f"Invalid expected unit: {exc}"],
                checks=[
                    CheckStepResult(
                        name="normalize",
                        passed=False,
                        status=CheckStatus.INVALID_INPUT,
                        message=str(exc),
                    )
                ],
            )

        try:
            actual_pq = self._compute_actual(spec, env, evaluator)
            steps.append(CheckStepResult(name="compute", passed=True, message="expression evaluated"))
        except ForbiddenExpressionError as exc:
            return self._result(
                spec,
                CheckStatus.INVALID_INPUT,
                diagnostics=[str(exc)],
                checks=steps
                + [
                    CheckStepResult(
                        name="compute",
                        passed=False,
                        status=CheckStatus.INVALID_INPUT,
                        message=str(exc),
                    )
                ],
            )
        except EvaluationTimeoutError as exc:
            return self._result(
                spec,
                CheckStatus.TIMEOUT,
                diagnostics=[str(exc)],
                checks=steps
                + [
                    CheckStepResult(
                        name="compute",
                        passed=False,
                        status=CheckStatus.TIMEOUT,
                        message=str(exc),
                    )
                ],
            )
        except IncompatibleDimensionsError as exc:
            return self._result(
                spec,
                CheckStatus.INCOMPATIBLE_DIMENSIONS,
                diagnostics=[str(exc)],
                checks=steps
                + [
                    CheckStepResult(
                        name="compute",
                        passed=False,
                        status=CheckStatus.INCOMPATIBLE_DIMENSIONS,
                        message=str(exc),
                    )
                ],
                expected=spec.expected,
            )
        except UnitError as exc:
            return self._result(
                spec,
                CheckStatus.INVALID_INPUT,
                diagnostics=[str(exc)],
                checks=steps
                + [
                    CheckStepResult(
                        name="compute",
                        passed=False,
                        status=CheckStatus.INVALID_INPUT,
                        message=str(exc),
                    )
                ],
                expected=spec.expected,
            )
        except ZeroDivisionError as exc:
            logger.error("Verification division by zero spec_id=%s", spec.spec_id)
            return self._result(
                spec,
                CheckStatus.EVALUATION_ERROR,
                diagnostics=[f"Division by zero: {exc}"],
                checks=steps
                + [
                    CheckStepResult(
                        name="compute",
                        passed=False,
                        status=CheckStatus.EVALUATION_ERROR,
                        message="Division by zero",
                    )
                ],
                expected=spec.expected,
            )
        except Exception as exc:
            logger.error("Verification evaluation error spec_id=%s: %s", spec.spec_id, exc)
            return self._result(
                spec,
                CheckStatus.EVALUATION_ERROR,
                diagnostics=[f"Evaluation error: {exc}"],
                checks=steps
                + [
                    CheckStepResult(
                        name="compute",
                        passed=False,
                        status=CheckStatus.EVALUATION_ERROR,
                        message=str(exc),
                    )
                ],
                expected=spec.expected,
            )

        try:
            actual_aligned = convert_to(actual_pq, expected_pq)
        except IncompatibleDimensionsError as exc:
            return self._result(
                spec,
                CheckStatus.INCOMPATIBLE_DIMENSIONS,
                diagnostics=[str(exc)],
                checks=steps
                + [
                    CheckStepResult(
                        name="dimensions",
                        passed=False,
                        status=CheckStatus.INCOMPATIBLE_DIMENSIONS,
                        message=str(exc),
                    )
                ],
                expected=spec.expected,
                actual=from_pint(actual_pq),
                normalized_expected=to_si(expected_pq),
                normalized_actual=to_si(actual_pq),
            )
        steps.append(
            CheckStepResult(name="dimensions", passed=True, message="actual convertible to expected")
        )

        compare_status, compare_msg = self._compare(actual_aligned, expected_pq, spec)
        steps.append(
            CheckStepResult(
                name="tolerance",
                passed=compare_status == CheckStatus.PASS,
                status=compare_status,
                message=compare_msg,
            )
        )
        if compare_status != CheckStatus.PASS:
            return self._result(
                spec,
                compare_status,
                diagnostics=[compare_msg],
                checks=steps,
                expected=spec.expected,
                actual=from_pint(actual_aligned),
                normalized_expected=to_si(expected_pq),
                normalized_actual=to_si(actual_aligned),
            )

        if spec.bounds is not None:
            bound_status, bound_msg = self._check_bounds(actual_aligned, spec)
            steps.append(
                CheckStepResult(
                    name="bounds",
                    passed=bound_status == CheckStatus.PASS,
                    status=bound_status,
                    message=bound_msg,
                )
            )
            if bound_status != CheckStatus.PASS:
                return self._result(
                    spec,
                    bound_status,
                    diagnostics=[bound_msg],
                    checks=steps,
                    expected=spec.expected,
                    actual=from_pint(actual_aligned),
                    normalized_expected=to_si(expected_pq),
                    normalized_actual=to_si(actual_aligned),
                )
        else:
            steps.append(CheckStepResult(name="bounds", passed=True, message="no bounds specified"))

        env_with_result = {
            **env,
            "actual": actual_aligned,
            "expected": expected_pq,
            "result": actual_aligned,
        }
        for sanity in spec.sanity_checks:
            try:
                ok = evaluator.evaluate(sanity.condition, env_with_result)
            except IncompatibleDimensionsError as exc:
                return self._result(
                    spec,
                    CheckStatus.INCOMPATIBLE_DIMENSIONS,
                    diagnostics=[f"sanity {sanity.name}: {exc}"],
                    checks=steps
                    + [
                        CheckStepResult(
                            name=f"sanity:{sanity.name}",
                            passed=False,
                            status=CheckStatus.INCOMPATIBLE_DIMENSIONS,
                            message=str(exc),
                        )
                    ],
                    expected=spec.expected,
                    actual=from_pint(actual_aligned),
                    normalized_expected=to_si(expected_pq),
                    normalized_actual=to_si(actual_aligned),
                )
            except ForbiddenExpressionError as exc:
                return self._result(
                    spec,
                    CheckStatus.INVALID_INPUT,
                    diagnostics=[f"sanity {sanity.name}: {exc}"],
                    checks=steps
                    + [
                        CheckStepResult(
                            name=f"sanity:{sanity.name}",
                            passed=False,
                            status=CheckStatus.INVALID_INPUT,
                            message=str(exc),
                        )
                    ],
                    expected=spec.expected,
                    actual=from_pint(actual_aligned),
                    normalized_expected=to_si(expected_pq),
                    normalized_actual=to_si(actual_aligned),
                )
            except Exception as exc:
                logger.error("Sanity check %s failed unexpectedly: %s", sanity.name, exc)
                return self._result(
                    spec,
                    CheckStatus.EVALUATION_ERROR,
                    diagnostics=[f"sanity {sanity.name}: {exc}"],
                    checks=steps
                    + [
                        CheckStepResult(
                            name=f"sanity:{sanity.name}",
                            passed=False,
                            status=CheckStatus.EVALUATION_ERROR,
                            message=str(exc),
                        )
                    ],
                    expected=spec.expected,
                    actual=from_pint(actual_aligned),
                    normalized_expected=to_si(expected_pq),
                    normalized_actual=to_si(actual_aligned),
                )
            if not ok:
                msg = sanity.failure_message or f"Sanity check {sanity.name!r} failed"
                steps.append(
                    CheckStepResult(
                        name=f"sanity:{sanity.name}",
                        passed=False,
                        status=CheckStatus.FAIL,
                        message=msg,
                    )
                )
                return self._result(
                    spec,
                    CheckStatus.FAIL,
                    diagnostics=[msg],
                    checks=steps,
                    expected=spec.expected,
                    actual=from_pint(actual_aligned),
                    normalized_expected=to_si(expected_pq),
                    normalized_actual=to_si(actual_aligned),
                )
            steps.append(
                CheckStepResult(name=f"sanity:{sanity.name}", passed=True, message="ok")
            )

        return self._result(
            spec,
            CheckStatus.PASS,
            diagnostics=[],
            checks=steps,
            expected=spec.expected,
            actual=from_pint(actual_aligned),
            normalized_expected=to_si(expected_pq),
            normalized_actual=to_si(actual_aligned),
        )

    def _check_limits(self, spec: VerificationSpec) -> None:
        n_qty = len(spec.inputs) + 1 + (1 if spec.actual is not None else 0)
        if spec.bounds is not None:
            n_qty += int(spec.bounds.minimum is not None) + int(spec.bounds.maximum is not None)
        if spec.tolerance.absolute is not None:
            n_qty += 1
        if n_qty > self.limits.max_quantities:
            raise ValueError(
                f"Too many quantities ({n_qty} > max_quantities={self.limits.max_quantities})"
            )
        if spec.expression and len(spec.expression) > self.limits.max_expression_chars:
            raise ValueError(
                f"Expression exceeds max_expression_chars={self.limits.max_expression_chars}"
            )

    def _normalize_inputs(self, spec: VerificationSpec) -> dict[str, Any]:
        env: dict[str, Any] = {}
        for name, qty in spec.inputs.items():
            if not name.isidentifier() or name.startswith("_"):
                raise UnitError(f"Invalid input name: {name!r}")
            env[name] = to_pint(qty)
        return env

    def _compute_actual(
        self,
        spec: VerificationSpec,
        env: dict[str, Any],
        evaluator: SafeExpressionEvaluator,
    ) -> Any:
        if spec.expression:
            raw = evaluator.evaluate(spec.expression, env)
            return _as_pint_quantity(raw)
        assert spec.actual is not None  # validator guarantees expression or actual
        return to_pint(spec.actual)

    def _compare(self, actual_aligned: Any, expected_pq: Any, spec: VerificationSpec) -> tuple[CheckStatus, str]:
        """PASS iff |Δ| <= atol + rtol*|expected|. Both tolerances optional but at least one set."""
        try:
            atol = 0.0
            if spec.tolerance.absolute is not None:
                atol_q = to_pint(spec.tolerance.absolute)
                atol = float(convert_to(atol_q, expected_pq).magnitude)
            rtol = float(spec.tolerance.relative or 0.0)
        except UnitError as exc:
            return CheckStatus.INVALID_INPUT, f"Invalid tolerance unit: {exc}"
        except IncompatibleDimensionsError as exc:
            return CheckStatus.INCOMPATIBLE_DIMENSIONS, f"Tolerance dimensions: {exc}"

        delta = abs(float(actual_aligned.magnitude) - float(expected_pq.magnitude))
        threshold = atol + rtol * abs(float(expected_pq.magnitude))
        if delta <= threshold:
            return CheckStatus.PASS, f"delta={delta} <= threshold={threshold}"
        return CheckStatus.FAIL, f"delta={delta} > threshold={threshold} (atol={atol}, rtol={rtol})"

    def _check_bounds(self, actual_aligned: Any, spec: VerificationSpec) -> tuple[CheckStatus, str]:
        bounds = spec.bounds
        assert bounds is not None
        try:
            if bounds.minimum is not None:
                lo = convert_to(to_pint(bounds.minimum), actual_aligned)
                if float(actual_aligned.magnitude) < float(lo.magnitude):
                    return (
                        CheckStatus.OUT_OF_BOUNDS,
                        f"actual {from_pint(actual_aligned)} < minimum {bounds.minimum}",
                    )
            if bounds.maximum is not None:
                hi = convert_to(to_pint(bounds.maximum), actual_aligned)
                if float(actual_aligned.magnitude) > float(hi.magnitude):
                    return (
                        CheckStatus.OUT_OF_BOUNDS,
                        f"actual {from_pint(actual_aligned)} > maximum {bounds.maximum}",
                    )
        except UnitError as exc:
            return CheckStatus.INVALID_INPUT, f"Invalid bounds unit: {exc}"
        except IncompatibleDimensionsError as exc:
            return CheckStatus.INCOMPATIBLE_DIMENSIONS, f"Bounds dimensions: {exc}"
        return CheckStatus.PASS, "within bounds"

    def _result(
        self,
        spec: VerificationSpec,
        status: CheckStatus,
        *,
        diagnostics: list[str],
        checks: list[CheckStepResult],
        expected: Quantity | None = None,
        actual: Quantity | None = None,
        normalized_expected: Quantity | None = None,
        normalized_actual: Quantity | None = None,
    ) -> VerificationResult:
        units = {name: qty.unit for name, qty in spec.inputs.items()}
        units["expected"] = spec.expected.unit
        if spec.actual is not None:
            units["actual"] = spec.actual.unit
        provenance = VerificationProvenance(
            verifier_version=VERIFIER_VERSION,
            spec_id=spec.spec_id,
            claim_id=spec.claim_id,
            spec_hash=_spec_hash(spec),
            inputs={name: qty.model_dump(mode="json") for name, qty in spec.inputs.items()},
            units=units,
            formula=spec.expression,
            tolerance=spec.tolerance.model_dump(mode="json"),
            numerical_policy=NUMERICAL_POLICY,
            bounds=spec.bounds.model_dump(mode="json") if spec.bounds else None,
        )
        return VerificationResult(
            spec_id=spec.spec_id,
            claim_id=spec.claim_id,
            status=status,
            expected=expected if expected is not None else spec.expected,
            actual=actual,
            normalized_expected=normalized_expected,
            normalized_actual=normalized_actual,
            diagnostics=diagnostics,
            checks=checks,
            provenance=provenance,
        )
