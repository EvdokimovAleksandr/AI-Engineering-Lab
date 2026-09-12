"""Proposal → parse → deterministic validate. LLM never sets ok=True.

Recovery: one optional LLM retry with structured errors, then StaticPlanner.
The fallback graph is built from the original ProblemContext, never by
repairing the invalid LLM graph. Static and LLM graphs share one validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_lab.core.enums import TaskGraphValidationReason
from ai_lab.core.models import (
    PlannerResolution,
    TaskGraph,
    TaskGraphProposal,
    TaskGraphValidationResult,
)
from ai_lab.llm.errors import LLMProviderError, Timeout
from ai_lab.observability.logger import get_logger
from ai_lab.planner.context import ProblemContext
from ai_lab.planner.hashing import proposal_payload_hash
from ai_lab.planner.proposal import (
    ProposalError,
    invalid_from_proposal_error,
    parse_proposal,
    sanitize_planner_errors,
)
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph

logger = get_logger(__name__)

# First LLM attempt + at most one repair. Never spend the run budget on planner loops.
_MAX_LLM_ATTEMPTS = 2


def classify_planner_failure(
    reason: TaskGraphValidationReason | None,
    *,
    provider_error: BaseException | None = None,
) -> str | None:
    """Map onto existing failure kinds — not a second taxonomy."""
    if isinstance(provider_error, Timeout):
        return "timeout"
    if isinstance(provider_error, LLMProviderError):
        return "provider_error"
    if reason is None:
        return None
    if reason in {
        TaskGraphValidationReason.MALFORMED_PROPOSAL,
        TaskGraphValidationReason.UNKNOWN_ROLE,
        TaskGraphValidationReason.UNKNOWN_TASK_KIND,
        TaskGraphValidationReason.MISSING_FIELD,
    }:
        return "schema_invalid"
    if reason in {
        TaskGraphValidationReason.FORBIDDEN_FIELD,
        TaskGraphValidationReason.INDEPENDENCE_VIOLATION,
        TaskGraphValidationReason.ROUTING_VIOLATION,
        TaskGraphValidationReason.SOLVER_POLICY_VIOLATION,
        TaskGraphValidationReason.UNKNOWN_SOLVER,
        TaskGraphValidationReason.UNKNOWN_TOOL,
    }:
        return "policy_invalid"
    if reason in {
        TaskGraphValidationReason.BUDGET_EXCEEDED,
        TaskGraphValidationReason.INVALID_TASK_BUDGET,
    }:
        return "budget_exceeded"
    return "taskgraph_invalid"


@dataclass
class PlanOutcome:
    """Validated graph plus planner provenance. graph is None when planning failed."""

    proposal: TaskGraphProposal | None
    graph: TaskGraph | None
    validation: TaskGraphValidationResult
    resolution: PlannerResolution
    rejected_proposal: TaskGraphProposal | dict[str, Any] | None = None
    rejected_validation: TaskGraphValidationResult | None = None


async def plan_and_validate(
    planner: object,
    context: ProblemContext,
    *,
    validation_context: TaskGraphValidationContext | None = None,
    repair_errors: list[str] | None = None,
) -> tuple[TaskGraphProposal | None, TaskGraph | None, TaskGraphValidationResult]:
    """Run planner.propose then parse+validate. Invalid plans are not executed."""
    try:
        proposal = await planner.propose(context, repair_errors=repair_errors)  # type: ignore[attr-defined]
    except LLMProviderError:
        raise
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


def _dump_proposal(proposal: TaskGraphProposal | dict[str, Any] | None) -> dict[str, Any] | None:
    if proposal is None:
        return None
    if isinstance(proposal, TaskGraphProposal):
        return proposal.model_dump(mode="json")
    if isinstance(proposal, dict):
        return proposal
    return None


async def plan_with_recovery(
    planner: object,
    context: ProblemContext,
    *,
    validation_context: TaskGraphValidationContext | None = None,
    fallback_planner: object | None = None,
    fallback_profile: str | None = None,
) -> PlanOutcome:
    """LLM/static propose → same validator → optional one retry → static recovery.

    Static recovery uses `context` (original problem + routing profile), not the
    invalid LLM graph. Provider outages are not reported as a successful LLM plan.
    """
    requested = str(getattr(planner, "name", "unknown") or "unknown")
    attempts = 0
    rejections = 0
    retry_count = 0
    rejected_proposal: TaskGraphProposal | dict[str, Any] | None = None
    rejected_validation: TaskGraphValidationResult | None = None
    failure_class: str | None = None
    provider_error: BaseException | None = None
    repair: list[str] | None = None

    llm_like = requested == "llm"
    max_attempts = _MAX_LLM_ATTEMPTS if llm_like else 1

    while attempts < max_attempts:
        attempts += 1
        try:
            proposal, graph, validation = await plan_and_validate(
                planner,
                context,
                validation_context=validation_context,
                repair_errors=repair,
            )
        except LLMProviderError as exc:
            logger.error("LLM planner provider failed (%s): %s", type(exc).__name__, exc)
            provider_error = exc
            failure_class = classify_planner_failure(None, provider_error=exc)
            rejections += 1
            rejected_validation = TaskGraphValidationResult(
                ok=False,
                reason=TaskGraphValidationReason.MALFORMED_PROPOSAL,
                errors=sanitize_planner_errors(
                    [f"planner provider error: {type(exc).__name__}"]
                ),
            )
            break

        if validation.ok and graph is not None:
            return PlanOutcome(
                proposal=proposal,
                graph=graph,
                validation=validation,
                resolution=PlannerResolution(
                    requested=requested,
                    accepted=True,
                    recovered=False,
                    retry_count=retry_count,
                    planner_attempts=attempts,
                    planner_rejections=rejections,
                    planner_fallbacks=0,
                    final_planner_type=requested,
                ),
            )

        rejections += 1
        rejected_proposal = proposal
        rejected_validation = validation
        failure_class = classify_planner_failure(validation.reason)
        logger.error(
            "Planner proposal rejected: %s %s",
            validation.reason.value,
            validation.errors,
        )
        if attempts < max_attempts:
            retry_count += 1
            repair = list(validation.errors)
            logger.error("Retrying LLM planner once with structured validation errors")

    rejected_hash = None
    dumped = _dump_proposal(rejected_proposal)
    if dumped is not None:
        rejected_hash = proposal_payload_hash(dumped)

    can_fallback = (
        fallback_planner is not None
        and requested == "llm"
        and getattr(fallback_planner, "name", None) == "static"
    )
    if not can_fallback:
        validation = rejected_validation or TaskGraphValidationResult(
            ok=False,
            reason=TaskGraphValidationReason.MALFORMED_PROPOSAL,
            errors=["planner produced no graph"],
        )
        return PlanOutcome(
            proposal=None,
            graph=None,
            validation=validation,
            resolution=PlannerResolution(
                requested=requested,
                accepted=False,
                recovered=False,
                retry_count=retry_count,
                planner_attempts=attempts,
                planner_rejections=rejections,
                planner_fallbacks=0,
                final_planner_type=requested,
                rejection_reason=(
                    rejected_validation.reason.value if rejected_validation else None
                ),
                failure_class=failure_class,
                validation_errors=list(
                    (rejected_validation.errors if rejected_validation else [])
                ),
                rejected_proposal_hash=rejected_hash,
            ),
            rejected_proposal=rejected_proposal,
            rejected_validation=rejected_validation,
        )

    # Rebuild from original problem + trusted profile. Never mutate the bad graph.
    logger.error(
        "LLM planner recovery: StaticPlanner profile=%s (provider_error=%s)",
        fallback_profile,
        type(provider_error).__name__ if provider_error else None,
    )
    fb_proposal, fb_graph, fb_validation = await plan_and_validate(
        fallback_planner,
        context,
        validation_context=validation_context,
    )
    fallbacks = 1
    if fb_validation.ok and fb_graph is not None:
        return PlanOutcome(
            proposal=fb_proposal,
            graph=fb_graph,
            validation=fb_validation,
            resolution=PlannerResolution(
                requested=requested,
                accepted=False,
                recovered=True,
                fallback="static",
                fallback_profile=fallback_profile,
                retry_count=retry_count,
                planner_attempts=attempts,
                planner_rejections=rejections,
                planner_fallbacks=fallbacks,
                final_planner_type="static",
                rejection_reason=(
                    rejected_validation.reason.value if rejected_validation else None
                ),
                failure_class=failure_class,
                validation_errors=list(
                    (rejected_validation.errors if rejected_validation else [])
                ),
                rejected_proposal_hash=rejected_hash,
            ),
            rejected_proposal=rejected_proposal,
            rejected_validation=rejected_validation,
        )

    logger.error(
        "Static fallback TaskGraph rejected by the same validator: %s %s",
        fb_validation.reason.value,
        fb_validation.errors,
    )
    return PlanOutcome(
        proposal=None,
        graph=None,
        validation=fb_validation,
        resolution=PlannerResolution(
            requested=requested,
            accepted=False,
            recovered=False,
            fallback="static",
            fallback_profile=fallback_profile,
            retry_count=retry_count,
            planner_attempts=attempts,
            planner_rejections=rejections + (0 if fb_validation.ok else 1),
            planner_fallbacks=fallbacks,
            final_planner_type="static",
            rejection_reason=fb_validation.reason.value,
            failure_class=classify_planner_failure(fb_validation.reason),
            validation_errors=list(fb_validation.errors),
            rejected_proposal_hash=rejected_hash,
        ),
        rejected_proposal=rejected_proposal,
        rejected_validation=rejected_validation,
    )
