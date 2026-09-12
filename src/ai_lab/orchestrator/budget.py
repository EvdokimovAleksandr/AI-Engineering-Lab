"""Run budget enforcement — hard stop on resource exhaustion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_lab.core.models import LabConfig, RunBudget
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


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


def budget_violation_message(budget: RunBudget, *, include_runtime: bool = True) -> str | None:
    """Same checks as ``check_budget``, as a string for UI/lifecycle (never hide the reason).

    ``include_runtime=False`` when reconstructing from a saved manifest: wall-clock
    must not be re-evaluated hours later.
    """
    if budget.agent_calls > budget.max_agent_calls:
        return f"max_agent_calls exceeded: {budget.agent_calls}>{budget.max_agent_calls}"
    if budget.tool_calls > budget.max_tool_calls:
        return f"max_tool_calls exceeded: {budget.tool_calls}>{budget.max_tool_calls}"
    # Unknown usage is not treated as zero — skip token cap until usage is known.
    if budget.tokens_used is not None and budget.tokens_used > budget.max_tokens:
        return f"max_tokens exceeded: {budget.tokens_used}>{budget.max_tokens}"
    if budget.cost_used > budget.max_cost:
        return f"max_cost exceeded: {budget.cost_used}>{budget.max_cost}"
    if include_runtime:
        elapsed = (datetime.now(timezone.utc) - budget.started_at).total_seconds()
        if elapsed > budget.max_runtime_seconds:
            return f"max_runtime_seconds exceeded: {elapsed}>{budget.max_runtime_seconds}"
    return None


def check_budget(budget: RunBudget) -> None:
    """Raise BudgetExceeded if any hard limit is crossed."""
    msg = budget_violation_message(budget)
    if msg:
        raise BudgetExceeded(msg)


def record_agent_call(budget: RunBudget, *, tokens: int = 0, cost: float = 0.0) -> None:
    budget.agent_calls += 1
    if tokens and budget.tokens_used is not None:
        budget.tokens_used += tokens
    budget.cost_used += cost
    check_budget(budget)


def record_tool_call(budget: RunBudget) -> None:
    budget.tool_calls += 1
    check_budget(budget)


def total_tokens_from_usage(usage: dict[str, Any] | None) -> int | None:
    """Extract total tokens. Missing usage → None (unknown), never silent 0."""
    if not usage:
        return None
    if usage.get("total_tokens") is not None:
        try:
            return int(usage["total_tokens"])
        except (TypeError, ValueError):
            logger.error("Invalid total_tokens in usage: %r", usage.get("total_tokens"))
            return None
    inn = usage.get("input_tokens", usage.get("prompt_tokens"))
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    if inn is None or out is None:
        return None
    try:
        return int(inn) + int(out)
    except (TypeError, ValueError):
        logger.error("Invalid input/output tokens in usage: %r", usage)
        return None


def record_llm_usage(budget: RunBudget, usage: dict[str, Any] | None) -> None:
    """Update RunBudget from provider usage at LLM completion time.

    Unknown usage sets tokens_unknown and tokens_used=None — never coerce to 0.
    """
    total = total_tokens_from_usage(usage)
    if total is None:
        if not budget.tokens_unknown:
            logger.error(
                "LLM completion reported no usable token usage; "
                "marking RunBudget.tokens_used as unknown (not zero)"
            )
        budget.tokens_unknown = True
        budget.tokens_used = None
        return
    if budget.tokens_unknown or budget.tokens_used is None:
        # Once unknown, keep unknown — do not invent a partial total.
        budget.tokens_unknown = True
        budget.tokens_used = None
        return
    budget.tokens_used += total
    check_budget(budget)
