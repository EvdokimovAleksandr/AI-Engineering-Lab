"""Adjudication: combine deterministic checks + verification + red team.

Critical rule: LLM cannot override a deterministic critical failure into PASS.
"""

from __future__ import annotations

from ai_lab.core.enums import (
    AdjudicationStatus,
    AgreementType,
    AttackSeverity,
    VerificationStatus,
)
from ai_lab.core.models import (
    AdjudicationResult,
    DeterministicCheckReport,
    RedTeamReport,
    VerificationReport,
)


def adjudicate(
    *,
    check_report: DeterministicCheckReport | None,
    verification: VerificationReport | None,
    red_team: RedTeamReport | None,
    require_independent_review: bool = True,
    require_red_team: bool = True,
) -> AdjudicationResult:
    """Combine gates. Profile may waive V/RT only when TaskRouter policy says so.

    SIMPLE: deterministic checks only (require_independent_review=False).
    STANDARD: verification required; red team optional (require_red_team=False).
    COMPLEX/RESEARCH: full V ∥ RT (defaults).
    """
    reasons: list[str] = []
    det_fail = bool(check_report and check_report.has_critical_failure)
    if det_fail:
        reasons.extend(check_report.critical_failures if check_report else [])
        reasons.append("Deterministic critical failure blocks PASS")

    v_status = verification.status if verification else None
    if require_independent_review and verification is None:
        reasons.append("Missing VerificationReport")

    rt_sev = red_team.max_severity if red_team else None
    rt_reject = bool(red_team and (red_team.recommended_reject or rt_sev in {
        AttackSeverity.HIGH,
        AttackSeverity.CRITICAL,
    }))
    if require_red_team and red_team is None:
        reasons.append("Missing RedTeamReport")

    # Deterministic FAIL always wins — cannot be upgraded by LLM PASS
    if det_fail:
        return AdjudicationResult(
            status=AdjudicationStatus.FAIL,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=True,
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        )

    # SIMPLE profile: checks-only gate (workflow policy already decided).
    if not require_independent_review:
        if check_report is not None and check_report.results and not check_report.all_passed:
            reasons.append("Deterministic checks did not all pass")
            return AdjudicationResult(
                status=AdjudicationStatus.FAIL,
                reasons=reasons,
                verification_status=v_status,
                red_team_max_severity=rt_sev,
                deterministic_critical_failure=False,
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            )
        reasons.append("SIMPLE profile: adjudication PASS on deterministic checks")
        agreement = (
            AgreementType.INDEPENDENT_EVIDENCE
            if check_report is not None and check_report.results and check_report.all_passed
            else AgreementType.CONSENSUS
        )
        return AdjudicationResult(
            status=AdjudicationStatus.PASS,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=agreement,
        )

    if verification is None or (require_red_team and red_team is None):
        return AdjudicationResult(
            status=AdjudicationStatus.INSUFFICIENT_EVIDENCE,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=AgreementType.MIXED,
        )

    if v_status in {
        VerificationStatus.FAIL,
        VerificationStatus.DISPUTED,
        VerificationStatus.INSUFFICIENT_EVIDENCE,
    }:
        status = (
            AdjudicationStatus.FAIL
            if v_status == VerificationStatus.FAIL
            else AdjudicationStatus.DISPUTED
            if v_status == VerificationStatus.DISPUTED
            else AdjudicationStatus.INSUFFICIENT_EVIDENCE
        )
        reasons.append(f"Verification status={v_status.value}")
        return AdjudicationResult(
            status=status,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=verification.agreement_type,
        )

    if rt_reject:
        reasons.append("Red team HIGH/CRITICAL or recommended_reject")
        return AdjudicationResult(
            status=AdjudicationStatus.DISPUTED,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=AgreementType.MIXED,
        )

    # Both ok and checks ok
    if check_report is not None and check_report.results and check_report.all_passed:
        agreement = AgreementType.INDEPENDENT_EVIDENCE
    else:
        agreement = AgreementType.CONSENSUS
        reasons.append("PASS without independent math recompute → CONSENSUS only")

    reasons.append("Adjudication PASS")
    return AdjudicationResult(
        status=AdjudicationStatus.PASS,
        reasons=reasons,
        verification_status=v_status,
        red_team_max_severity=rt_sev,
        deterministic_critical_failure=False,
        agreement_type=agreement,
    )
