"""Protocols — keep core free of concrete agent/tool/LLM imports."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ai_lab.core.models import AgentResult, LLMRequest, LLMResponse, TaskSpec


@runtime_checkable
class LLMProvider(Protocol):
    """Abstraction over reasoning backends (Mock, Cursor SDK, future providers)."""

    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return text (and optional parsed JSON) for a completion request."""
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
