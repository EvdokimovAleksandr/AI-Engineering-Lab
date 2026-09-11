"""LLMRouter: role → RoutingPolicy → ModelConfig → LLMProvider.

Not an agent. Does not execute tools, write files, or change gates.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ai_lab.core.enums import AgentRole
from ai_lab.core.models import LLMRequest, LLMResponse, RunEvent
from ai_lab.knowledge.hashing import sha256_json, sha256_text
from ai_lab.llm.config import (
    ROUTING_CONTROL_KEYS,
    ModelConfig,
    RoutingPolicy,
    redact_secrets,
)
from ai_lab.llm.retry import RetryPolicy
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RoutingContext:
    """Non-LLM facts the router may record. Never read from model output."""

    run_id: str | None = None
    task_id: str | None = None
    independence_group: str | None = None
    review_contexts_differ: bool = True
    frozen_blind_bundle: bool = False
    parallel_review: bool = False
    sink: Any = None  # RunEventSink | None
    run_store: Any = None  # RunStore | None
    extra: dict[str, Any] = field(default_factory=dict)


def _strip_routing_controls(metadata: dict[str, Any]) -> dict[str, Any]:
    """LLM/research prose must not steer provider/model selection."""
    return {
        key: value
        for key, value in metadata.items()
        if str(key).replace("-", "_").lower() not in ROUTING_CONTROL_KEYS
    }


def _role_key(request: LLMRequest, role: AgentRole | str | None) -> str:
    if role is not None:
        return role.value if isinstance(role, AgentRole) else str(role)
    meta = request.metadata or {}
    if meta.get("planner") is True or meta.get("planner") == "true":
        return "planner"
    raw = meta.get("agent_role")
    if raw in (None, ""):
        raise ValueError("LLM request has no routable role (agent_role / planner missing)")
    try:
        return AgentRole(str(raw)).value
    except ValueError as exc:
        logger.error("Unknown agent_role in LLM metadata: %s", raw)
        raise ValueError(f"Unknown role for routing: {raw!r}") from exc


def _prompt_hash(request: LLMRequest) -> str:
    return sha256_text("\n".join(m.content for m in request.messages))


def _request_hash(request: LLMRequest, cfg: ModelConfig, role_key: str) -> str:
    payload = {
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        "model": cfg.model,
        "provider": cfg.provider,
        "response_schema_name": request.response_schema_name,
        "role": role_key,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    return sha256_json(payload)


def _tokens_from_usage(usage: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not usage:
        return None, None
    raw_in = usage.get("input_tokens", usage.get("prompt_tokens"))
    raw_out = usage.get("output_tokens", usage.get("completion_tokens"))
    input_tokens = int(raw_in) if raw_in is not None else None
    output_tokens = int(raw_out) if raw_out is not None else None
    return input_tokens, output_tokens


def _reported_cost(response: LLMResponse) -> float | None:
    """Pass through provider-reported cost only. Never invent a number."""
    if response.estimated_cost is not None:
        return response.estimated_cost
    usage = response.usage or {}
    if "estimated_cost" in usage and usage["estimated_cost"] is not None:
        return float(usage["estimated_cost"])
    return None


class PolicyLLMRouter:
    """Validated configuration → provider instance. Provider identity is config-owned."""

    name = "router"
    is_llm_router = True

    def __init__(
        self,
        *,
        policy: RoutingPolicy,
        providers: Mapping[str, Any],
        retry_policy: RetryPolicy | None = None,
        default_context: RoutingContext | None = None,
    ) -> None:
        if retry_policy is None:
            retry_policy = RetryPolicy()
        retry_policy.validate_invariants()
        if retry_policy.max_attempts != 1:
            # Declared extension only — do not silently retry independent reviewers.
            logger.error(
                "RetryPolicy.max_attempts=%s ignored; router does not retry",
                retry_policy.max_attempts,
            )
        self.policy = policy
        self.providers = dict(providers)
        self.retry_policy = retry_policy
        self.default_context = default_context or RoutingContext()

    def resolve(
        self, role: AgentRole | str, context: RoutingContext | None = None
    ) -> ModelConfig:
        _ = context  # routing is policy-only; context is provenance, not a selector
        return self.policy.for_role(role)

    async def complete(
        self,
        request: LLMRequest,
        role: AgentRole | str | None = None,
        context: RoutingContext | None = None,
    ) -> LLMResponse:
        ctx = context or self.default_context
        role_key = _role_key(request, role)
        cfg = self.policy.for_role(role_key)
        provider = self.providers.get(cfg.provider)
        if provider is None:
            logger.error("Routing selected unregistered provider %s", cfg.provider)
            raise RuntimeError(f"Provider {cfg.provider!r} is not registered")

        # Policy wins over request.model / temperature / attacker metadata.
        safe_meta = _strip_routing_controls(dict(request.metadata or {}))
        safe_meta["agent_role"] = (
            role_key if role_key != "planner" else safe_meta.get("agent_role", role_key)
        )
        if ctx.run_id:
            safe_meta.setdefault("run_id", ctx.run_id)
        if ctx.task_id:
            safe_meta.setdefault("task_id", ctx.task_id)
        routed = request.model_copy(
            update={
                "model": cfg.model,
                "temperature": cfg.temperature,
                "metadata": safe_meta,
            }
        )

        t0 = time.perf_counter()
        raw = await provider.complete(routed)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        input_tokens, output_tokens = _tokens_from_usage(raw.usage)
        prompt_h = _prompt_hash(request)
        request_h = _request_hash(request, cfg, role_key)
        response_h = sha256_text(raw.content or "")
        routing_meta = redact_secrets(
            {
                "independence_group": ctx.independence_group,
                "max_tokens": cfg.max_tokens,
                "model": cfg.model,
                "model_version": cfg.model_version,
                "provider": cfg.provider,
                "provider_reported_model": raw.model,
                "role": role_key,
                "routing_policy_version": self.policy.version,
                "task_id": ctx.task_id,
                "temperature": cfg.temperature,
            }
        )
        response = raw.model_copy(
            update={
                "provider": cfg.provider,
                "model": cfg.model,
                "model_version": cfg.model_version,
                "routing_policy_version": self.policy.version,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens,
                "request_hash": request_h,
                "prompt_hash": prompt_h,
                "response_hash": response_h,
                "latency_ms": latency_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost": _reported_cost(raw),
                "routing": routing_meta,
            }
        )
        self._record_provenance(response, cfg, role_key, ctx)
        return response

    def _record_provenance(
        self,
        response: LLMResponse,
        cfg: ModelConfig,
        role_key: str,
        ctx: RoutingContext,
    ) -> None:
        run_id = ctx.run_id or (response.run_id or "unknown")
        payload = {
            "latency_ms": response.latency_ms,
            "model": cfg.model,
            "model_version": cfg.model_version,
            "prompt_hash": response.prompt_hash,
            "provider": cfg.provider,
            "request_hash": response.request_hash,
            "response_hash": response.response_hash,
            "role": role_key,
            "routing_policy_version": self.policy.version,
            "run_id": run_id,
            "task_id": ctx.task_id,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "estimated_cost": response.estimated_cost,
        }
        if ctx.sink is not None:
            ctx.sink.emit(
                RunEvent(
                    run_id=run_id,
                    agent_role=role_key,
                    task_id=ctx.task_id,
                    duration_ms=response.latency_ms,
                    message="llm_complete",
                    data=payload,
                )
            )
        if ctx.run_store is not None and hasattr(ctx.run_store, "append_llm_invocation"):
            ctx.run_store.append_llm_invocation(payload)
