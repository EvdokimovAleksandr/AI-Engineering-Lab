"""Deterministic math / recompute checks — LLM is not the authority."""

from __future__ import annotations

import ast
import math
import re
from typing import Any

from ai_lab.core.enums import AgreementType
from ai_lab.core.models import MathCheckRequest, MathCheckResult
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


def _safe_eval_expression(expression: str, inputs: dict[str, float]) -> float:
    """Evaluate a restricted arithmetic expression with named inputs."""
    # Allow only names from inputs plus math functions via a tiny whitelist
    allowed_names: dict[str, Any] = {k: float(v) for k, v in inputs.items()}
    allowed_names.update(
        {
            "pi": math.pi,
            "e": math.e,
            "sqrt": math.sqrt,
            "abs": abs,
            "min": min,
            "max": max,
            "round": round,
        }
    )
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id not in allowed_names:
                raise ValueError(f"Disallowed name in expression: {node.id}")
        elif isinstance(
            node,
            (
                ast.Expression,
                ast.BinOp,
                ast.UnaryOp,
                ast.Call,
                ast.Load,
                ast.Constant,
                ast.Add,
                ast.Sub,
                ast.Mult,
                ast.Div,
                ast.Pow,
                ast.Mod,
                ast.FloorDiv,
                ast.USub,
                ast.UAdd,
                ast.Compare,
                ast.Eq,
                ast.NotEq,
                ast.Lt,
                ast.LtE,
                ast.Gt,
                ast.GtE,
                ast.And,
                ast.Or,
                ast.BoolOp,
                ast.IfExp,
            ),
        ):
            continue
        elif isinstance(node, ast.Attribute):
            raise ValueError("Attribute access not allowed in MathCheck expression")
        else:
            raise ValueError(f"Disallowed AST node in expression: {type(node).__name__}")
    return float(eval(compile(tree, "<math_check>", "eval"), {"__builtins__": {}}, allowed_names))


def _check_units(req: MathCheckRequest) -> str | None:
    """Return discrepancy if required_units are violated."""
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
    # Prefer last numeric token
    for line in reversed(lines):
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", line)
        if match:
            return float(match.group(0))
    return None


async def run_math_check(
    req: MathCheckRequest,
    *,
    execute_code=None,
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
        )

    computed: float | None = None
    details: dict[str, Any] = {}

    try:
        if req.code and execute_code is not None:
            exec_result = await execute_code(req.code)
            details["exec"] = {
                "returncode": exec_result.get("returncode"),
                "stderr": exec_result.get("stderr", "")[:500],
            }
            if exec_result.get("returncode") not in (0, None):
                return MathCheckResult(
                    check_id=req.check_id,
                    passed=False,
                    expected=req.expected,
                    discrepancy=f"Recompute failed with returncode={exec_result.get('returncode')}",
                    agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                    details=details,
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
                )
        elif req.expression:
            computed = _safe_eval_expression(req.expression, req.inputs)
        elif req.expected is not None and req.inputs:
            # No expression/code — cannot independently verify
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy="MathCheck missing expression/code for independent recompute",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
            )
        else:
            return MathCheckResult(
                check_id=req.check_id,
                passed=False,
                expected=req.expected,
                discrepancy="MathCheckRequest has neither expression nor code",
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
                details=details,
            )
    except Exception as exc:
        logger.error("MathCheck failed unexpectedly: %s", exc)
        return MathCheckResult(
            check_id=req.check_id,
            passed=False,
            expected=req.expected,
            discrepancy=f"MathCheck error: {exc}",
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            details=details,
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
    )
