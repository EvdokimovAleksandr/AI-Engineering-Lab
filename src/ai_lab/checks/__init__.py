"""Run deterministic checks against reviewable claims / math_check payloads."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ai_lab.checks.math_check import math_result_from_verification, run_math_check
from ai_lab.checks.verifier import DeterministicVerifier, limits_from_config
from ai_lab.core.models import (
    BlindClaimView,
    DeterministicCheckReport,
    MathCheckRequest,
    MathCheckResult,
    VerificationLimits,
    VerificationResult,
    VerificationSpec,
)


def _critical_line(claim_id: str, result: MathCheckResult) -> str:
    return f"claim={claim_id} check={result.check_id}: {result.discrepancy}"


async def run_deterministic_checks(
    claims: list[BlindClaimView],
    *,
    execute_code: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    limits: VerificationLimits | None = None,
    verification_cfg: dict[str, Any] | None = None,
) -> DeterministicCheckReport:
    """
    Execute embedded verification_spec (preferred) or math_check requests.

    Critical failures = failed numeric/unit checks only.
    Legacy CALCULATION claims without a spec are noted but do not poison the run
    (they cannot create INDEPENDENT_EVIDENCE either).
    """
    engine = DeterministicVerifier(limits or limits_from_config(verification_cfg))
    results: list[MathCheckResult] = []
    verification_results: list[VerificationResult] = []
    critical: list[str] = []

    for claim in claims:
        if claim.verification_spec:
            spec = VerificationSpec.model_validate(
                {**claim.verification_spec, "claim_id": claim.claim_id}
            )
            vr = engine.verify(spec)
            verification_results.append(vr)
            math_row = math_result_from_verification(vr)
            results.append(math_row)
            if not vr.passed:
                critical.append(_critical_line(claim.claim_id, math_row))
            continue
        if not claim.math_check:
            continue
        req = MathCheckRequest.model_validate({**claim.math_check, "claim_id": claim.claim_id})
        result = await run_math_check(req, execute_code=execute_code, verifier=engine)
        results.append(result)
        if result.verification_result is not None:
            verification_results.append(result.verification_result)
        if not result.passed:
            critical.append(_critical_line(claim.claim_id, result))

    return DeterministicCheckReport(
        results=results,
        verification_results=verification_results,
        critical_failures=critical,
    )
