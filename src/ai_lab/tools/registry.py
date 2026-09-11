"""Permission-scoped tool registry."""

from __future__ import annotations

from typing import Any

from ai_lab.observability.logger import get_logger
from ai_lab.tools.base import ToolSpec

logger = get_logger(__name__)


class ToolPermissionError(PermissionError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def list_tools(self) -> list[str]:
        return sorted(self._tools)

    async def call(
        self,
        name: str,
        *,
        allowed: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        if allowed is not None and name not in allowed:
            logger.error("Tool denied by permission scope: %s allowed=%s", name, allowed)
            raise ToolPermissionError(f"Tool {name!r} not allowed for this agent/task")
        return await self._tools[name].handler(**kwargs)
