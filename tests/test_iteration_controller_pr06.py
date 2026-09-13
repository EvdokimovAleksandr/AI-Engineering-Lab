"""PR-06: IterationController — no-progress → REPLAN → STOP / ASK_USER."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.core.enums import (
    AdjudicationStatus,
    FailureClass,
    IterationAction,
    LabErrorCode,
    ProjectState,
    ResearchOutcome,
    TaskStatus,
)
from ai_lab.core.models import AdjudicationResult, LabConfig, ProjectSnapshot, TaskGraph
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.iteration_policy import (
    IterationController,
    IterationObservation,
    classify_failure,
)
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.task_routing.profiles import standard_pipeline_tasks
from ai_lab.workflows.engine import WorkflowEngine

REPO = Path(__file__).resolve().parents[1]


def _obs(
    *,
    missing: tuple[str, ...] = ("power", "efficiency"),
    coverage: float | None = 0.0,
    failure_class: FailureClass = FailureClass.VERIFICATION,
    status: AdjudicationStatus = AdjudicationStatus.FAIL,
    covered: tuple[str, ...] = (),
) -> IterationObservation:
    covered_n = len(covered)
    required_n = covered_n + len(missing)
    return IterationObservation(
        coverage_ratio=coverage,
        missing_outputs=missing,
        covered_outputs=covered,
        failure_class=failure_class,
        adjudication_status=status,
        known=list(covered),
        covered_count=covered_n if required_n else None,
        required_count=required_n if required_n else None,
    )


def test_three_identical_no_progress_replan_then_stop() -> None:
    """3 одинаковых итерации → REPLAN; ещё одна без прогресса → STOP (без бюджета)."""
    ctrl = IterationController(no_progress_limit=3, hitl_on_no_progress=False)
    same = _obs()

    d1 = ctrl.record_and_decide(same)
    assert d1.action == IterationAction.CONTINUE
    assert d1.failure_class == FailureClass.VERIFICATION

    d2 = ctrl.record_and_decide(_obs())
    assert d2.action == IterationAction.CONTINUE
    assert d2.progress is not None and d2.progress.no_progress is True

    d3 = ctrl.record_and_decide(_obs())
    assert d3.action == IterationAction.REPLAN
    assert d3.failure_class == FailureClass.VERIFICATION

    d4 = ctrl.record_and_decide(_obs())
    assert d4.action == IterationAction.STOP_INSUFFICIENT_EVIDENCE
    assert d4.failure_class == FailureClass.VERIFICATION
    assert d4.failure_class is not None


def test_coverage_improvement_allows_continue() -> None:
    ctrl = IterationController(no_progress_limit=3)
    ctrl.record_and_decide(
        _obs(missing=("power", "efficiency"), coverage=0.0, covered=())
    )
    # Сузили missing + вырос coverage → прогресс, streak сброшен.
    d = ctrl.record_and_decide(
        _obs(
            missing=("efficiency",),
            coverage=0.5,
            covered=("power",),
        )
    )
    assert d.action == IterationAction.CONTINUE
    assert d.progress is not None
    assert d.progress.no_progress is False
    assert "power" in d.progress.gaps_closed
    assert "0/2 → 1/2" in d.progress.coverage_display or "0.00 → 0.50" in (
        d.progress.coverage_display
    )


def test_hitl_on_no_progress_asks_user() -> None:
    ctrl = IterationController(no_progress_limit=2, hitl_on_no_progress=True)
    ctrl.record_and_decide(_obs())
    assert ctrl.record_and_decide(_obs()).action == IterationAction.REPLAN
    assert ctrl.record_and_decide(_obs()).action == IterationAction.ASK_USER


def test_classify_failure_mappings() -> None:
    assert (
        classify_failure(error_code=LabErrorCode.CONTEXT_MISMATCH)
        == FailureClass.CONTEXT
    )
    assert (
        classify_failure(error_code=LabErrorCode.CONTRACT_NOT_READY)
        == FailureClass.SCOPE
    )
    assert (
        classify_failure(research_outcome=ResearchOutcome.RESEARCH_EMPTY)
        == FailureClass.RESEARCH
    )
    assert (
        classify_failure(research_outcome=ResearchOutcome.RESEARCH_PROVIDER_ERROR)
        == FailureClass.PROVIDER
    )
    assert classify_failure(budget_exceeded=True) == FailureClass.BUDGET
    assert (
        classify_failure(reason_texts=["dimension mismatch on stress"])
        == FailureClass.UNIT
    )


def _runtime(tmp_path: Path, **runtime_overrides: object) -> LabRuntime:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "problem.md").write_text("sofa height methods", encoding="utf-8")
    (root / "requirements.md").write_text("", encoding="utf-8")
    (root / "assumptions.md").write_text("", encoding="utf-8")
    store = ProjectStore(root)
    store.ensure_layout()
    raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    raw["provider"] = "mock"
    raw["runtime"]["hitl_on_disputed"] = False
    for key, value in runtime_overrides.items():
        raw["runtime"][key] = value
    return LabRuntime(
        store,
        LabConfig.model_validate(raw),
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
    )


def _standard_ready(rt: LabRuntime) -> None:
    graph = TaskGraph(graph_id="standard_pipeline", tasks=standard_pipeline_tasks(), version=1)
    rt._task_graph = graph
    rt._task_statuses = {t.task_id: TaskStatus.SUCCESS for t in graph.tasks}
    rt._task_statuses["synthesis"] = TaskStatus.PENDING


@pytest.mark.asyncio
async def test_stop_insufficient_sets_engineering_outcome(tmp_path: Path) -> None:
    """STOP_INSUFFICIENT_EVIDENCE пишет честный engineering_outcome, не PASS."""
    rt = _runtime(tmp_path)
    ctrl = IterationController(no_progress_limit=2, hitl_on_no_progress=False)
    # Две одинаковые итерации уже «сожгли» лимит → REPLAN выдан; следующий no-progress = STOP.
    same = _obs(missing=("power",), coverage=0.0, failure_class=FailureClass.CALCULATION)
    assert ctrl.record_and_decide(same).action == IterationAction.CONTINUE
    assert ctrl.record_and_decide(
        _obs(missing=("power",), coverage=0.0, failure_class=FailureClass.CALCULATION)
    ).action == IterationAction.REPLAN
    rt._iteration_controller = ctrl
    _standard_ready(rt)

    rt._last_adjudication = AdjudicationResult(
        status=AdjudicationStatus.FAIL,
        reasons=["mathcheck delta"],
        evidence_completeness={
            "coverage_ratio": 0.0,
            "missing_outputs": ["power"],
            "covered_outputs": [],
        },
        engineering_outcome=AdjudicationStatus.FAIL,
    )
    engine = WorkflowEngine(
        ProjectSnapshot(
            project_name="proj",
            state=ProjectState.VERIFICATION,
            adjudication_status=AdjudicationStatus.FAIL,
            iteration=0,
        )
    )
    await rt._after_adjudication(engine, AdjudicationStatus.FAIL)

    assert rt._last_adjudication is not None
    assert rt._last_adjudication.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE
    assert (
        rt._last_adjudication.engineering_outcome
        == AdjudicationStatus.INSUFFICIENT_EVIDENCE
    )
    assert rt._last_iteration_decision is not None
    assert (
        rt._last_iteration_decision.action
        == IterationAction.STOP_INSUFFICIENT_EVIDENCE
    )
    assert rt._last_iteration_decision.failure_class is not None
    assert engine.state != ProjectState.BUDGET_EXCEEDED
    # Graph не ревизился — остановились до re-entry.
    assert rt._task_graph is not None
    assert rt._task_graph.version == 1
