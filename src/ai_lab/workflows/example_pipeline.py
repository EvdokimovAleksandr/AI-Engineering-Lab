"""Example stage → default agent roles mapping for MVP pipeline.

NOTE: This is a stage→roles table, NOT a dependency graph.
LabRuntime executes a validated TaskGraph (see planner/). StaticPlanner
compiles this table into data. Independent Verification ∥ Red Team remain
siblings under independence_group=independent_review.
"""

from __future__ import annotations

from ai_lab.core.enums import AgentRole, ProjectState

# Which roles the orchestrator should schedule for each stage by default
STAGE_ROLES: dict[ProjectState, list[AgentRole]] = {
    ProjectState.UNDERSTANDING: [AgentRole.CHIEF_ENGINEER],
    ProjectState.DECOMPOSITION: [AgentRole.CHIEF_ENGINEER],
    ProjectState.RESEARCH: [AgentRole.RESEARCH],
    ProjectState.HYPOTHESIS: [AgentRole.THEORIST],
    ProjectState.ANALYSIS: [AgentRole.THEORIST],
    ProjectState.CALCULATION: [AgentRole.SIMULATION],
    ProjectState.SIMULATION: [AgentRole.SIMULATION],
    # Handled specially: parallel Verification + Red Team (see LabRuntime._run_independent_review)
    ProjectState.VERIFICATION: [],
    ProjectState.RED_TEAM: [],  # merged into VERIFICATION parallel review
    ProjectState.SYNTHESIS: [AgentRole.CHIEF_ENGINEER],
    ProjectState.ITERATION_REQUIRED: [AgentRole.THEORIST, AgentRole.SIMULATION],
}
