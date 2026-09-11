"""Parse untrusted LLM JSON into TaskGraphProposal → TaskGraph.

LLM output is never authoritative: extra keys, unknown roles, and command-like
fields fail here before LabRuntime sees a graph.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ai_lab.core.enums import TaskGraphValidationReason
from ai_lab.core.models import TaskGraph, TaskGraphProposal, TaskGraphValidationResult, TaskSpec
from ai_lab.observability.logger import get_logger
from ai_lab.planner.schemas import FORBIDDEN_PROPOSAL_KEYS

logger = get_logger(__name__)


class ProposalError(ValueError):
    """Structured proposal could not be parsed into a TaskGraph."""

    def __init__(self, reason: TaskGraphValidationReason, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or reason.value)
        self.reason = reason
        self.errors = errors


def _scan_forbidden(obj: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            normalized = str(key).replace("-", "_").lower()
            child = f"{path}.{key}"
            if normalized in FORBIDDEN_PROPOSAL_KEYS:
                errors.append(f"Forbidden field {child}")
            errors.extend(_scan_forbidden(value, child))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            errors.extend(_scan_forbidden(value, f"{path}[{i}]"))
    return errors


def parse_proposal(data: dict[str, Any] | TaskGraphProposal) -> TaskGraph:
    """Validate proposal schema, reject forbidden keys, build a TaskGraph.

    Does not run DAG/budget validation — that is `validate_task_graph`.
    """
    raw: dict[str, Any]
    if isinstance(data, TaskGraphProposal):
        raw = data.model_dump(mode="json")
    elif isinstance(data, dict):
        raw = data
    else:
        raise ProposalError(
            TaskGraphValidationReason.MALFORMED_PROPOSAL,
            [f"Proposal must be an object, got {type(data).__name__}"],
        )

    forbidden = _scan_forbidden(raw)
    if forbidden:
        logger.error("Planner proposal contained forbidden fields: %s", forbidden)
        raise ProposalError(TaskGraphValidationReason.FORBIDDEN_FIELD, forbidden)

    try:
        proposal = TaskGraphProposal.model_validate(raw)
    except ValidationError as exc:
        raise ProposalError(
            TaskGraphValidationReason.MALFORMED_PROPOSAL,
            [e["msg"] for e in exc.errors()],
        ) from exc

    tasks: list[TaskSpec] = []
    for i, item in enumerate(proposal.tasks):
        if not isinstance(item, dict):
            raise ProposalError(
                TaskGraphValidationReason.MALFORMED_PROPOSAL,
                [f"tasks[{i}] must be an object"],
            )
        try:
            tasks.append(TaskSpec.model_validate(item))
        except ValidationError as exc:
            msgs = [e["msg"] for e in exc.errors()]
            locs = [".".join(str(x) for x in e.get("loc", ())) for e in exc.errors()]
            blob = " ".join(msgs + locs).lower()
            reason = TaskGraphValidationReason.MALFORMED_PROPOSAL
            if "role" in blob or "agentrole" in blob:
                reason = TaskGraphValidationReason.UNKNOWN_ROLE
            raise ProposalError(reason, [f"tasks[{i}]: {m}" for m in msgs]) from exc

    return TaskGraph(
        graph_id=proposal.graph_id,
        tasks=tasks,
        version=proposal.version,
        supersedes=proposal.supersedes,
        reason=proposal.reason,
        metadata=dict(proposal.metadata),
    )


def invalid_from_proposal_error(exc: ProposalError) -> TaskGraphValidationResult:
    return TaskGraphValidationResult(ok=False, reason=exc.reason, errors=exc.errors)
