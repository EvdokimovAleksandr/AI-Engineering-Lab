"""Proposal → parse → deterministic validate. LLM never sets ok=True."""

from __future__ import annotations

from ai_lab.core.models import (
    TaskGraph,
    TaskGraphProposal,
    TaskGraphValidationResult,
)
from ai_lab.planner.context import ProblemContext
from ai_lab.planner.proposal import ProposalError, invalid_from_proposal_error, parse_proposal
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph


async def plan_and_validate(
    planner: object,
    context: ProblemContext,
    *,
    validation_context: TaskGraphValidationContext | None = None,
) -> tuple[TaskGraphProposal | None, TaskGraph | None, TaskGraphValidationResult]:
    """Run planner.propose then parse+validate. Invalid plans are not executed."""
    try:
        proposal = await planner.propose(context)  # type: ignore[attr-defined]
    except ProposalError as exc:
        return None, None, invalid_from_proposal_error(exc)

    try:
        graph = parse_proposal(proposal if isinstance(proposal, dict) else proposal)
    except ProposalError as exc:
        prop = proposal if isinstance(proposal, TaskGraphProposal) else None
        return prop, None, invalid_from_proposal_error(exc)

    vctx = validation_context or TaskGraphValidationContext(budget=context.budget)
    result = validate_task_graph(graph, vctx)
    return proposal, graph, result
