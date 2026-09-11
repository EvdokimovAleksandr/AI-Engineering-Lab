"""Factory for LLM providers and the policy router.

Provider construction lives here (registry). The router only selects a ModelConfig.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ai_lab.core.models import LabConfig
from ai_lab.llm.config import (
    KNOWN_PROVIDER_IDS,
    RoutingPolicy,
    independence_policy_from_config,
    routing_policy_from_config,
)
from ai_lab.llm.mock import MockProvider
from ai_lab.llm.policy import validate_routing_policy
from ai_lab.llm.router import PolicyLLMRouter, RoutingContext
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

ProviderFactory = Callable[..., Any]


def _make_mock(
    config: LabConfig,
    *,
    force_verification_fail: bool = False,
    verification_status_override: str | None = None,
    **_: Any,
) -> MockProvider:
    return MockProvider(
        force_verification_fail=force_verification_fail,
        verification_status_override=verification_status_override,
    )


def _make_cursor_sdk(config: LabConfig, *, cwd: Path | None = None, **_: Any) -> Any:
    from ai_lab.llm.cursor_sdk import CursorSDKProvider

    default_model = config.models.get("chief_engineer", "composer-2.5")
    _ = cwd  # must not bind Cursor to the project root
    return CursorSDKProvider(default_model=default_model, reasoning_only=True)


def _make_replay(config: LabConfig, **kwargs: Any) -> Any:
    from ai_lab.knowledge.replay import ReplayProvider

    records = kwargs.get("replay_records")
    return ReplayProvider(records)


_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "mock": _make_mock,
    "cursor_sdk": _make_cursor_sdk,
    "replay": _make_replay,
}


def create_provider(
    provider_id: str,
    config: LabConfig,
    **kwargs: Any,
) -> Any:
    """Instantiate one named provider. Unknown ids fail loudly."""
    factory = _PROVIDER_FACTORIES.get(provider_id)
    if factory is None:
        raise RuntimeError(f"Unsupported provider: {provider_id!r}")
    return factory(config, **kwargs)


def create_provider_registry(
    config: LabConfig,
    *,
    policy: RoutingPolicy | None = None,
    extra_providers: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build provider_id → instance for every provider the policy needs."""
    policy = policy or routing_policy_from_config(config)
    needed = set(policy.provider_ids()) | {config.provider}
    registry: dict[str, Any] = {}
    extras = dict(extra_providers or {})
    for provider_id in sorted(needed):
        if provider_id in extras:
            registry[provider_id] = extras[provider_id]
            continue
        registry[provider_id] = create_provider(provider_id, config, **kwargs)
    for provider_id, instance in extras.items():
        registry[provider_id] = instance
    return registry


def create_llm_router(
    config: LabConfig,
    *,
    cwd: Path | None = None,
    force_verification_fail: bool = False,
    verification_status_override: str | None = None,
    extra_providers: Mapping[str, Any] | None = None,
    routing_context: RoutingContext | None = None,
    skip_policy_validation: bool = False,
    **kwargs: Any,
) -> PolicyLLMRouter:
    """Validate routing, construct providers, return a PolicyLLMRouter."""
    policy = routing_policy_from_config(config)
    independence = independence_policy_from_config(config)
    if not skip_policy_validation:
        result = validate_routing_policy(
            policy,
            KNOWN_PROVIDER_IDS,
            independence_policy=independence,
        )
        if not result.ok:
            logger.error("Routing policy invalid: %s", result.errors)
            raise RuntimeError(f"Invalid routing policy: {result.errors}")
    providers = create_provider_registry(
        config,
        policy=policy,
        extra_providers=extra_providers,
        cwd=cwd,
        force_verification_fail=force_verification_fail,
        verification_status_override=verification_status_override,
        **kwargs,
    )
    return PolicyLLMRouter(
        policy=policy,
        providers=providers,
        default_context=routing_context,
    )


def create_llm_provider(
    config: LabConfig,
    *,
    cwd: Path | None = None,
    force_verification_fail: bool = False,
    verification_status_override: str | None = None,
):
    """
    Backward-compatible entry: returns an LLMRouter that implements complete(request).

    CursorSDK is always constructed in reasoning-only mode (ignores project cwd).
    """
    return create_llm_router(
        config,
        cwd=cwd,
        force_verification_fail=force_verification_fail,
        verification_status_override=verification_status_override,
    )
