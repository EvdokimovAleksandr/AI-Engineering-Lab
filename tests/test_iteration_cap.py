"""INSUFFICIENT_EVIDENCE must not re-enter the TaskGraph until the budget dies."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.core.enums import AdjudicationStatus, ProjectState, TaskStatus
from ai_lab.core.models import AdjudicationResult, LabConfig, ProjectSnapshot, TaskGraph
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.task_routing.profiles import standard_pipeline_tasks
from ai_lab.workflows.engine import WorkflowEngine

REPO = Path(__file__).resolve().parents[1]


def _runtime(tmp_path: Path) -> LabRuntime:
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
async def test_insufficient_evidence_leaves_synthesis_pending(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    _standard_ready(rt)
    engine = WorkflowEngine(
        ProjectSnapshot(
            project_name="proj",
            state=ProjectState.VERIFICATION,
            adjudication_status=AdjudicationStatus.INSUFFICIENT_EVIDENCE,
        )
    )
    rt._last_adjudication = AdjudicationResult(
        status=AdjudicationStatus.INSUFFICIENT_EVIDENCE,
        reasons=["no computation relevant to CalculationSpec"],
    )
    await rt._after_adjudication(engine, AdjudicationStatus.INSUFFICIENT_EVIDENCE)
    assert rt._task_graph is not None
    assert rt._task_graph.version == 1
    assert rt._task_statuses["synthesis"] == TaskStatus.PENDING
    assert engine.snapshot.iteration == 0


@pytest.mark.asyncio
async def test_fail_stops_after_reentry_cap(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    # Явный cap=1: при iteration>=1 re-entry запрещён (кроме REPLAN от контроллера).
    rt.config.runtime["max_graph_reentries"] = 1
    _standard_ready(rt)
    engine = WorkflowEngine(
        ProjectSnapshot(
            project_name="proj",
            state=ProjectState.VERIFICATION,
            adjudication_status=AdjudicationStatus.FAIL,
            iteration=1,
        )
    )
    rt._last_adjudication = AdjudicationResult(status=AdjudicationStatus.FAIL, reasons=["math"])
    await rt._after_adjudication(engine, AdjudicationStatus.FAIL)
    assert rt._task_graph is not None
    assert rt._task_graph.version == 1
    assert rt._task_statuses["synthesis"] == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_method_question_does_not_hit_max_iterations(tmp_path: Path) -> None:
    """Live sofa/height methods ran calculation→verify in a loop until 24>24."""
    root = tmp_path / "sofa"
    root.mkdir()
    (root / "problem.md").write_text(
        "предложите минимум два способа определения высоты, с которой требуется "
        "спустить диван, при наличии рулетки длиной 3 м и линейки длиной 30 см.",
        encoding="utf-8",
    )
    (root / "requirements.md").write_text("", encoding="utf-8")
    (root / "assumptions.md").write_text("", encoding="utf-8")
    store = ProjectStore(root)
    store.ensure_layout()
    raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    raw["provider"] = "mock"
    raw["runtime"]["hitl_on_disputed"] = False
    raw["runtime"]["max_iterations"] = 24
    rt = LabRuntime(
        store,
        LabConfig.model_validate(raw),
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
    )
    snap = await rt.run()
    assert snap.state != ProjectState.BUDGET_EXCEEDED
    assert rt.stop_reason is None or "max_iterations" not in (rt.stop_reason or "")
    assert snap.state in {ProjectState.COMPLETED, ProjectState.AWAITING_HUMAN}
