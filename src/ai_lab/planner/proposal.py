"""Parse untrusted LLM JSON into TaskGraphProposal → TaskGraph.

LLM output is never authoritative: extra keys, unknown roles, and command-like
fields fail here before LabRuntime sees a graph.

Unknown roles are rejected, never remapped to a "closest" AgentRole.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ai_lab.core.enums import AgentRole, TaskGraphValidationReason, TaskKind
from ai_lab.core.models import TaskGraph, TaskGraphProposal, TaskGraphValidationResult, TaskSpec
from ai_lab.observability.logger import get_logger
from ai_lab.planner.schemas import (
    FORBIDDEN_PROPOSAL_KEYS,
    allowed_output_schema_values,
    allowed_role_values,
    allowed_task_kind_values,
)

logger = get_logger(__name__)

# Truncate diagnostics so events/UI never carry huge untrusted blobs.
_MAX_ERROR_CHARS = 400
_MAX_ERRORS = 32


class ProposalError(ValueError):
    """Structured proposal could not be parsed into a TaskGraph."""

    def __init__(self, reason: TaskGraphValidationReason, errors: list[str]) -> None:
        super().__init__("; ".join(errors) or reason.value)
        self.reason = reason
        self.errors = errors


def sanitize_planner_errors(errors: list[str]) -> list[str]:
    """Keep structured validation text; drop overlong untrusted payloads."""
    out: list[str] = []
    for raw in errors:
        text = " ".join(str(raw).split())
        if len(text) > _MAX_ERROR_CHARS:
            text = text[:_MAX_ERROR_CHARS] + "…"
        out.append(text)
        if len(out) >= _MAX_ERRORS:
            out.append(f"… {len(errors) - _MAX_ERRORS} more validation errors omitted")
            break
    return out


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


def _classify_taskspec_error(err: dict[str, Any]) -> TaskGraphValidationReason:
    loc = ".".join(str(x) for x in err.get("loc", ()))
    blob = f"{loc} {err.get('msg', '')} {err.get('type', '')}".lower()
    if "role" in blob or "agentrole" in blob:
        return TaskGraphValidationReason.UNKNOWN_ROLE
    if "task_kind" in blob or "taskkind" in blob:
        return TaskGraphValidationReason.UNKNOWN_TASK_KIND
    return TaskGraphValidationReason.MALFORMED_PROPOSAL


def _taskspec_error_line(index: int, err: dict[str, Any]) -> str:
    loc = ".".join(str(x) for x in err.get("loc", ())) or "?"
    msg = str(err.get("msg") or "invalid")
    return f"tasks[{index}].{loc}: {msg}"


def format_repair_diagnostics(errors: list[str]) -> str:
    """Structured retry payload: invalid values + allowed enums. No remapping advice."""
    roles = ", ".join(allowed_role_values())
    kinds = ", ".join(allowed_task_kind_values())
    schemas = ", ".join(allowed_output_schema_values())
    lines = [
        "Previous proposal failed validation. Return a new TaskGraphProposal.",
        "Do not invent roles or remap them yourself unless you choose a legal enum value.",
        f"Allowed roles: {roles}",
        f"Allowed task_kind: {kinds}",
        f"Allowed output_schema (string): {schemas}",
        "inputs must be an array of strings, never an object.",
        "budget_slice must be omitted or {max_agent_calls, max_tool_calls, max_tokens, max_cost}, never a number.",
        "Validation errors:",
    ]
    for item in sanitize_planner_errors(errors):
        lines.append(f"- {item}")
    return "\n".join(lines)


def parse_proposal(data: dict[str, Any] | TaskGraphProposal) -> TaskGraph:
    """Validate proposal schema, reject forbidden keys, build a TaskGraph.

    Collects diagnostics from every task. Does not remap unknown roles.
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
        raise ProposalError(
            TaskGraphValidationReason.FORBIDDEN_FIELD,
            sanitize_planner_errors(forbidden),
        )

    try:
        proposal = TaskGraphProposal.model_validate(raw)
    except ValidationError as exc:
        raise ProposalError(
            TaskGraphValidationReason.MALFORMED_PROPOSAL,
            sanitize_planner_errors([e["msg"] for e in exc.errors()]),
        ) from exc

    tasks: list[TaskSpec] = []
    messages: list[str] = []
    reasons: list[TaskGraphValidationReason] = []
    for i, item in enumerate(proposal.tasks):
        if not isinstance(item, dict):
            messages.append(f"tasks[{i}] must be an object")
            reasons.append(TaskGraphValidationReason.MALFORMED_PROPOSAL)
            continue
        # Record invented role names explicitly — never coerce to AgentRole.
        role_raw = item.get("role")
        if isinstance(role_raw, str) and role_raw not in {r.value for r in AgentRole}:
            messages.append(f"tasks[{i}].role: unknown role {role_raw!r}")
            reasons.append(TaskGraphValidationReason.UNKNOWN_ROLE)
        kind_raw = item.get("task_kind")
        if kind_raw is not None and isinstance(kind_raw, str) and kind_raw not in {k.value for k in TaskKind}:
            messages.append(f"tasks[{i}].task_kind: unknown task_kind {kind_raw!r}")
            reasons.append(TaskGraphValidationReason.UNKNOWN_TASK_KIND)
        try:
            tasks.append(TaskSpec.model_validate(item))
        except ValidationError as exc:
            for err in exc.errors():
                reasons.append(_classify_taskspec_error(err))
                messages.append(_taskspec_error_line(i, err))

    if messages:
        unique_msgs = list(dict.fromkeys(messages))
        if TaskGraphValidationReason.UNKNOWN_ROLE in reasons:
            reason = TaskGraphValidationReason.UNKNOWN_ROLE
        elif TaskGraphValidationReason.UNKNOWN_TASK_KIND in reasons:
            reason = TaskGraphValidationReason.UNKNOWN_TASK_KIND
        else:
            reason = TaskGraphValidationReason.MALFORMED_PROPOSAL
        raise ProposalError(reason, sanitize_planner_errors(unique_msgs))

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
