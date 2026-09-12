"""Shared agent context and base helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_lab.core.enums import AgentRole
from ai_lab.core.models import AgentResult, LabConfig, LLMMessage, LLMRequest, TaskSpec
from ai_lab.memory.decision_log import DecisionLog
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.tracing import RunEventSink
from ai_lab.tools.registry import ToolRegistry


def coerce_str_list(values: Any, *, limit: int | None = None) -> list[str]:
    """Normalize LLM list fields to list[str].

    Real providers often return assumption/unknown objects like
    ``{"id": "...", "statement": "..."}``; Claim/DecisionRecord require strings.
    """
    if values is None:
        return []
    if isinstance(values, str):
        text = values.strip()
        return [text] if text else []
    if not isinstance(values, list):
        text = str(values).strip()
        return [text] if text else []

    out: list[str] = []
    for item in values:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(
                item.get("statement")
                or item.get("text")
                or item.get("assumption")
                or item.get("description")
                or item.get("unknown")
                or item.get("id")
                or ""
            ).strip()
        else:
            text = str(item).strip()
        if text:
            out.append(text)
        if limit is not None and len(out) >= limit:
            break
    return out


def coerce_optional_str(value: Any, *, limit: int = 8) -> str | None:
    """Normalize an LLM scalar that Claim stores as a single string.

    Live providers often emit ``evidence`` as a list of notes. ``dict``/list
    must not reach Pydantic ``str | None`` (that aborts the whole run).
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts = coerce_str_list(value, limit=limit)
        if not parts:
            return None
        return "; ".join(parts)
    if isinstance(value, dict):
        text = str(
            value.get("statement")
            or value.get("text")
            or value.get("evidence")
            or value.get("description")
            or ""
        ).strip()
        return text or None
    text = str(value).strip()
    return text or None


@dataclass
class AgentContext:
    """Runtime dependencies injected into every agent."""

    run_id: str
    store: ProjectStore
    evidence: EvidenceStore
    decisions: DecisionLog
    tools: ToolRegistry
    llm: Any  # LLMProvider Protocol
    config: LabConfig
    sink: RunEventSink
    run_store: Any = None  # RunStore | None
    graph: Any = None  # EvidenceGraph | JsonEvidenceRepository | None
    knowledge: Any = None  # KnowledgeService | None
    budget: Any = None  # RunBudget | None
    extra: dict[str, Any] = field(default_factory=dict)

    def model_for(self, role: AgentRole) -> str | None:
        return self.config.models.get(role.value)

    def allowed_tools_for(self, role: AgentRole, task: TaskSpec) -> list[str]:
        if task.allowed_tools:
            return list(task.allowed_tools)
        agent_cfg = self.config.agents.get(role.value) or {}
        return list(agent_cfg.get("allowed_tools") or [])


async def llm_json(
    ctx: AgentContext,
    *,
    role: AgentRole,
    system: str,
    user: str,
    schema_name: str,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call LLM and require parsed JSON object."""
    from ai_lab.orchestrator.budget import record_agent_call

    if ctx.budget is not None:
        record_agent_call(ctx.budget)

    metadata: dict[str, Any] = {"agent_role": role.value, "run_id": ctx.run_id}
    if ctx.extra.get("task_id"):
        metadata["task_id"] = ctx.extra["task_id"]
    if extra_metadata:
        metadata.update(extra_metadata)
    request = LLMRequest(
        messages=[
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ],
        model=ctx.model_for(role),
        response_schema_name=schema_name,
        metadata=metadata,
    )
    if getattr(ctx.llm, "is_llm_router", False):
        from ai_lab.llm.router import RoutingContext

        routing_ctx = RoutingContext(
            run_id=ctx.run_id,
            task_id=ctx.extra.get("task_id"),
            independence_group=ctx.extra.get("independence_group"),
            frozen_blind_bundle=bool(ctx.extra.get("frozen_blind_bundle")),
            parallel_review=bool(ctx.extra.get("parallel_review")),
            review_contexts_differ=True,
            sink=ctx.sink,
            run_store=ctx.run_store,
        )
        response = await ctx.llm.complete(request, role=role, context=routing_ctx)
    else:
        response = await ctx.llm.complete(request)
    ctx.extra["last_llm_routing"] = getattr(response, "routing", None) or {}
    if ctx.budget is not None:
        from ai_lab.orchestrator.budget import record_llm_usage

        # Register usage at completion time — never coerce missing usage to 0.
        record_llm_usage(ctx.budget, response.usage if response.usage else None)
    if response.parsed is None:
        raise RuntimeError(f"LLM provider {response.provider} returned no parsed JSON")
    return response.parsed


class BaseAgent:
    role: AgentRole
    system_prompt: str = ""

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError
