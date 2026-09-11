"""Tool base types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Awaitable[dict[str, Any]]]
