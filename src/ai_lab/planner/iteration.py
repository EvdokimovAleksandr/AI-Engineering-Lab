"""IterationPolicy → TaskGraph revision (extension point, not a second policy)."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import TaskGraph, TaskSpec
from ai_lab.planner.static import default_pipeline_tasks

# Stage order used only to slice the static backbone for re-entry.
_STAGE_ORDER = [
    ProjectState.UNDERSTANDING,
    ProjectState.DECOMPOSITION,
    ProjectState.RESEARCH,
    ProjectState.HYPOTHESIS,
    ProjectState.ANALYSIS,
    ProjectState.CALCULATION,
    ProjectState.SIMULATION,
    ProjectState.VERIFICATION,
    ProjectState.SYNTHESIS,
]


def graph_for_iteration(
    previous: TaskGraph,
    reentry: ProjectState,
    *,
    reason: str,
) -> TaskGraph:
    """New graph version starting at IterationPolicy's target stage.

    Does not invent a second iteration mechanism — only materializes the
    existing policy as TaskGraph vN (supersedes previous graph_id@version).
    """
    if reentry not in _STAGE_ORDER:
        raise ValueError(f"Unsupported iteration re-entry state: {reentry}")
    start = _STAGE_ORDER.index(reentry)
    allowed = set(_STAGE_ORDER[start:])
    # Prefer tasks from the previous graph so custom plans can iterate too.
    source = previous.tasks or default_pipeline_tasks()
    kept: list[TaskSpec] = []
    for task in source:
        stage = task.state_context
        if stage is None:
            kept.append(task.model_copy(deep=True))
            continue
        if stage in allowed or (
            stage in {ProjectState.RED_TEAM, ProjectState.VERIFICATION}
            and ProjectState.VERIFICATION in allowed
        ):
            kept.append(task.model_copy(deep=True))
    if not kept:
        raise ValueError(f"No tasks remain for re-entry at {reentry.value}")
    return TaskGraph(
        graph_id=previous.graph_id,
        tasks=kept,
        version=previous.version + 1,
        supersedes=f"{previous.graph_id}@v{previous.version}",
        reason=reason,
        created_at=datetime.now(timezone.utc),
        metadata={
            **dict(previous.metadata or {}),
            "iteration_reentry": reentry.value,
        },
    )
