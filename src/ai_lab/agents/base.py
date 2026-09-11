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
    response = await ctx.llm.complete(request)
    if ctx.budget is not None and response.usage:
        tokens = int(response.usage.get("total_tokens") or 0)
        ctx.budget.tokens_used += tokens
    if response.parsed is None:
        raise RuntimeError(f"LLM provider {response.provider} returned no parsed JSON")
    return response.parsed


class BaseAgent:
    role: AgentRole
    system_prompt: str = ""

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError
