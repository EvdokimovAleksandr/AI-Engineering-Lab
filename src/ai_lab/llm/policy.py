"""Deterministic routing-policy validator. LLM cannot waive violations."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.core.enums import AgentRole
from ai_lab.llm.config import (
    KNOWN_PROVIDER_IDS,
    SPECIAL_ROUTING_KEYS,
    IndependencePolicy,
    ModelConfig,
    RoutingPolicy,
    is_safe_identifier,
)
from ai_lab.llm.independence import (
    ArchitectureFlags,
    IndependenceAssessment,
    classify_independence,
)

# Duplicated from planner.schemas so llm.policy does not import the planner package
# (that would load planner.__init__ → tools → knowledge and create an import cycle).
_AUTHOR_ROLES = frozenset(
    {
        AgentRole.CHIEF_ENGINEER,
        AgentRole.RESEARCH,
        AgentRole.THEORIST,
        AgentRole.SIMULATION,
        AgentRole.ENGINEERING_DESIGNER,
        AgentRole.EXPERIMENTAL_SCIENTIST,
    }
)
_REVIEW_ROLES = frozenset({AgentRole.VERIFICATION, AgentRole.RED_TEAM})


class RoutingValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    errors: list[str] = Field(default_factory=list)
    assessment: IndependenceAssessment | None = None


_IMPLEMENTED_ROLES: tuple[str, ...] = (
    AgentRole.CHIEF_ENGINEER.value,
    AgentRole.RESEARCH.value,
    AgentRole.THEORIST.value,
    AgentRole.SIMULATION.value,
    AgentRole.VERIFICATION.value,
    AgentRole.RED_TEAM.value,
)


def validate_routing_policy(
    routing_policy: RoutingPolicy,
    available_providers: Iterable[str],
    roles: Iterable[str] | None = None,
    independence_policy: IndependencePolicy | None = None,
    *,
    architecture: ArchitectureFlags | None = None,
) -> RoutingValidationResult:
    """Fail before execution on unknown providers/roles or independence violations."""
    available = frozenset(available_providers)
    required = [str(r) for r in (roles if roles is not None else _IMPLEMENTED_ROLES)]
    indep = independence_policy or IndependencePolicy()
    errors: list[str] = []

    known_keys = {r.value for r in AgentRole} | set(SPECIAL_ROUTING_KEYS)
    for key in routing_policy.roles:
        if key not in known_keys:
            errors.append(f"unknown role {key!r}")
        cfg = routing_policy.roles[key]
        errors.extend(_config_errors(cfg, available, prefix=f"role {key}"))

    if routing_policy.default is not None:
        errors.extend(_config_errors(routing_policy.default, available, prefix="default"))
    if routing_policy.planner is not None:
        errors.extend(_config_errors(routing_policy.planner, available, prefix="planner"))
    if routing_policy.adjudication is not None:
        errors.extend(
            _config_errors(routing_policy.adjudication, available, prefix="adjudication")
        )

    for role in required:
        if not routing_policy.has_role(role):
            errors.append(f"missing required model for role {role!r}")
        else:
            try:
                cfg = routing_policy.for_role(role)
            except (KeyError, ValueError) as exc:
                errors.append(str(exc))
                continue
            errors.extend(_config_errors(cfg, available, prefix=f"required role {role}"))

    assessment: IndependenceAssessment | None = None
    try:
        author = _author_model(routing_policy)
        verification = routing_policy.for_role(AgentRole.VERIFICATION)
        red_team = routing_policy.for_role(AgentRole.RED_TEAM)
        assessment = classify_independence(
            author, verification, red_team, indep, architecture=architecture
        )
        if not assessment.ok:
            errors.extend(assessment.policy_violations)
        errors.extend(
            _author_reviewer_duplicates(routing_policy, indep)
        )
    except KeyError as exc:
        errors.append(str(exc))

    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for err in errors:
        if err not in seen:
            seen.add(err)
            uniq.append(err)

    return RoutingValidationResult(
        ok=not uniq, errors=uniq, assessment=assessment
    )


def _config_errors(cfg: ModelConfig, available: frozenset[str], *, prefix: str) -> list[str]:
    errors: list[str] = []
    if cfg.provider not in available:
        errors.append(f"{prefix}: unknown provider {cfg.provider!r}")
    if not is_safe_identifier(cfg.provider):
        errors.append(f"{prefix}: invalid provider {cfg.provider!r}")
    if not is_safe_identifier(cfg.model):
        errors.append(f"{prefix}: invalid model {cfg.model!r}")
    return errors


def _author_model(policy: RoutingPolicy) -> ModelConfig:
    """Representative author config: simulation, else theorist, else chief."""
    for role in (
        AgentRole.SIMULATION,
        AgentRole.THEORIST,
        AgentRole.RESEARCH,
        AgentRole.CHIEF_ENGINEER,
    ):
        if policy.has_role(role):
            return policy.for_role(role)
    if policy.default is not None:
        return policy.default
    raise KeyError("No author model configuration")


def _author_reviewer_duplicates(
    policy: RoutingPolicy, indep: IndependencePolicy
) -> list[str]:
    """Every author role vs every reviewer, when the policy demands distinct models."""
    if not indep.require_different_model_from_author:
        return []
    errors: list[str] = []
    reviewers = []
    for role in _REVIEW_ROLES:
        if policy.has_role(role):
            reviewers.append((role, policy.for_role(role)))
    for author_role in _AUTHOR_ROLES:
        if not policy.has_role(author_role):
            continue
        author_cfg = policy.for_role(author_role)
        for review_role, review_cfg in reviewers:
            if author_cfg.identity == review_cfg.identity:
                errors.append(
                    f"independence policy violation: {author_role.value} "
                    f"shares model {author_cfg.identity} with {review_role.value}"
                )
    return errors
