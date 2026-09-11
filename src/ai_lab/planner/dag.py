"""Deterministic DAG helpers for TaskGraph — order-independent of the task list."""

from __future__ import annotations

from collections import defaultdict

from ai_lab.core.enums import TaskStatus
from ai_lab.core.models import TaskSpec


class CycleError(ValueError):
    """Graph is not a DAG."""


def _adjacency(tasks: list[TaskSpec]) -> tuple[dict[str, TaskSpec], dict[str, set[str]], dict[str, int]]:
    by_id = {t.task_id: t for t in tasks}
    dependents: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {t.task_id: 0 for t in tasks}
    for task in tasks:
        seen: set[str] = set()
        for dep in task.depends_on:
            if dep in seen:
                continue
            seen.add(dep)
            indegree[task.task_id] = indegree.get(task.task_id, 0) + 1
            dependents[dep].add(task.task_id)
    return by_id, dependents, indegree


def topological_order(tasks: list[TaskSpec]) -> list[str]:
    """Kahn sort. Ready ties: higher priority first, then task_id.

    List order of `tasks` must not affect the result.
    """
    by_id, dependents, indegree = _adjacency(tasks)
    ready = {tid for tid, deg in indegree.items() if deg == 0}
    order: list[str] = []
    while ready:
        # Deterministic pick among currently ready: higher priority, then task_id.
        current = min(ready, key=lambda tid: (-by_id[tid].priority, tid))
        ready.remove(current)
        order.append(current)
        for child in dependents[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.add(child)
    if len(order) != len(tasks):
        leftover = sorted(tid for tid, deg in indegree.items() if deg > 0)
        raise CycleError(f"Cycle involving tasks: {leftover}")
    return order


def ready_task_ids(
    tasks: list[TaskSpec],
    statuses: dict[str, TaskStatus],
) -> list[str]:
    """Tasks whose dependencies are SUCCESS, themselves still PENDING."""
    by_id = {t.task_id: t for t in tasks}
    ready: list[str] = []
    for task in tasks:
        status = statuses.get(task.task_id, TaskStatus.PENDING)
        if status != TaskStatus.PENDING:
            continue
        deps_ok = True
        for dep in task.depends_on:
            if statuses.get(dep, TaskStatus.PENDING) != TaskStatus.SUCCESS:
                deps_ok = False
                break
        if deps_ok:
            ready.append(task.task_id)
    ready.sort(key=lambda tid: (-by_id[tid].priority, tid))
    return ready
