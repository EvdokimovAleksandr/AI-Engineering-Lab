"""Workflow engine wrapping project state transitions."""

from __future__ import annotations

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import ProjectSnapshot, RedTeamReport, VerificationReport
from ai_lab.workflows import transitions


class WorkflowEngine:
    def __init__(self, snapshot: ProjectSnapshot, *, hitl_on_disputed: bool = True) -> None:
        self.snapshot = snapshot
        self.hitl_on_disputed = hitl_on_disputed

    @property
    def state(self) -> ProjectState:
        return self.snapshot.state

    def set_state(self, state: ProjectState) -> None:
        self.snapshot.state = state

    def advance(self) -> ProjectState:
        nxt = transitions.advance(self.snapshot.state)
        self.snapshot.state = nxt
        return nxt

    def apply_verification(self, report: VerificationReport) -> ProjectState:
        nxt = transitions.next_after_verification(self.snapshot.state, report)
        self.snapshot.state = nxt
        if nxt == ProjectState.ITERATION_REQUIRED:
            self.snapshot.iteration += 1
        return nxt

    def apply_red_team(self, report: RedTeamReport) -> ProjectState:
        nxt = transitions.next_after_red_team(
            self.snapshot.state,
            report,
            hitl_on_disputed=self.hitl_on_disputed,
        )
        self.snapshot.state = nxt
        return nxt

    def request_iteration(self) -> ProjectState:
        self.snapshot.state = ProjectState.ITERATION_REQUIRED
        self.snapshot.iteration += 1
        return self.snapshot.state

    def is_terminal(self) -> bool:
        return self.snapshot.state in {
            ProjectState.COMPLETED,
            ProjectState.AWAITING_HUMAN,
            ProjectState.BUDGET_EXCEEDED,
            ProjectState.DISPUTED,
        }
