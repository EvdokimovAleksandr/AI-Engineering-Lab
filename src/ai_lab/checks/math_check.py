"""Deterministic math / recompute checks — LLM is not the authority.

Expression path uses DeterministicVerifier (AST interpreter + optional Pint).
Sandbox `code` path still uses python.execute for independent recompute.
"""

from __future__ import annotations

import re
from typing import Any

from ai_lab.checks.verifier import DeterministicVerifier, spec_from_math_check
from ai_lab.core.enums import AgreementType, CheckStatus
from ai_lab.core.models import MathCheckRequest, MathCheckResult, VerificationResult
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


def _coerce_str_dict(value: Any) -> dict[str, str]:
    """LLM may emit units as a bare string or list; MathCheckRequest needs dict[str, str]."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if v is not None}
    # Bare unit string / other shapes cannot be mapped to input keys safely.
    logger.warning("Discarding non-dict math_check units/required_units: %r", value)
    return {}


def _coerce_float_dict(value: Any) -> dict[str, float]:
    """Accept dict or list[{name,value}] for math_check.inputs."""
    if value is None:
        return {}
    if isinstance(value, dict):
        out: dict[str, float] = {}
        for key, raw in value.items():
            try:
                out[str(key)] = float(raw)
            except (TypeError, ValueError):
                logger.warning("Skipping non-numeric math_check input %s=%r", key, raw)
        return out
    if isinstance(value, list):
        out = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("id") or item.get("key")
            raw = item.get("value") if "value" in item else item.get("magnitude")
            if name is None or raw is None:
                continue
            try:
                out[str(name)] = float(raw)
            except (TypeError, ValueError):
                logger.warning("Skipping non-numeric math_check input item %r", item)
        return out
    logger.warning("Discarding non-dict math_check inputs: %r", value)
    return {}


def normalize_math_check_payload(raw: Any) -> dict[str, Any] | None:
    """Normalize LLM math_check blobs before MathCheckRequest validation."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.error("math_check must be an object, got %s", type(raw).__name__)
        raise ValueError(f"math_check must be a JSON object, got {type(raw).__name__}")
    out = dict(raw)
    out["units"] = _coerce_str_dict(out.get("units"))
    out["required_units"] = _coerce_str_dict(out.get("required_units"))
    out["inputs"] = _coerce_float_dict(out.get("inputs"))
    if "expected" in out and out["expected"] is not None and not isinstance(out["expected"], (int, float)):
        try:
            out["expected"] = float(out["expected"])
        except (TypeError, ValueError):
            logger.warning("Dropping non-numeric math_check.expected=%r", out["expected"])
            out["expected"] = None
    if "tolerance" in out and out["tolerance"] is not None and not isinstance(out["tolerance"], (int, float)):
        try:
            out["tolerance"] = float(out["tolerance"])
        except (TypeError, ValueError):
            out.pop("tolerance", None)
    if out.get("expression") is not None:
        out["expression"] = str(out["expression"])
    if out.get("code") is not None:
        out["code"] = str(out["code"])
    return out


def _check_units(req: MathCheckRequest) -> str | None:
    """Legacy string-tag unit guard for MathCheckRequest.required_units.

    New unit-aware work belongs on VerificationSpec (Pint). This stays so
    existing required_units tests keep their exact discrepancy text.
    """
    if not req.required_units:
        return None
    for key, required in req.required_units.items():
        actual = req.units.get(key)
        if actual is None:
            return f"Missing unit for parameter {key!r}; required {required!r}"
        if actual != required:
            return f"Unit mismatch for {key!r}: got {actual!r}, required {required!r}"
    return None


def _parse_float_from_stdout(stdout: str) -> float | None:
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", line)
        if match:
            return float(match.group(0))
    return None


def math_result_from_verification(vr: VerificationResult) -> MathCheckResult:
    """Keep DeterministicCheckReport.results backward-compatible."""
    computed = vr.actual.value if vr.actual is not None else None
    expected = vr.expected.value if vr.expected is not None else None
    discrepancy = None if vr.passed else ("; ".join(vr.diagnostics) or vr.status.value)
    result = MathCheckResult(
        check_id=vr.spec_id,
        passed=vr.passed,
        computed=computed,
        expected=expected,
        discrepancy=discrepancy,
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        details={
            "status": vr.status.value,
            "result_id": vr.result_id,
            "kind": "verification_spec",
        },
        status=vr.status,
        verification_result=vr,
    )
    return result


async def run_math_check(
    req: MathCheckRequest,
    *,
    execute_code=None,
    verifier: DeterministicVerifier | None = None,
) -> MathCheckResult:
    """
    Run a deterministic math check.

    execute_code: optional async callable(code: str) -> dict with stdout/returncode
    used when req.code is set (sandbox recompute).
    """
    unit_err = _check_units(req)
    if unit_err:
        return MathCheckResult(
            check_id=req.check_id,
            passed=False,
            expected=req.expected,
            discrepancy=unit_err,
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            details={"kind": "unit_failure"},
            status=CheckStatus.FAIL,
        )

    details: dict[str, Any] = {}

    if req.code and execute_code is not None:
        return await _run_sandbox_recompute(req, execute_code)

    if req.expression:
        if req.expected is None:
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=None,
                discrepancy="MathCheck requires expected value",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
                status=CheckStatus.INVALID_INPUT,
            )
        try:
            spec = spec_from_math_check(req)
        except Exception as exc:
            logger.error("MathCheck spec conversion failed: %s", exc)
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy=f"MathCheck error: {exc}",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
                status=CheckStatus.INVALID_INPUT,
            )
        engine = verifier or DeterministicVerifier()
        vr = engine.verify(spec)
        result = math_result_from_verification(vr)
        result.check_id = req.check_id
        result.details = {**result.details, "kind": "expression"}
        result.verification_result = vr
        return result

    if req.expected is not None and req.inputs:
        return MathCheckResult(
            check_id=req.check_id,
            passed=False,
            expected=req.expected,
            discrepancy="MathCheck missing expression/code for independent recompute",
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            details=details,
            status=CheckStatus.INVALID_INPUT,
        )

    return MathCheckResult(
        check_id=req.check_id,
        passed=False,
        expected=req.expected,
        discrepancy="MathCheckRequest has neither expression nor code",
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        details=details,
        status=CheckStatus.INVALID_INPUT,
    )


async def _run_sandbox_recompute(req: MathCheckRequest, execute_code) -> MathCheckResult:
    """Independent recompute via existing python.execute sandbox — not the AST verifier."""
    details: dict[str, Any] = {}
    try:
        exec_result = await execute_code(req.code)
        details["exec"] = {
            "returncode": exec_result.get("returncode"),
            "stderr": exec_result.get("stderr", "")[:500],
            "sandbox_status": exec_result.get("sandbox_status"),
        }
        # Timeout/output-limit are execution outcomes, not CheckStatus.FAIL.
        if exec_result.get("timed_out") or exec_result.get("sandbox_status") == "TIMEOUT":
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy="MathCheck sandbox TIMEOUT",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
                status=CheckStatus.TIMEOUT,
            )
        if exec_result.get("sandbox_status") == "OUTPUT_LIMIT":
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy="MathCheck sandbox OUTPUT_LIMIT",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
                status=CheckStatus.EVALUATION_ERROR,
            )
        if exec_result.get("returncode") not in (0, None):
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy=f"Recompute failed with returncode={exec_result.get('returncode')}",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
                status=CheckStatus.EVALUATION_ERROR,
            )
        computed = _parse_float_from_stdout(str(exec_result.get("stdout") or ""))
        if computed is None:
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy="Recompute produced no parseable float on stdout",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
                status=CheckStatus.EVALUATION_ERROR,
            )
    except TimeoutError as exc:
        logger.error("MathCheck sandbox timeout: %s", exc)
        return MathCheckResult(
            check_id=req.check_id,
            passed=False,
            expected=req.expected,
            discrepancy=f"MathCheck timeout: {exc}",
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            details=details,
            status=CheckStatus.TIMEOUT,
        )
    except Exception as exc:
        logger.error("MathCheck sandbox failed unexpectedly: %s", exc)
        return MathCheckResult(
            check_id=req.check_id,
            passed=False,
            expected=req.expected,
            discrepancy=f"MathCheck error: {exc}",
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            details=details,
            status=CheckStatus.EVALUATION_ERROR,
        )

    if req.expected is None:
        return MathCheckResult(
            check_id=req.check_id,
            passed=False,
            computed=computed,
            expected=None,
            discrepancy="MathCheck requires expected value",
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            details=details,
            status=CheckStatus.INVALID_INPUT,
        )

    delta = abs(float(computed) - float(req.expected))
    passed = delta <= float(req.tolerance)
    return MathCheckResult(
        check_id=req.check_id,
        passed=passed,
        computed=computed,
        expected=req.expected,
        discrepancy=None if passed else f"delta={delta} > tolerance={req.tolerance}",
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        details=details,
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
    )
