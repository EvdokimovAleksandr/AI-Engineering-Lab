"""Adjudication: combine deterministic checks + verification + red team.

Critical rules:
- LLM cannot override a deterministic critical failure into PASS.
- Empty / missing required verification cannot become PASS.
- Missing required calculation / irrelevant computation → INSUFFICIENT_EVIDENCE.
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
    EvidenceCompletenessReport,
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
    evidence_completeness: EvidenceCompletenessReport | None = None,
    verification_required: bool = True,
) -> AdjudicationResult:
    """Combine gates. Profile may waive V/RT only when TaskRouter policy says so.

    SIMPLE: deterministic checks only (require_independent_review=False) — but
    empty checks still cannot PASS when verification_required.
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
        result = AdjudicationResult(
            status=AdjudicationStatus.FAIL,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=True,
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        )
        result.engineering_outcome = result.status
        if evidence_completeness is not None:
            result.evidence_completeness = evidence_completeness.model_dump(mode="json")
        return result

    # V2.6: evidence completeness before any PASS path.
    if evidence_completeness is not None and not evidence_completeness.is_complete:
        reasons.extend(evidence_completeness.reasons)
        # Irrelevant / dimension mismatch with executed wrong outputs → FAIL
        # when relevance explicitly failed; otherwise INSUFFICIENT_EVIDENCE.
        relevance_failed = (
            evidence_completeness.computation_complete
            and not evidence_completeness.computation_relevant
            and bool(evidence_completeness.relevance_results)
        )
        checks_failed = (
            evidence_completeness.verification_complete
            and not evidence_completeness.required_checks_pass
        )
        if checks_failed:
            status = AdjudicationStatus.FAIL
            reasons.append("Required deterministic checks failed")
        elif relevance_failed and any(
            r.dimension_mismatches or r.missing_outputs
            for r in evidence_completeness.relevance_results
        ):
            status = AdjudicationStatus.INSUFFICIENT_EVIDENCE
            reasons.append("Computation not relevant to CalculationSpec")
        else:
            status = AdjudicationStatus.INSUFFICIENT_EVIDENCE
            reasons.append("Required engineering evidence incomplete")
        result = AdjudicationResult(
            status=status,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=AgreementType.MIXED,
            evidence_completeness=evidence_completeness.model_dump(mode="json"),
        )
        result.engineering_outcome = result.status
        return result

    # Empty check report cannot PASS when verification is required.
    if verification_required:
        empty = check_report is None or (
            not check_report.results and not check_report.verification_results
        )
        if empty:
            reasons.append("Empty verification report cannot PASS (verification_required)")
            result = AdjudicationResult(
                status=AdjudicationStatus.INSUFFICIENT_EVIDENCE,
                reasons=reasons,
                verification_status=v_status,
                red_team_max_severity=rt_sev,
                deterministic_critical_failure=False,
                agreement_type=AgreementType.MIXED,
            )
            result.engineering_outcome = result.status
            if evidence_completeness is not None:
                result.evidence_completeness = evidence_completeness.model_dump(mode="json")
            return result
        if check_report is not None and not check_report.all_passed:
            reasons.append("Deterministic checks did not all pass")
            result = AdjudicationResult(
                status=AdjudicationStatus.FAIL,
                reasons=reasons,
                verification_status=v_status,
                red_team_max_severity=rt_sev,
                deterministic_critical_failure=False,
                agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
            )
            result.engineering_outcome = result.status
            return result

    # SIMPLE profile: checks-only gate (workflow policy already decided).
    if not require_independent_review:
        reasons.append("SIMPLE profile: adjudication PASS on deterministic checks")
        agreement = (
            AgreementType.INDEPENDENT_EVIDENCE
            if check_report is not None and check_report.all_passed
            else AgreementType.CONSENSUS
        )
        result = AdjudicationResult(
            status=AdjudicationStatus.PASS,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=agreement,
        )
        result.engineering_outcome = result.status
        if evidence_completeness is not None:
            result.evidence_completeness = evidence_completeness.model_dump(mode="json")
        return result

    if verification is None or (require_red_team and red_team is None):
        result = AdjudicationResult(
            status=AdjudicationStatus.INSUFFICIENT_EVIDENCE,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=AgreementType.MIXED,
        )
        result.engineering_outcome = result.status
        return result

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
        result = AdjudicationResult(
            status=status,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=verification.agreement_type,
        )
        result.engineering_outcome = result.status
        return result

    if rt_reject:
        reasons.append("Red team HIGH/CRITICAL or recommended_reject")
        result = AdjudicationResult(
            status=AdjudicationStatus.DISPUTED,
            reasons=reasons,
            verification_status=v_status,
            red_team_max_severity=rt_sev,
            deterministic_critical_failure=False,
            agreement_type=AgreementType.MIXED,
        )
        result.engineering_outcome = result.status
        return result

    # Both ok and checks ok
    if check_report is not None and check_report.all_passed:
        agreement = AgreementType.INDEPENDENT_EVIDENCE
    else:
        agreement = AgreementType.CONSENSUS
        reasons.append("PASS without independent math recompute → CONSENSUS only")

    reasons.append("Adjudication PASS")
    result = AdjudicationResult(
        status=AdjudicationStatus.PASS,
        reasons=reasons,
        verification_status=v_status,
        red_team_max_severity=rt_sev,
        deterministic_critical_failure=False,
        agreement_type=agreement,
    )
    result.engineering_outcome = result.status
    if evidence_completeness is not None:
        result.evidence_completeness = evidence_completeness.model_dump(mode="json")
    return result
