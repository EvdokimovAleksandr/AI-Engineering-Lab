"""PR-04: ScopeResolver problem kinds, HITL Required fields, TaskGraph contract binding."""

from __future__ import annotations

from pathlib import Path

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
from ai_lab.orchestrator.scope import apply_clarification, resolve_scope
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


def test_design_requires_second_stage_scope() -> None:
    """После load_type=axial kind=DESIGN, но второй этап держит Required."""
    prior = resolve_scope(ROD_OPEN)
    assert prior.problem_kind == ProblemKind.OPEN_ENDED
    updated = apply_clarification(prior, choice="axial")
    scope = resolve_scope(updated.original_problem, prior=updated)
    assert scope.problem_kind == ProblemKind.DESIGN
    assert scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
    assert scope.required_fields, "DESIGN second stage must keep non-empty Required"
    assert "load_type" not in scope.required_fields
    assert scope.known_parameters.get("load_type") == "axial"
    # strength_metric / geometry / design_constraint — блокируют READY.
    assert set(scope.required_fields) >= {"strength_metric", "geometry", "design_constraint"}


def test_axial_answer_does_not_complete_design_contract() -> None:
    """Один ответ axial не делает контракт READY/LOCKED."""
    prior = resolve_scope(ROD_OPEN)
    updated = apply_clarification(prior, choice="axial")
    scope = resolve_scope(updated.original_problem, prior=updated)
    contract = contract_from_investigation_scope(
        scope,
        project_id="investigation_rod_axial",
        investigation_id="investigation_rod_axial",
        run_id="run_axial",
    )
    assert contract.problem_kind == ProblemKind.DESIGN
    assert contract.status == ContractStatus.NEEDS_CLARIFICATION
    assert contract.required, "DESIGN required must remain after axial"
    with pytest.raises(ContractError) as ei:
        require_pipeline_allowed(contract)
    assert ei.value.code == LabErrorCode.CONTRACT_NOT_READY
    # Даже принудительный READY → LOCKED должен падать на missing Required.
    draft = contract.model_copy(update={"status": ContractStatus.DRAFT})
    with pytest.raises(ContractError):
        transition_status(draft, ContractStatus.READY)


def test_required_fields_are_not_cleared_on_resume() -> None:
    """Resume с axial не обнуляет required_fields ([] запрещён на этом шаге)."""
    prior = resolve_scope(ROD_OPEN)
    assert prior.required_fields == ["load_type"]
    updated = apply_clarification(prior, choice="axial")
    scope = resolve_scope(updated.original_problem, prior=updated)
    assert scope.required_fields != []
    assert len(scope.required_fields) >= 1
    # Optional не должен поглотить бывшие unknown (strength_metric).
    assert "strength_metric" not in (scope.optional_fields or [])
    assert "strength_metric" in (scope.required_fields or [])


@pytest.mark.asyncio
async def test_open_ended_design_cannot_reach_engineering_pass_after_one_answer(
    tmp_path: Path,
) -> None:
    """E2E: после одного HITL (axial) нет LOCKED / engineering PASS / финального расчёта."""
    import json

    import yaml

    from ai_lab.core.enums import ProjectState
    from ai_lab.core.models import LabConfig
    from ai_lab.memory.project_store import ProjectStore
    from ai_lab.orchestrator.hitl import HitlDecision, HitlGate
    from ai_lab.orchestrator.runtime import LabRuntime

    def _config() -> LabConfig:
        raw = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config" / "default.yaml").read_text(
                encoding="utf-8"
            )
        )
        raw["provider"] = "mock"
        raw["runtime"]["hitl_on_disputed"] = False
        return LabConfig.model_validate(raw)

    root = tmp_path / "rod_open_e2e"
    root.mkdir()
    (root / "problem.md").write_text(ROD_OPEN + "\n", encoding="utf-8")
    (root / "requirements.md").write_text("", encoding="utf-8")
    (root / "assumptions.md").write_text("", encoding="utf-8")
    store = ProjectStore(root)
    store.ensure_layout()

    first = LabRuntime(
        store, _config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False)
    )
    snap1 = await first.run()
    assert snap1.state == ProjectState.AWAITING_HUMAN
    run_id = snap1.run_id

    second = LabRuntime(
        store,
        _config(),
        repo_root=tmp_path,
        hitl=HitlGate(pending_decision=HitlDecision(approved=True, choice="axial")),
        resume_run_id=run_id,
    )
    snap2 = await second.run()
    assert snap2.run_id == run_id
    run_dir = store.root / ".runs" / run_id
    scope = json.loads((run_dir / "planner" / "scope.json").read_text(encoding="utf-8"))
    contract = json.loads(
        (run_dir / "planner" / "engineering_contract.json").read_text(encoding="utf-8")
    )
    assert scope.get("problem_kind") == "DESIGN"
    assert scope.get("status") == "SCOPE_NEEDS_CLARIFICATION"
    assert scope.get("required_fields")
    assert contract.get("status") == "NEEDS_CLARIFICATION"
    assert contract.get("required")
    assert snap2.state == ProjectState.AWAITING_HUMAN
    adj_path = run_dir / "reviews" / "last_adjudication.json"
    if adj_path.is_file():
        adj = json.loads(adj_path.read_text(encoding="utf-8"))
        outcome = adj.get("engineering_outcome") or adj.get("status")
        assert outcome not in {"PASS", "pass"}
    comps_dir = run_dir / "computations"
    comps = list(comps_dir.glob("comp_*.json")) if comps_dir.is_dir() else []
    assert comps == []