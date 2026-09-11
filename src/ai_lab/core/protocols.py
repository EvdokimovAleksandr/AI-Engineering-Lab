"""Protocols — keep core free of concrete agent/tool/LLM imports."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ai_lab.core.enums import AgentRole
from ai_lab.core.models import AgentResult, LLMRequest, LLMResponse, TaskGraphProposal, TaskSpec


@runtime_checkable
class LLMProvider(Protocol):
    """Abstraction over reasoning backends (Mock, Cursor SDK, future providers)."""

    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return text (and optional parsed JSON) for a completion request."""
        ...


@runtime_checkable
class LLMRouter(Protocol):
    """Selects a ModelConfig and delegates to an LLMProvider. Not an agent.

    Does not execute tools, write files, mutate TaskGraph / RunBudget /
    VerificationResult / CheckStatus, or adjudicate.
    """

    async def complete(
        self,
        request: LLMRequest,
        role: AgentRole | None = None,
        context: Any = None,
    ) -> LLMResponse:
        """Route by role/policy, then call the selected LLMProvider."""
        ...


@runtime_checkable
class Tool(Protocol):
    """Single capability exposed to agents via ToolRegistry."""

    name: str
    description: str

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        ...


@runtime_checkable
class Agent(Protocol):
    """Specialized lab role."""

    role: Any  # AgentRole — kept loose in Protocol to avoid circular import issues

    async def run(self, task: TaskSpec, ctx: Any) -> AgentResult:
        ...


@runtime_checkable
class TaskPlanner(Protocol):
    """Produces an untrusted TaskGraphProposal. Never executes agents or tools."""

    name: str

    async def propose(self, context: Any) -> TaskGraphProposal:
        """Return a structured proposal. Validation happens outside the planner."""
        ...
