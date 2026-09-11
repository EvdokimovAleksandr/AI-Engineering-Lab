"""ComputeSandbox protocol. Not a second ToolRegistry and not DeterministicVerifier."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai_lab.sandbox.models import ComputeSpec, SandboxCapabilities, SandboxContext, SandboxResult


@runtime_checkable
class ComputeSandbox(Protocol):
    """Isolated general-purpose Python execution.

    DeterministicVerifier / SafeExpressionEvaluator stay in-process on purpose.
    """

    name: str

    async def execute(self, spec: ComputeSpec, context: SandboxContext) -> SandboxResult:
        ...

    def capabilities(self) -> SandboxCapabilities:
        ...
