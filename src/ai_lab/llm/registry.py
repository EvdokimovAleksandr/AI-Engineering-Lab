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
    verification_status_override: str | None = None,
):
    """
    Build provider from config.provider.

    CursorSDK is always constructed in reasoning-only mode (ignores project cwd).
    """
    if config.provider == "mock":
        return MockProvider(
            force_verification_fail=force_verification_fail,
            verification_status_override=verification_status_override,
        )
    if config.provider == "cursor_sdk":
        from ai_lab.llm.cursor_sdk import CursorSDKProvider

        default_model = config.models.get("chief_engineer", "composer-2.5")
        # cwd intentionally not used for project binding — reasoning-only empty temp dir
        _ = cwd  # kept for API compatibility; must not bind to project root
        return CursorSDKProvider(default_model=default_model, reasoning_only=True)
    raise RuntimeError(f"Unsupported provider: {config.provider!r}")
