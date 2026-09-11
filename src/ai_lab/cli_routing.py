"""CLI helper for `python -m ai_lab routing` — inspect policy without calling an LLM."""

from __future__ import annotations

from pathlib import Path

from ai_lab.config_loader import load_config
from ai_lab.core.enums import AgentRole
from ai_lab.llm.config import (
    KNOWN_PROVIDER_IDS,
    apply_provider_override,
    independence_policy_from_config,
    routing_policy_from_config,
)
from ai_lab.llm.independence import ArchitectureFlags
from ai_lab.llm.policy import validate_routing_policy
from ai_lab.planner.schemas import AUTHOR_ROLES, INDEPENDENT_REVIEW_GROUP, REVIEW_ROLES
from ai_lab.planner.static import default_pipeline_tasks


def _group_for_role(role: AgentRole) -> str:
    if role in REVIEW_ROLES:
        return INDEPENDENT_REVIEW_GROUP
    if role in AUTHOR_ROLES:
        return "author"
    return "-"


def run_routing_cli(*, config_path: Path | None, provider: str | None) -> int:
    config = load_config(config_path)
    if provider:
        config = apply_provider_override(config, provider)
    policy = routing_policy_from_config(config)
    independence = independence_policy_from_config(config)
    result = validate_routing_policy(
        policy,
        KNOWN_PROVIDER_IDS,
        independence_policy=independence,
        architecture=ArchitectureFlags(
            review_contexts_differ=True,
            frozen_blind_bundle=True,
            parallel_review=True,
        ),
    )

    task_groups = {
        t.role: t.independence_group
        for t in default_pipeline_tasks()
        if t.role is not None
    }

    display_roles = [
        AgentRole.CHIEF_ENGINEER,
        AgentRole.RESEARCH,
        AgentRole.THEORIST,
        AgentRole.SIMULATION,
        AgentRole.VERIFICATION,
        AgentRole.RED_TEAM,
    ]
    print(f"routing_policy_version={policy.version}")
    for role in display_roles:
        cfg = policy.for_role(role)
        group = task_groups.get(role) or _group_for_role(role)
        print(f"{role.value:16} -> {cfg.identity}    group={group}")
    if policy.adjudication is not None:
        print(f"{'adjudication':16} -> {policy.adjudication.identity}    group=-")
    elif policy.has_role("adjudication"):
        adj = policy.for_role("adjudication")
        print(f"{'adjudication':16} -> {adj.identity}    group=-")

    print()
    print("Independence:")
    assessment = result.assessment
    if assessment is None:
        print("  (unavailable)")
    else:
        print(f"  verification vs red_team: {assessment.verification_vs_red_team.value}")
        print(f"  author vs verification: {assessment.author_vs_verification.value}")
        print(f"  author vs red_team: {assessment.author_vs_red_team.value}")
        print(f"  overall: {assessment.level.value}")
        print("  (architectural classification, not INDEPENDENT_EVIDENCE)")

    print()
    status = "VALID" if result.ok else "INVALID"
    print(status)
    for err in result.errors:
        print(f"  - {err}")
    return 0 if result.ok else 1
