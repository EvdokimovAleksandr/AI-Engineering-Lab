"""IterationPolicy — map failure reasons to re-entry workflow states."""

from __future__ import annotations

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import AdjudicationResult, DeterministicCheckReport, VerificationReport


def next_iteration_state(
    *,
    adjudication: AdjudicationResult | None = None,
    verification: VerificationReport | None = None,
    check_report: DeterministicCheckReport | None = None,
) -> ProjectState:
    """
    Choose where to re-enter after a failed/disputed review.

    Not a blind jump to ANALYSIS for every failure.
    """
    texts: list[str] = []
    if adjudication:
        texts.extend(adjudication.reasons)
    if verification:
        texts.extend(verification.discrepancies)
        texts.append(verification.notes)
    if check_report:
        texts.extend(check_report.critical_failures)
        for r in check_report.results:
            if r.discrepancy:
                texts.append(r.discrepancy)

    blob = " ".join(texts).lower()

    if any(k in blob for k in ("unit", "recompute", "calculation", "mathcheck", "delta=")):
        return ProjectState.CALCULATION
    if any(k in blob for k in ("simulation", "sandbox", "returncode")):
        return ProjectState.SIMULATION
    if any(k in blob for k in ("source", "research", "stub", "literature", "insufficient")):
        return ProjectState.RESEARCH
    if any(k in blob for k in ("assumption", "hypothesis", "model", "physics")):
        return ProjectState.ANALYSIS
    return ProjectState.ANALYSIS
