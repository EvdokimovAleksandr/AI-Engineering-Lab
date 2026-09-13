"""Deterministic TaskGraph validator. LLM cannot override the result."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ai_lab.core.enums import AgentRole, ContractStatus, TaskGraphValidationReason, TaskKind
from ai_lab.core.models import (
    RunBudget,
    TaskGraph,
    TaskGraphValidationResult,
    TaskSpec,
)
from ai_lab.planner.contract_binding import contract_binding_errors
from ai_lab.planner.dag import CycleError, topological_order
from ai_lab.planner.hashing import task_graph_hash
from ai_lab.planner.proposal import _scan_forbidden
from ai_lab.planner.schemas import (
    AUTHOR_ROLES,
    INDEPENDENT_REVIEW_GROUP,
    KNOWN_ARTIFACT_IDS,
    KNOWN_TOOL_NAMES,
    OUTPUT_SCHEMAS,
    REVIEW_ROLES,
    schema_allows,
)

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


@dataclass
class TaskGraphValidationContext:
    """Runtime facts the validator may use. Not LLM-controlled."""

    budget: RunBudget | None = None
    known_artifact_ids: frozenset[str] = KNOWN_ARTIFACT_IDS
    known_tool_names: frozenset[str] = KNOWN_TOOL_NAMES
    extra_input_ids: frozenset[str] = field(default_factory=frozenset)
    routing_policy: Any = None
    independence_policy: Any = None
    available_providers: frozenset[str] | None = None
    # PR-04: EngineeringContract binding (TaskGraph = f(contract)).
    contract_version: str | None = None
    contract_status: ContractStatus | None = None
    investigation_id: str | None = None
    required_outputs: list[str] = field(default_factory=list)
    require_calculation_producers: bool = False


def default_budget_slice(task: TaskSpec) -> tuple[int, int, int, float]:
    """Policy defaults used only for plan-vs-RunBudget comparison."""
    if task.budget_slice is not None:
        s = task.budget_slice
        return s.max_agent_calls, s.max_tool_calls, s.max_tokens, s.max_cost
    if task.task_kind == TaskKind.AGENT:
        return 1, 20, 20_000, 1.0
    if task.task_kind == TaskKind.DETERMINISTIC_CHECK:
        return 0, 10, 0, 0.0
    if task.task_kind in {
        TaskKind.MODEL_BUILD,
        TaskKind.SIMULATION,
        TaskKind.SIMULATION_VERIFICATION,
    }:
        return 0, 0, 0, 0.0
    return 0, 0, 0, 0.0


def _fail(
    reason: TaskGraphValidationReason, errors: list[str], *, topo: list[str] | None = None
) -> TaskGraphValidationResult:
    return TaskGraphValidationResult(
        ok=False, reason=reason, errors=errors, topo_order=topo or []
    )


def validate_task_graph(
    graph: TaskGraph,
    context: TaskGraphValidationContext | None = None,
) -> TaskGraphValidationResult:
    """All checks are deterministic functions of (graph, context)."""
    ctx = context or TaskGraphValidationContext()

    if not graph.graph_id or not _ID_RE.match(graph.graph_id):
        return _fail(
            TaskGraphValidationReason.INVALID_GRAPH_ID,
            [f"Invalid graph_id: {graph.graph_id!r}"],
        )
    if not graph.tasks:
        return _fail(TaskGraphValidationReason.MISSING_FIELD, ["TaskGraph.tasks is empty"])

    forbidden = _scan_forbidden(graph.metadata, "$.metadata")
    for task in graph.tasks:
        forbidden.extend(_scan_forbidden(task.metadata, f"{task.task_id}.metadata"))
    if forbidden:
        return _fail(TaskGraphValidationReason.FORBIDDEN_FIELD, forbidden)

    ids = [t.task_id for t in graph.tasks]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        return _fail(
            TaskGraphValidationReason.DUPLICATE_TASK_ID,
            [f"Duplicate task id(s): {dup}"],
        )

    for task in graph.tasks:
        field_err = _required_fields(task)
        if field_err:
            return _fail(TaskGraphValidationReason.MISSING_FIELD, field_err)
        if not _ID_RE.match(task.task_id):
            return _fail(
                TaskGraphValidationReason.MISSING_FIELD,
                [f"Invalid task_id: {task.task_id!r}"],
            )
        if task.task_kind not in TaskKind:
            return _fail(
                TaskGraphValidationReason.UNKNOWN_TASK_KIND,
                [f"{task.task_id}: unknown task_kind"],
            )
        if task.task_kind == TaskKind.AGENT:
            if task.role is None or task.role not in AgentRole:
                return _fail(
                    TaskGraphValidationReason.UNKNOWN_ROLE,
                    [f"{task.task_id}: unknown or missing role"],
                )

    id_set = set(ids)
    for task in graph.tasks:
        for dep in task.depends_on:
            if dep == task.task_id:
                return _fail(
                    TaskGraphValidationReason.SELF_DEPENDENCY,
                    [f"{task.task_id} depends on itself"],
                )
            if dep not in id_set:
                return _fail(
                    TaskGraphValidationReason.MISSING_DEPENDENCY,
                    [f"{task.task_id} depends on unknown task {dep!r}"],
                )

    try:
        topo = topological_order(graph.tasks)
    except CycleError as exc:
        return _fail(TaskGraphValidationReason.CYCLE, [str(exc)])

    input_err = _validate_inputs(graph, ctx)
    if input_err:
        return _fail(TaskGraphValidationReason.INVALID_INPUT, input_err, topo=topo)

    schema_err = _validate_schemas(graph)
    if schema_err:
        return _fail(TaskGraphValidationReason.INVALID_OUTPUT_SCHEMA, schema_err, topo=topo)

    tool_err = _validate_tools(graph, ctx)
    if tool_err:
        return _fail(TaskGraphValidationReason.UNKNOWN_TOOL, tool_err, topo=topo)

    indep_err = _validate_independence(graph)
    if indep_err:
        return _fail(TaskGraphValidationReason.INDEPENDENCE_VIOLATION, indep_err, topo=topo)

    budget_result = _validate_budget(graph, ctx)
    if budget_result is not None:
        budget_result.topo_order = topo
        return budget_result

    routing_err = _validate_routing(graph, ctx)
    if routing_err:
        return _fail(TaskGraphValidationReason.ROUTING_VIOLATION, routing_err, topo=topo)

    solver_err, solver_reason = _validate_solver_policy(graph)
    if solver_err:
        return _fail(solver_reason, solver_err, topo=topo)

    contract_err = _validate_contract_binding(graph, ctx)
    if contract_err:
        return _fail(
            TaskGraphValidationReason.CONTRACT_BINDING_VIOLATION,
            contract_err,
            topo=topo,
        )

    return TaskGraphValidationResult(
        ok=True,
        reason=TaskGraphValidationReason.OK,
        errors=[],
        topo_order=topo,
        graph_hash=task_graph_hash(graph),
    )


def _required_fields(task: TaskSpec) -> list[str]:
    errors: list[str] = []
    if not task.task_id:
        errors.append("task_id is required")
    if not (task.objective or "").strip():
        errors.append(f"{task.task_id or '?'}: objective is required")
    if not (task.output_schema or "").strip():
        errors.append(f"{task.task_id}: output_schema is required")
    return errors


def _validate_inputs(graph: TaskGraph, ctx: TaskGraphValidationContext) -> list[str]:
    id_set = {t.task_id for t in graph.tasks}
    allowed_static = ctx.known_artifact_ids | ctx.extra_input_ids
    errors: list[str] = []
    for task in graph.tasks:
        dep_set = set(task.depends_on)
        for ref in task.inputs:
            if ref in allowed_static:
                continue
            if ref in id_set:
                # Future/other task output is only legal if declared as a dependency.
                if ref not in dep_set:
                    errors.append(
                        f"{task.task_id} input {ref!r} is a task id not listed in depends_on"
                    )
                continue
            errors.append(f"{task.task_id} references unknown input {ref!r}")
    return errors


def _validate_schemas(graph: TaskGraph) -> list[str]:
    errors: list[str] = []
    for task in graph.tasks:
        if task.output_schema not in OUTPUT_SCHEMAS:
            errors.append(f"{task.task_id}: unknown output_schema {task.output_schema!r}")
            continue
        if not schema_allows(task.output_schema, role=task.role, kind=task.task_kind):
            errors.append(
                f"{task.task_id}: output_schema {task.output_schema!r} is not valid for "
                f"kind={task.task_kind.value} role={task.role.value if task.role else None}"
            )
    return errors


def _validate_tools(graph: TaskGraph, ctx: TaskGraphValidationContext) -> list[str]:
    errors: list[str] = []
    for task in graph.tasks:
        for name in task.allowed_tools:
            if name not in ctx.known_tool_names:
                errors.append(f"{task.task_id}: unknown tool {name!r}")
    return errors


def _validate_independence(graph: TaskGraph) -> list[str]:
    """Verification ∥ RedTeam must stay blind of author workspace and of each other."""
    errors: list[str] = []
    by_id = {t.task_id: t for t in graph.tasks}
    author_ids = {t.task_id for t in graph.tasks if t.role in AUTHOR_ROLES}
    review_tasks = [t for t in graph.tasks if t.role in REVIEW_ROLES]
    v_tasks = [t for t in review_tasks if t.role == AgentRole.VERIFICATION]
    rt_tasks = [t for t in review_tasks if t.role == AgentRole.RED_TEAM]
    review_ids = {t.task_id for t in review_tasks}

    for task in review_tasks:
        for dep in task.depends_on:
            other = by_id[dep]
            if dep in author_ids:
                errors.append(
                    f"{task.task_id} must not depend on author task {dep} "
                    "(independent review sees only ReviewBundle / checks)"
                )
            if other.role in REVIEW_ROLES and other.task_id != task.task_id:
                errors.append(
                    f"{task.task_id} must not depend on peer review task {dep} "
                    "(Verification ∥ RedTeam, never a chain)"
                )
        for ref in task.inputs:
            if ref in author_ids:
                errors.append(f"{task.task_id} must not take author task {ref} as input")
            if ref in review_ids and ref != task.task_id:
                errors.append(f"{task.task_id} must not take peer review output {ref} as input")

    if v_tasks and rt_tasks:
        groups = {t.independence_group for t in v_tasks + rt_tasks}
        if None in groups or len(groups) != 1:
            errors.append(
                "verification and red_team must share a single independence_group "
                f"(expected {INDEPENDENT_REVIEW_GROUP!r})"
            )
        else:
            group = next(iter(groups))
            if group != INDEPENDENT_REVIEW_GROUP:
                errors.append(
                    f"review independence_group must be {INDEPENDENT_REVIEW_GROUP!r}, got {group!r}"
                )
    return errors


def _validate_budget(
    graph: TaskGraph, ctx: TaskGraphValidationContext
) -> TaskGraphValidationResult | None:
    budget = ctx.budget
    if budget is None:
        return None
    sum_agents = sum_tools = sum_tokens = 0
    sum_cost = 0.0
    for task in graph.tasks:
        a, t, tok, cost = default_budget_slice(task)
        if a > budget.max_agent_calls or t > budget.max_tool_calls:
            return _fail(
                TaskGraphValidationReason.INVALID_TASK_BUDGET,
                [
                    f"{task.task_id} budget_slice exceeds run caps "
                    f"(agent_calls={a}, tool_calls={t})"
                ],
            )
        if tok > budget.max_tokens or cost > budget.max_cost:
            return _fail(
                TaskGraphValidationReason.INVALID_TASK_BUDGET,
                [f"{task.task_id} budget_slice exceeds token/cost caps"],
            )
        sum_agents += a
        sum_tools += t
        sum_tokens += tok
        sum_cost += cost
    if (
        sum_agents > budget.max_agent_calls
        or sum_tools > budget.max_tool_calls
        or sum_tokens > budget.max_tokens
        or sum_cost > budget.max_cost
    ):
        return _fail(
            TaskGraphValidationReason.BUDGET_EXCEEDED,
            [
                f"Planned slices agent_calls={sum_agents}/{budget.max_agent_calls} "
                f"tool_calls={sum_tools}/{budget.max_tool_calls} "
                f"tokens={sum_tokens}/{budget.max_tokens} "
                f"cost={sum_cost}/{budget.max_cost}"
            ],
        )
    return None


def _validate_solver_policy(
    graph: TaskGraph,
) -> tuple[list[str], TaskGraphValidationReason]:
    """Planner may name a solver/spec only if it is in the trusted registry."""
    from ai_lab.simulation.load import TRUSTED_SPEC_IDS
    from ai_lab.simulation.registry import known_solver_ids

    allowed = known_solver_ids()
    errors: list[str] = []
    reason = TaskGraphValidationReason.UNKNOWN_SOLVER
    for task in graph.tasks:
        meta = task.metadata or {}
        solver = meta.get("solver") if "solver" in meta else meta.get("solver_id")
        if solver is not None:
            if not isinstance(solver, str) or solver not in allowed:
                errors.append(f"{task.task_id}: unknown solver {solver!r}")
                reason = TaskGraphValidationReason.UNKNOWN_SOLVER
        spec_id = meta.get("spec_id")
        if spec_id is not None:
            if not isinstance(spec_id, str) or spec_id not in TRUSTED_SPEC_IDS:
                errors.append(f"{task.task_id}: untrusted spec_id {spec_id!r}")
                reason = TaskGraphValidationReason.SOLVER_POLICY_VIOLATION
        for banned in ("host_path", "docker_args", "image", "network", "cwd", "command"):
            if banned in meta:
                errors.append(f"{task.task_id}: metadata forbids {banned}")
                reason = TaskGraphValidationReason.SOLVER_POLICY_VIOLATION
    return errors, reason


def _validate_routing(graph: TaskGraph, ctx: TaskGraphValidationContext) -> list[str]:
    """TaskGraph roles must be routable; independence policy is checked against models."""
    policy = ctx.routing_policy
    if policy is None:
        return []
    from ai_lab.llm.config import KNOWN_PROVIDER_IDS, IndependencePolicy
    from ai_lab.llm.independence import ArchitectureFlags
    from ai_lab.llm.policy import validate_routing_policy

    graph_roles = sorted(
        {t.role.value for t in graph.tasks if t.role is not None}
    )
    available = ctx.available_providers or KNOWN_PROVIDER_IDS
    indep = ctx.independence_policy or IndependencePolicy()
    result = validate_routing_policy(
        policy,
        available,
        roles=graph_roles,
        independence_policy=indep,
        architecture=ArchitectureFlags(
            review_contexts_differ=True,
            frozen_blind_bundle=True,
            parallel_review=True,
        ),
    )
    return list(result.errors)


def _validate_contract_binding(
    graph: TaskGraph, ctx: TaskGraphValidationContext
) -> list[str]:
    """Reject cross-version / orphan tasks when an EngineeringContract is in force."""
    return contract_binding_errors(
        graph,
        contract_version=ctx.contract_version,
        contract_status=ctx.contract_status,
        investigation_id=ctx.investigation_id,
        required_outputs=list(ctx.required_outputs or []),
        require_calculation_producers=ctx.require_calculation_producers,
    )
