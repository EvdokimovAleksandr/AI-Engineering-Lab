"""StaticPlanner — deterministic TaskGraph without an LLM.

STAGE_ROLES remains a stage→role table. This module compiles the default
pipeline into data (a TaskGraph), which LabRuntime executes only after
validation. Verification ∥ RedTeam are siblings, never a chain.

Workflow profiles (SIMPLE/STANDARD/COMPLEX/RESEARCH) are overlays from
task_routing.profiles — not a second orchestration engine.
"""

from __future__ import annotations

from ai_lab.core.enums import AgentRole, ProjectState, TaskKind
from ai_lab.core.models import BudgetSlice, TaskGraph, TaskGraphProposal, TaskSpec
from ai_lab.planner.context import ProblemContext
from ai_lab.planner.schemas import INDEPENDENT_REVIEW_GROUP
from ai_lab.task_routing.profiles import KNOWN_WORKFLOW_PIPELINES


def _slice_agent() -> BudgetSlice:
    # Sum of slices must fit default RunBudget (100 agent / 200 tool calls).
    return BudgetSlice(max_agent_calls=1, max_tool_calls=8, max_tokens=8_000, max_cost=0.5)


def _slice_system() -> BudgetSlice:
    return BudgetSlice(max_agent_calls=0, max_tool_calls=10, max_tokens=0, max_cost=0.0)


def default_pipeline_tasks() -> list[TaskSpec]:
    """Canonical linear backbone + independent review fan-out/fan-in."""
    return [
        TaskSpec(
            task_id="understanding",
            role=AgentRole.CHIEF_ENGINEER,
            objective="Formalize and understand the engineering problem",
            inputs=["problem.md", "requirements.md", "assumptions.md"],
            output_schema="problem_framing",
            depends_on=[],
            budget_slice=_slice_agent(),
            state_context=ProjectState.UNDERSTANDING,
        ),
        TaskSpec(
            task_id="decomposition",
            role=AgentRole.CHIEF_ENGINEER,
            objective="Decompose the problem into specialist work",
            inputs=["understanding"],
            output_schema="decomposition",
            depends_on=["understanding"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.DECOMPOSITION,
        ),
        TaskSpec(
            task_id="research",
            role=AgentRole.RESEARCH,
            objective="Gather sources and structured findings",
            inputs=["decomposition"],
            output_schema="research_findings",
            depends_on=["decomposition"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.RESEARCH,
        ),
        TaskSpec(
            task_id="hypothesis",
            role=AgentRole.THEORIST,
            objective="Formulate hypotheses and falsifiers",
            inputs=["research"],
            output_schema="hypotheses",
            depends_on=["research"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.HYPOTHESIS,
        ),
        TaskSpec(
            task_id="analysis",
            role=AgentRole.THEORIST,
            objective="Analyze models and assumptions",
            inputs=["hypothesis"],
            output_schema="analysis",
            depends_on=["hypothesis"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.ANALYSIS,
        ),
        TaskSpec(
            task_id="calculation",
            role=AgentRole.SIMULATION,
            objective="Run quantitative calculations",
            inputs=["analysis"],
            output_schema="computation",
            depends_on=["analysis"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.CALCULATION,
        ),
        TaskSpec(
            task_id="simulation",
            role=AgentRole.SIMULATION,
            objective="Run simulation / computation artifacts",
            inputs=["calculation"],
            output_schema="computation",
            depends_on=["calculation"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.SIMULATION,
        ),
        TaskSpec(
            task_id="deterministic_verify",
            task_kind=TaskKind.DETERMINISTIC_CHECK,
            role=None,
            objective="Deterministic checks and freeze ReviewBundle",
            inputs=["simulation"],
            output_schema="check_report",
            depends_on=["simulation"],
            budget_slice=_slice_system(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="verification",
            role=AgentRole.VERIFICATION,
            objective="Independent verification of ReviewBundle",
            inputs=["deterministic_verify"],
            output_schema="verification_report",
            depends_on=["deterministic_verify"],
            independence_group=INDEPENDENT_REVIEW_GROUP,
            budget_slice=_slice_agent(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="red_team",
            role=AgentRole.RED_TEAM,
            objective="Independent red-team attack on ReviewBundle",
            inputs=["deterministic_verify"],
            output_schema="red_team_report",
            depends_on=["deterministic_verify"],
            independence_group=INDEPENDENT_REVIEW_GROUP,
            budget_slice=_slice_agent(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="adjudication",
            task_kind=TaskKind.ADJUDICATION,
            role=None,
            objective="Deterministic adjudication of checks + V ∥ RT",
            inputs=["verification", "red_team"],
            output_schema="adjudication_result",
            depends_on=["verification", "red_team"],
            budget_slice=BudgetSlice(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="synthesis",
            role=AgentRole.CHIEF_ENGINEER,
            objective="synthesis: consolidate verified findings",
            inputs=["adjudication"],
            output_schema="synthesis_bundle",
            depends_on=["adjudication"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.SYNTHESIS,
        ),
    ]


def tensile_pipeline_tasks() -> list[TaskSpec]:
    """Uniaxial tensile benchmark graph. Solver nodes have no AgentRole."""
    return [
        TaskSpec(
            task_id="understanding",
            role=AgentRole.CHIEF_ENGINEER,
            objective="Formalize the tensile fiber problem",
            inputs=["problem.md", "requirements.md", "assumptions.md"],
            output_schema="problem_framing",
            depends_on=[],
            budget_slice=_slice_agent(),
            state_context=ProjectState.UNDERSTANDING,
        ),
        TaskSpec(
            task_id="research_material_properties",
            role=AgentRole.RESEARCH,
            objective="Gather sources for material properties (must not become FACT automatically)",
            inputs=["understanding"],
            output_schema="research_findings",
            depends_on=["understanding"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.RESEARCH,
        ),
        TaskSpec(
            task_id="build_tensile_model",
            task_kind=TaskKind.MODEL_BUILD,
            role=None,
            objective="Load and validate the uniaxial tensile SimulationSpec",
            inputs=["research_material_properties"],
            output_schema="simulation_spec",
            depends_on=["research_material_properties"],
            budget_slice=_slice_system(),
            state_context=ProjectState.ANALYSIS,
            metadata={"spec_id": "uniaxial_tension", "solver_id": "uniaxial_tension"},
        ),
        TaskSpec(
            task_id="run_tensile_simulation",
            task_kind=TaskKind.SIMULATION,
            role=None,
            objective="Evaluate uniaxial tensile stress",
            inputs=["build_tensile_model"],
            output_schema="simulation_result",
            depends_on=["build_tensile_model"],
            budget_slice=_slice_system(),
            state_context=ProjectState.SIMULATION,
            metadata={"solver_id": "uniaxial_tension"},
        ),
        TaskSpec(
            task_id="verify_simulation",
            task_kind=TaskKind.SIMULATION_VERIFICATION,
            role=None,
            objective="Deterministic verification of tensile simulation outputs",
            inputs=["run_tensile_simulation"],
            output_schema="simulation_verification",
            depends_on=["run_tensile_simulation"],
            budget_slice=_slice_system(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="deterministic_verify",
            task_kind=TaskKind.DETERMINISTIC_CHECK,
            role=None,
            objective="Deterministic checks and freeze ReviewBundle",
            inputs=["verify_simulation"],
            output_schema="check_report",
            depends_on=["verify_simulation"],
            budget_slice=_slice_system(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="verification",
            role=AgentRole.VERIFICATION,
            objective="Independent verification of ReviewBundle",
            inputs=["deterministic_verify"],
            output_schema="verification_report",
            depends_on=["deterministic_verify"],
            independence_group=INDEPENDENT_REVIEW_GROUP,
            budget_slice=_slice_agent(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="red_team",
            role=AgentRole.RED_TEAM,
            objective="Independent red-team attack on ReviewBundle",
            inputs=["deterministic_verify"],
            output_schema="red_team_report",
            depends_on=["deterministic_verify"],
            independence_group=INDEPENDENT_REVIEW_GROUP,
            budget_slice=_slice_agent(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="adjudication",
            task_kind=TaskKind.ADJUDICATION,
            role=None,
            objective="Deterministic adjudication of checks + V ∥ RT",
            inputs=["verification", "red_team"],
            output_schema="adjudication_result",
            depends_on=["verification", "red_team"],
            budget_slice=BudgetSlice(),
            state_context=ProjectState.VERIFICATION,
        ),
        TaskSpec(
            task_id="synthesis",
            role=AgentRole.CHIEF_ENGINEER,
            objective="synthesis: consolidate verified findings",
            inputs=["adjudication"],
            output_schema="synthesis_bundle",
            depends_on=["adjudication"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.SYNTHESIS,
        ),
    ]


def tasks_to_proposal_dicts(tasks: list[TaskSpec]) -> list[dict]:
    rows: list[dict] = []
    for task in tasks:
        row = task.model_dump(mode="json", exclude={"review_bundle_path"})
        rows.append(row)
    return rows


_GRAPH_IDS = {
    "default": "static_pipeline",
    "uniaxial_tension": "uniaxial_tension_pipeline",
    "simple": "simple_pipeline",
    "standard": "standard_pipeline",
    "complex": "complex_pipeline",
    "research": "research_pipeline",
}


class StaticPlanner:
    """Regression / demo planner: named pipeline graph (profile or special)."""

    name = "static"

    def __init__(self, *, graph_id: str | None = None, pipeline: str = "default") -> None:
        kind = (pipeline or "default").strip().lower()
        if kind not in KNOWN_WORKFLOW_PIPELINES:
            raise ValueError(
                f"Unknown static pipeline {pipeline!r} "
                f"(expected {sorted(KNOWN_WORKFLOW_PIPELINES)})"
            )
        self.pipeline = kind
        self.graph_id = graph_id or _GRAPH_IDS[kind]

    def _tasks(self) -> list[TaskSpec]:
        # Lazy imports keep planner.static importable without pulling profile↔planner cycles early.
        if self.pipeline == "uniaxial_tension":
            return tensile_pipeline_tasks()
        if self.pipeline == "default":
            return default_pipeline_tasks()
        from ai_lab.task_routing.enums import WorkflowProfile
        from ai_lab.task_routing.profiles import (
            complex_pipeline_tasks,
            pipeline_tasks_for_profile,
            simple_pipeline_tasks,
            standard_pipeline_tasks,
        )

        if self.pipeline == "simple":
            return simple_pipeline_tasks()
        if self.pipeline == "standard":
            return standard_pipeline_tasks()
        if self.pipeline == "complex":
            return complex_pipeline_tasks()
        if self.pipeline == "research":
            return pipeline_tasks_for_profile(WorkflowProfile.RESEARCH)
        raise ValueError(f"Unhandled static pipeline {self.pipeline!r}")

    async def propose(self, context: ProblemContext) -> TaskGraphProposal:
        tasks = self._tasks()
        return TaskGraphProposal(
            graph_id=self.graph_id,
            tasks=tasks_to_proposal_dicts(tasks),
            version=1,
            metadata={
                "planner": self.name,
                "pipeline": self.pipeline,
                "project_id": context.project_id,
            },
        )

    def graph(self, context: ProblemContext) -> TaskGraph:
        """Direct graph for tests; production still goes through proposal+validate."""
        return TaskGraph(
            graph_id=self.graph_id,
            tasks=self._tasks(),
            version=1,
            metadata={"planner": self.name, "pipeline": self.pipeline, "project_id": context.project_id},
        )
