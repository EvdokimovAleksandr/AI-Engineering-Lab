"""Workflow transition table — non-linear, supports iteration."""

from __future__ import annotations

from ai_lab.core.enums import AttackSeverity, ProjectState, VerificationStatus
from ai_lab.core.models import RedTeamReport, VerificationReport


# Default forward path when a stage succeeds without disputes
FORWARD: dict[ProjectState, ProjectState] = {
    ProjectState.CREATED: ProjectState.UNDERSTANDING,
    ProjectState.UNDERSTANDING: ProjectState.DECOMPOSITION,
    ProjectState.DECOMPOSITION: ProjectState.RESEARCH,
    ProjectState.RESEARCH: ProjectState.HYPOTHESIS,
    ProjectState.HYPOTHESIS: ProjectState.ANALYSIS,
    ProjectState.ANALYSIS: ProjectState.CALCULATION,
    ProjectState.CALCULATION: ProjectState.SIMULATION,
    ProjectState.SIMULATION: ProjectState.VERIFICATION,
    ProjectState.VERIFICATION: ProjectState.RED_TEAM,
    ProjectState.RED_TEAM: ProjectState.SYNTHESIS,
    ProjectState.SYNTHESIS: ProjectState.COMPLETED,
    ProjectState.ITERATION_REQUIRED: ProjectState.ANALYSIS,
    ProjectState.DISPUTED: ProjectState.AWAITING_HUMAN,
}


def next_after_verification(
    current: ProjectState,
    report: VerificationReport,
) -> ProjectState:
    """Verification FAIL / DISPUTED / INSUFFICIENT → iterate; PASS → continue."""
    if current != ProjectState.VERIFICATION:
        raise ValueError(f"next_after_verification called in state {current}")
    if report.status == VerificationStatus.PASS:
        return ProjectState.RED_TEAM
    if report.status in {
        VerificationStatus.FAIL,
        VerificationStatus.DISPUTED,
        VerificationStatus.INSUFFICIENT_EVIDENCE,
    }:
        return ProjectState.ITERATION_REQUIRED
    raise ValueError(f"Unknown verification status: {report.status}")


def next_after_red_team(
    current: ProjectState,
    report: RedTeamReport,
    *,
    hitl_on_disputed: bool = True,
) -> ProjectState:
    """Critical red-team findings → DISPUTED / HITL; else SYNTHESIS."""
    if current != ProjectState.RED_TEAM:
        raise ValueError(f"next_after_red_team called in state {current}")
    severity = report.max_severity
    critical = severity in {AttackSeverity.HIGH, AttackSeverity.CRITICAL} or report.recommended_reject
    if critical:
        if hitl_on_disputed:
            return ProjectState.AWAITING_HUMAN
        return ProjectState.DISPUTED
    return ProjectState.SYNTHESIS


def advance(state: ProjectState) -> ProjectState:
    if state not in FORWARD:
        raise KeyError(f"No default forward transition from {state}")
    return FORWARD[state]
