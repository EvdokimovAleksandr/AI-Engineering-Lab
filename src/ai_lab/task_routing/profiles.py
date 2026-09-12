"""Workflow profile TaskGraph templates — overlays on StaticPlanner, not a second engine."""

from __future__ import annotations

from ai_lab.core.enums import AgentRole, ProjectState, TaskKind
from ai_lab.core.models import BudgetSlice, TaskSpec
from ai_lab.task_routing.enums import WorkflowProfile

# Duplicated from planner.schemas to avoid import cycles
# (profiles ← static ← factory ← planner.__init__ ← … ← tools ← schemas).
INDEPENDENT_REVIEW_GROUP = "independent_review"


def _slice_agent() -> BudgetSlice:
    return BudgetSlice(max_agent_calls=1, max_tool_calls=8, max_tokens=8_000, max_cost=0.5)


def _slice_system() -> BudgetSlice:
    return BudgetSlice(max_agent_calls=0, max_tool_calls=10, max_tokens=0, max_cost=0.0)


def simple_pipeline_tasks() -> list[TaskSpec]:
    """Lightweight path: framing → deterministic calc → checks → gate → result.

    No research / red-team. Adjudication uses deterministic checks only
    (require_independent_review=False on the RoutingDecision).
    """
    return [
        TaskSpec(
            task_id="understanding",
            role=AgentRole.CHIEF_ENGINEER,
            objective="Briefly formalize the closed-form calculation problem",
            inputs=["problem.md", "requirements.md", "assumptions.md"],
            output_schema="problem_framing",
            depends_on=[],
            budget_slice=_slice_agent(),
            state_context=ProjectState.UNDERSTANDING,
            metadata={"workflow_profile": WorkflowProfile.SIMPLE.value},
        ),
        TaskSpec(
            task_id="calculation",
            role=AgentRole.SIMULATION,
            objective="Run deterministic quantitative calculation (tool-backed)",
            inputs=["understanding"],
            output_schema="computation",
            depends_on=["understanding"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.CALCULATION,
            metadata={"workflow_profile": WorkflowProfile.SIMPLE.value},
        ),
        TaskSpec(
            task_id="deterministic_verify",
            task_kind=TaskKind.DETERMINISTIC_CHECK,
            role=None,
            objective="Deterministic validation of calculation claims",
            inputs=["calculation"],
            output_schema="check_report",
            depends_on=["calculation"],
            budget_slice=_slice_system(),
            state_context=ProjectState.VERIFICATION,
            metadata={"workflow_profile": WorkflowProfile.SIMPLE.value},
        ),
        TaskSpec(
            task_id="adjudication",
            task_kind=TaskKind.ADJUDICATION,
            role=None,
            objective="Lightweight gate: deterministic checks only (SIMPLE profile)",
            inputs=["deterministic_verify"],
            output_schema="adjudication_result",
            depends_on=["deterministic_verify"],
            budget_slice=BudgetSlice(),
            state_context=ProjectState.VERIFICATION,
            metadata={
                "workflow_profile": WorkflowProfile.SIMPLE.value,
                "require_independent_review": False,
            },
        ),
        TaskSpec(
            task_id="synthesis",
            role=AgentRole.CHIEF_ENGINEER,
            objective="synthesis: report deterministic calculation result",
            inputs=["adjudication"],
            output_schema="synthesis_bundle",
            depends_on=["adjudication"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.SYNTHESIS,
            metadata={"workflow_profile": WorkflowProfile.SIMPLE.value},
        ),
    ]


def standard_pipeline_tasks() -> list[TaskSpec]:
    """STANDARD: decompose → specialist calc → deterministic checks → verification."""
    return [
        TaskSpec(
            task_id="understanding",
            role=AgentRole.CHIEF_ENGINEER,
            objective="Formalize the engineering design problem and assumptions",
            inputs=["problem.md", "requirements.md", "assumptions.md"],
            output_schema="problem_framing",
            depends_on=[],
            budget_slice=_slice_agent(),
            state_context=ProjectState.UNDERSTANDING,
        ),
        TaskSpec(
            task_id="decomposition",
            role=AgentRole.CHIEF_ENGINEER,
            objective="Decompose into calculation and verification work",
            inputs=["understanding"],
            output_schema="decomposition",
            depends_on=["understanding"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.DECOMPOSITION,
        ),
        TaskSpec(
            task_id="analysis",
            role=AgentRole.THEORIST,
            objective="Fix material model and strength criterion assumptions",
            inputs=["decomposition"],
            output_schema="analysis",
            depends_on=["decomposition"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.ANALYSIS,
        ),
        TaskSpec(
            task_id="calculation",
            role=AgentRole.SIMULATION,
            objective="Deterministic engineering calculations with unit checks",
            inputs=["analysis"],
            output_schema="computation",
            depends_on=["analysis"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.CALCULATION,
        ),
        TaskSpec(
            task_id="deterministic_verify",
            task_kind=TaskKind.DETERMINISTIC_CHECK,
            role=None,
            objective="Deterministic checks and freeze ReviewBundle",
            inputs=["calculation"],
            output_schema="check_report",
            depends_on=["calculation"],
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
            task_id="adjudication",
            task_kind=TaskKind.ADJUDICATION,
            role=None,
            objective="Adjudicate checks + verification (STANDARD: no red team)",
            inputs=["verification"],
            output_schema="adjudication_result",
            depends_on=["verification"],
            budget_slice=BudgetSlice(),
            state_context=ProjectState.VERIFICATION,
            metadata={
                "workflow_profile": WorkflowProfile.STANDARD.value,
                "require_independent_review": True,
                "require_red_team": False,
            },
        ),
        TaskSpec(
            task_id="synthesis",
            role=AgentRole.CHIEF_ENGINEER,
            objective="synthesis: consolidate verified engineering findings",
            inputs=["adjudication"],
            output_schema="synthesis_bundle",
            depends_on=["adjudication"],
            budget_slice=_slice_agent(),
            state_context=ProjectState.SYNTHESIS,
        ),
    ]


def complex_pipeline_tasks() -> list[TaskSpec]:
    """COMPLEX: decomposition → specialists → tools → V ∥ RT → adjudication."""
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
            task_id="analysis",
            role=AgentRole.THEORIST,
            objective="Analyze models and assumptions",
            inputs=["decomposition"],
            output_schema="analysis",
            depends_on=["decomposition"],
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


def profile_to_pipeline_name(profile: WorkflowProfile) -> str:
    return profile.value.lower()


def pipeline_tasks_for_profile(profile: WorkflowProfile) -> list[TaskSpec]:
    if profile == WorkflowProfile.SIMPLE:
        return simple_pipeline_tasks()
    if profile == WorkflowProfile.STANDARD:
        return standard_pipeline_tasks()
    if profile == WorkflowProfile.COMPLEX:
        return complex_pipeline_tasks()
    if profile == WorkflowProfile.RESEARCH:
        # Full lab = existing default pipeline (imported lazily to avoid cycles).
        from ai_lab.planner.static import default_pipeline_tasks

        return default_pipeline_tasks()
    raise ValueError(f"Unknown workflow profile {profile!r}")


KNOWN_WORKFLOW_PIPELINES: frozenset[str] = frozenset(
    {"default", "uniaxial_tension", "simple", "standard", "complex", "research"}
)
