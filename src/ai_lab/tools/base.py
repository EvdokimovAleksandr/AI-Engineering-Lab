"""Tool base types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ToolSpec:
    """Trusted tool descriptor. LLM cannot mutate these guarantees."""

    name: str
    description: str
    handler: Callable[..., Awaitable[dict[str, Any]]]
    # V2.4b: compute tools declare sandbox policy. Not overridable via ComputeSpec.
    sandbox_required: bool = False
    network: str = "n/a"
    filesystem: str = "n/a"
    metadata: dict[str, Any] = field(default_factory=dict)
