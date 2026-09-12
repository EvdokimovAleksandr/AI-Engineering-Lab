"""TaskGraph revision for IterationPolicy re-entry."""

from __future__ import annotations

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import TaskGraph
from ai_lab.planner.iteration import graph_for_iteration
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph
from ai_lab.task_routing.profiles import simple_pipeline_tasks


def test_graph_for_iteration_prunes_deps_to_sliced_tasks() -> None:
    """SIMPLE: calculation depends on understanding; re-entry at CALCULATION must not keep that edge."""
    previous = TaskGraph(graph_id="g_simple", tasks=simple_pipeline_tasks(), version=1)
    revised = graph_for_iteration(
        previous, ProjectState.CALCULATION, reason="adjudication:FAIL"
    )
    ids = {t.task_id for t in revised.tasks}
    assert "understanding" not in ids
    assert "calculation" in ids
    calc = next(t for t in revised.tasks if t.task_id == "calculation")
    assert "understanding" not in calc.depends_on
    assert "understanding" not in calc.inputs

    validation = validate_task_graph(revised, TaskGraphValidationContext())
    assert validation.ok, validation.errors


def test_graph_for_iteration_analysis_reentry_keeps_downstream() -> None:
    previous = TaskGraph(graph_id="g_simple", tasks=simple_pipeline_tasks(), version=1)
    revised = graph_for_iteration(
        previous, ProjectState.ANALYSIS, reason="adjudication:FAIL"
    )
    # SIMPLE has no ANALYSIS tasks; CALCULATION is after ANALYSIS in stage order,
    # so CALCULATION+ still remain.
    ids = {t.task_id for t in revised.tasks}
    assert "understanding" not in ids
    assert {"calculation", "deterministic_verify", "adjudication", "synthesis"} <= ids
    validation = validate_task_graph(revised, TaskGraphValidationContext())
    assert validation.ok, validation.errors
