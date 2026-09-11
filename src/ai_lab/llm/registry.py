"""Factory for LLM providers from LabConfig."""

from __future__ import annotations

from pathlib import Path

from ai_lab.core.models import LabConfig
from ai_lab.llm.mock import MockProvider


def create_llm_provider(
    config: LabConfig,
    *,
    cwd: Path | None = None,
    force_verification_fail: bool = False,
) -> MockProvider:
    """
    Build provider from config.provider.

    Returns MockProvider or CursorSDKProvider. Typed loosely for Protocol use.
    """
    if config.provider == "mock":
        return MockProvider(force_verification_fail=force_verification_fail)
    if config.provider == "cursor_sdk":
        from ai_lab.llm.cursor_sdk import CursorSDKProvider

        default_model = config.models.get("chief_engineer", "composer-2.5")
        return CursorSDKProvider(default_model=default_model, cwd=cwd)  # type: ignore[return-value]
    raise RuntimeError(f"Unsupported provider: {config.provider!r}")
