"""Example stage → default agent roles mapping for MVP pipeline."""

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
    ProjectState.VERIFICATION: [AgentRole.VERIFICATION],
    ProjectState.RED_TEAM: [AgentRole.RED_TEAM],
    ProjectState.SYNTHESIS: [AgentRole.CHIEF_ENGINEER],
    ProjectState.ITERATION_REQUIRED: [AgentRole.THEORIST, AgentRole.SIMULATION],
}

# Stages that should fan-out verification + red team independently when claims exist
INDEPENDENT_REVIEW_GROUP = "independent_review"
