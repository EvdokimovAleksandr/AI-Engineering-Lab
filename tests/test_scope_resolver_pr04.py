"""PR-04: ScopeResolver problem kinds, HITL Required fields, TaskGraph contract binding."""

from __future__ import annotations

import pytest

from ai_lab.core.enums import (
    ContractStatus,
    LabErrorCode,
    ProblemKind,
    ScopeStatus,
    TaskGraphValidationReason,
)
from ai_lab.core.engineering_contract import (
    ContractError,
    ContractObjective,
    EngineeringContract,
    contract_from_investigation_scope,
    lock_contract,
    require_pipeline_allowed,
    stamp_task_graph_to_contract,
    transition_status,
)
from ai_lab.core.models import TaskGraph, TaskSpec
from ai_lab.orchestrator.scope import resolve_scope
from ai_lab.orchestrator.scope_resolver import classify_problem_kind
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph
from ai_lab.task_routing.profiles import simple_pipeline_tasks as profile_simple_tasks

ROD_CLOSED = (
    "Steel rod Ø10 mm, F=10 kN axial, diameter becomes d×2; find the stress ratio."
)
ROD_OPEN = "How to make the rod stronger?"
SILK = (
    "Исследуй промышленные методы производства искусственного паучьего шёлка "
    "и их ограничения при масштабировании."
)


def test_closed_numeric_rod_stress_ratio() -> None:
    assert classify_problem_kind(ROD_CLOSED) == ProblemKind.CLOSED_NUMERIC
    scope = resolve_scope(ROD_CLOSED)
    assert scope.problem_kind == ProblemKind.CLOSED_NUMERIC
    assert scope.status == ScopeStatus.SCOPE_RESOLVED
    assert "stress_ratio" in scope.required_outputs
    assert scope.required_fields == []
    contract = contract_from_investigation_scope(
        scope,
        project_id="investigation_rod",
        investigation_id="investigation_rod",
        run_id="run_rod",
    )
    assert contract.problem_kind == ProblemKind.CLOSED_NUMERIC
    assert contract.status == ContractStatus.READY
    require_pipeline_allowed(contract)


def test_open_ended_needs_clarification_blocks_pipeline() -> None:
    assert classify_problem_kind(ROD_OPEN) == ProblemKind.OPEN_ENDED
    scope = resolve_scope(ROD_OPEN)
    assert scope.problem_kind == ProblemKind.OPEN_ENDED
    assert scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
    assert "load_type" in scope.required_fields
    assert scope.clarification is not None
    # HITL спрашивает конкретный Required, а не generic «clarify the task».
    q = scope.clarification.question.lower()
    assert "load type" in q
    assert "axial" in q
    assert "clarify" not in q or "load" in q

    contract = contract_from_investigation_scope(
        scope,
        project_id="investigation_rod_open",
        investigation_id="investigation_rod_open",
        run_id="run_open",
    )
    assert contract.status == ContractStatus.NEEDS_CLARIFICATION
    assert contract.problem_kind == ProblemKind.OPEN_ENDED
    assert "load_type" in contract.required
    with pytest.raises(ContractError) as ei:
        require_pipeline_allowed(contract)
    assert ei.value.code == LabErrorCode.CONTRACT_NOT_READY


def test_research_review_silk_classification() -> None:
    assert classify_problem_kind(SILK) == ProblemKind.RESEARCH_REVIEW
    scope = resolve_scope(SILK)
    assert scope.problem_kind == ProblemKind.RESEARCH_REVIEW
    assert scope.status == ScopeStatus.SCOPE_RESOLVED
    assert scope.pipeline_hint == "research"
    contract = contract_from_investigation_scope(
        scope,
        project_id="investigation_silk",
        investigation_id="investigation_silk",
        run_id="run_silk",
    )
    assert contract.problem_kind == ProblemKind.RESEARCH_REVIEW
    assert contract.status == ContractStatus.READY


def test_hitl_asks_specific_required_field_not_generic() -> None:
    scope = resolve_scope(ROD_OPEN)
    assert scope.clarification is not None
    assert scope.clarification.options == ["axial", "bending", "combined"]
    assert scope.required_fields == ["load_type"]
    # Known/Unknown/Optional frame populated.
    assert "load_type" in scope.unknown_parameters
    assert "material" in scope.optional_fields
    assert scope.assumption_candidates


def _locked_contract() -> EngineeringContract:
    c = EngineeringContract(
        project_id="investigation_bind",
        investigation_id="investigation_bind",
        run_id="run_bind",
        version="1",
        status=ContractStatus.DRAFT,
        objective=ContractObjective(statement="Compute stress ratio"),
        required_outputs=[{"name": "stress_ratio", "dimension": "1"}],
        problem_kind=ProblemKind.CLOSED_NUMERIC,
    )
    return lock_contract(transition_status(c, ContractStatus.READY))


def test_taskgraph_validator_rejects_mismatched_contract_version_when_locked() -> None:
    locked = _locked_contract()
    tasks = profile_simple_tasks()
    # Stamp wrong version on one node — cross-domain contamination signal.
    bad_tasks = []
    for t in tasks:
        meta = dict(t.metadata or {})
        meta["contract_version"] = "1" if t.task_id != "calculation" else "99"
        meta["investigation_id"] = locked.investigation_id
        bad_tasks.append(t.model_copy(update={"metadata": meta}))
    graph = TaskGraph(
        graph_id="simple_pipeline",
        tasks=bad_tasks,
        metadata={
            "contract_version": "1",
            "investigation_id": locked.investigation_id,
        },
    )
    result = validate_task_graph(
        graph,
        TaskGraphValidationContext(
            contract_version=locked.version,
            contract_status=ContractStatus.LOCKED,
            investigation_id=locked.investigation_id,
            required_outputs=["stress_ratio"],
            require_calculation_producers=True,
        ),
    )
    assert result.ok is False
    assert result.reason == TaskGraphValidationReason.CONTRACT_BINDING_VIOLATION
    assert any("calculation" in e and "99" in e for e in result.errors)


def test_stamp_task_graph_binds_all_tasks() -> None:
    locked = _locked_contract()
    graph = TaskGraph(
        graph_id="simple_pipeline",
        tasks=profile_simple_tasks(),
        metadata={},
    )
    stamped = stamp_task_graph_to_contract(graph, locked)
    assert stamped.metadata["contract_version"] == "1"
    assert all(
        (t.metadata or {}).get("contract_version") == "1" for t in stamped.tasks
    )
    result = validate_task_graph(
        stamped,
        TaskGraphValidationContext(
            contract_version="1",
            contract_status=ContractStatus.LOCKED,
            investigation_id=locked.investigation_id,
            required_outputs=["stress_ratio"],
            require_calculation_producers=True,
        ),
    )
    assert result.ok, result.errors


def test_locked_orphan_task_without_binding_rejected() -> None:
    from ai_lab.core.enums import AgentRole

    graph = TaskGraph(
        graph_id="orphan_g",
        tasks=[
            TaskSpec(
                task_id="understanding",
                role=AgentRole.CHIEF_ENGINEER,
                objective="Formalize",
                inputs=["problem.md"],
                output_schema="problem_framing",
                metadata={},
            )
        ],
        metadata={},
    )
    result = validate_task_graph(
        graph,
        TaskGraphValidationContext(
            contract_version="1",
            contract_status=ContractStatus.LOCKED,
            investigation_id="investigation_bind",
        ),
    )
    assert result.ok is False
    assert result.reason == TaskGraphValidationReason.CONTRACT_BINDING_VIOLATION
