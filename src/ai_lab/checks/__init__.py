"""Run deterministic checks against reviewable claims / math_check payloads."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ai_lab.checks.math_check import run_math_check
from ai_lab.core.models import (
    BlindClaimView,
    DeterministicCheckReport,
    MathCheckRequest,
    MathCheckResult,
)


async def run_deterministic_checks(
    claims: list[BlindClaimView],
    *,
    execute_code: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> DeterministicCheckReport:
    """
    Execute embedded math_check requests.

    Critical failures = failed math_check only.
    Legacy CALCULATION claims without math_check are noted but do not poison the run
    (they cannot create INDEPENDENT_EVIDENCE either).
    """
    results: list[MathCheckResult] = []
    critical: list[str] = []

    for claim in claims:
        if not claim.math_check:
            continue
        req = MathCheckRequest.model_validate({**claim.math_check, "claim_id": claim.claim_id})
        result = await run_math_check(req, execute_code=execute_code)
        results.append(result)
        if not result.passed:
            critical.append(
                f"claim={claim.claim_id} check={result.check_id}: {result.discrepancy}"
            )

    return DeterministicCheckReport(results=results, critical_failures=critical)
