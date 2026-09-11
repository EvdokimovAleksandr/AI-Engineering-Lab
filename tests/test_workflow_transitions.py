"""Workflow transition tests."""

from ai_lab.core.enums import AttackSeverity, ProjectState, VerificationStatus
from ai_lab.core.models import (
    ProjectSnapshot,
    RedTeamAttack,
    RedTeamReport,
    VerificationReport,
)
from ai_lab.workflows.engine import WorkflowEngine
from ai_lab.workflows.transitions import next_after_red_team, next_after_verification


def test_verification_fail_goes_to_iteration() -> None:
    report = VerificationReport(status=VerificationStatus.FAIL, discrepancies=["dim mismatch"])
    assert (
        next_after_verification(ProjectState.VERIFICATION, report)
        == ProjectState.ITERATION_REQUIRED
    )


def test_verification_pass_goes_to_synthesis() -> None:
    report = VerificationReport(status=VerificationStatus.PASS)
    assert (
        next_after_verification(ProjectState.VERIFICATION, report)
        == ProjectState.SYNTHESIS
    )


def test_red_team_critical_awaits_human() -> None:
    report = RedTeamReport(
        recommended_reject=True,
        attacks=[
            RedTeamAttack(
                description="breaks physics",
                severity=AttackSeverity.CRITICAL,
                category="physical_law",
            )
        ],
    )
    assert (
        next_after_red_team(ProjectState.RED_TEAM, report, hitl_on_disputed=True)
        == ProjectState.AWAITING_HUMAN
    )


def test_engine_increments_iteration_on_fail() -> None:
    snap = ProjectSnapshot(project_name="x", state=ProjectState.VERIFICATION)
    engine = WorkflowEngine(snap)
    engine.apply_verification(VerificationReport(status=VerificationStatus.DISPUTED))
    assert engine.state == ProjectState.ITERATION_REQUIRED
    assert engine.snapshot.iteration == 1
