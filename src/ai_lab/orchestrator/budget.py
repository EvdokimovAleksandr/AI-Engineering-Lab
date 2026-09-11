"""Run budget enforcement — hard stop on resource exhaustion."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_lab.core.models import LabConfig, RunBudget


class BudgetExceeded(RuntimeError):
    """Raised when a run exceeds configured resource limits."""


def budget_from_config(config: LabConfig) -> RunBudget:
    rt = config.runtime or {}
    budget_cfg = dict(rt.get("budget") or {})
    return RunBudget(
        max_iterations=int(budget_cfg.get("max_iterations", rt.get("max_iterations", 24))),
        max_agent_calls=int(budget_cfg.get("max_agent_calls", 100)),
        max_tool_calls=int(budget_cfg.get("max_tool_calls", 200)),
        max_runtime_seconds=float(budget_cfg.get("max_runtime_seconds", 3600)),
        max_tokens=int(budget_cfg.get("max_tokens", 500_000)),
        max_cost=float(budget_cfg.get("max_cost", 50.0)),
        started_at=datetime.now(timezone.utc),
    )


def check_budget(budget: RunBudget) -> None:
    """Raise BudgetExceeded if any hard limit is crossed."""
    if budget.agent_calls > budget.max_agent_calls:
        raise BudgetExceeded(
            f"max_agent_calls exceeded: {budget.agent_calls}>{budget.max_agent_calls}"
        )
    if budget.tool_calls > budget.max_tool_calls:
        raise BudgetExceeded(
            f"max_tool_calls exceeded: {budget.tool_calls}>{budget.max_tool_calls}"
        )
    if budget.tokens_used > budget.max_tokens:
        raise BudgetExceeded(f"max_tokens exceeded: {budget.tokens_used}>{budget.max_tokens}")
    if budget.cost_used > budget.max_cost:
        raise BudgetExceeded(f"max_cost exceeded: {budget.cost_used}>{budget.max_cost}")
    elapsed = (datetime.now(timezone.utc) - budget.started_at).total_seconds()
    if elapsed > budget.max_runtime_seconds:
        raise BudgetExceeded(
            f"max_runtime_seconds exceeded: {elapsed}>{budget.max_runtime_seconds}"
        )


def record_agent_call(budget: RunBudget, *, tokens: int = 0, cost: float = 0.0) -> None:
    budget.agent_calls += 1
    budget.tokens_used += tokens
    budget.cost_used += cost
    check_budget(budget)


def record_tool_call(budget: RunBudget) -> None:
    budget.tool_calls += 1
    check_budget(budget)
