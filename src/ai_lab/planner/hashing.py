"""Deterministic TaskGraph hashing — independent of JSON key order and task-list order."""

from __future__ import annotations

from typing import Any

from ai_lab.core.models import TaskGraph, TaskSpec
from ai_lab.knowledge.hashing import sha256_json


def canonical_task(task: TaskSpec) -> dict[str, Any]:
    """Semantic fields only: changing objective/deps/role/schema/budget changes the hash."""
    slice_dump = None
    if task.budget_slice is not None:
        slice_dump = task.budget_slice.model_dump(mode="json")
    return {
        "task_id": task.task_id,
        "role": task.role.value if task.role else None,
        "task_kind": task.task_kind.value,
        "objective": task.objective,
        "inputs": sorted(task.inputs),
        "depends_on": sorted(task.depends_on),
        "output_schema": task.output_schema,
        "independence_group": task.independence_group,
        "budget_slice": slice_dump,
        "priority": task.priority,
        "allowed_tools": sorted(task.allowed_tools),
        "state_context": task.state_context.value if task.state_context else None,
    }


def canonical_graph(graph: TaskGraph) -> dict[str, Any]:
    tasks = sorted((canonical_task(t) for t in graph.tasks), key=lambda d: d["task_id"])
    return {
        "graph_id": graph.graph_id,
        "version": graph.version,
        "supersedes": graph.supersedes,
        "tasks": tasks,
    }


def task_graph_hash(graph: TaskGraph) -> str:
    return sha256_json(canonical_graph(graph))
