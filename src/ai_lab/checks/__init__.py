"""Run deterministic checks against reviewable claims / math_check payloads."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ai_lab.checks.math_check import math_result_from_verification, normalize_math_check_payload, run_math_check
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
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


def _critical_line(claim_id: str, result: MathCheckResult) -> str:
    return f"claim={claim_id} check={result.check_id}: {result.discrepancy}"


async def run_deterministic_checks(
    claims: list[BlindClaimView],
    *,
    execute_code: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    limits: VerificationLimits | None = None,
    verification_cfg: dict[str, Any] | None = None,
    require_specs_for_calculation: bool = False,
) -> DeterministicCheckReport:
    """
    Execute embedded verification_spec (preferred) or math_check requests.

    Critical failures = failed numeric/unit checks only.
    CALCULATION claims without a spec cannot create INDEPENDENT_EVIDENCE.
    When require_specs_for_calculation is True, such claims are logged; empty
    reports still cannot PASS (evidence completeness / adjudication gates).
    """
    engine = DeterministicVerifier(limits or limits_from_config(verification_cfg))
    results: list[MathCheckResult] = []
    verification_results: list[VerificationResult] = []
    critical: list[str] = []
    skipped_calculation = 0

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
            if require_specs_for_calculation and claim.kind.value == "CALCULATION":
                skipped_calculation += 1
                logger.error(
                    "CALCULATION claim %s has no math_check/verification_spec",
                    claim.claim_id,
                )
            continue
        normalized = normalize_math_check_payload(
            {**claim.math_check, "claim_id": claim.claim_id}
        )
        req = MathCheckRequest.model_validate(normalized)
        result = await run_math_check(req, execute_code=execute_code, verifier=engine)
        # Ensure claim_id is recoverable for synthesis grounding.
        if not result.details.get("claim_id"):
            result.details = {**result.details, "claim_id": claim.claim_id}
        results.append(result)
        if result.verification_result is not None:
            verification_results.append(result.verification_result)
        if not result.passed:
            critical.append(_critical_line(claim.claim_id, result))

    if skipped_calculation and not results and not verification_results:
                logger.error(
                    "%s CALCULATION claim(s) without check specs "
                    "(empty report -> INSUFFICIENT_EVIDENCE via completeness gate)",
                    skipped_calculation,
                )

    return DeterministicCheckReport(
        results=results,
        verification_results=verification_results,
        critical_failures=critical,
    )
