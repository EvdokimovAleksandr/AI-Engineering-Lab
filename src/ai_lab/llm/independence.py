"""Classify architectural model independence.

This is NOT AgreementType.INDEPENDENT_EVIDENCE.
Different model names never prove independent evidence or recomputation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.core.enums import IndependenceLevel
from ai_lab.llm.config import IndependencePolicy, ModelConfig


class ArchitectureFlags(BaseModel):
    """Runtime review isolation — orthogonal to model diversity."""

    model_config = ConfigDict(extra="forbid")

    review_contexts_differ: bool = False
    frozen_blind_bundle: bool = False
    parallel_review: bool = False

    @property
    def all_set(self) -> bool:
        return (
            self.review_contexts_differ
            and self.frozen_blind_bundle
            and self.parallel_review
        )


class IndependenceAssessment(BaseModel):
    """Classification of *architectural* independence, not scientific proof."""

    model_config = ConfigDict(extra="forbid")

    level: IndependenceLevel
    author_vs_verification: IndependenceLevel
    author_vs_red_team: IndependenceLevel
    verification_vs_red_team: IndependenceLevel
    reasons: list[str] = Field(default_factory=list)
    policy_violations: list[str] = Field(default_factory=list)
    # Explicit: model diversity is never evidence independence.
    is_evidence_independence: bool = False

    @property
    def ok(self) -> bool:
        return self.level != IndependenceLevel.INVALID


def classify_pair(left: ModelConfig, right: ModelConfig) -> IndependenceLevel:
    """Pairwise model-config diversity.

    NONE    — identical (provider, model)
    PARTIAL — same provider, different model id
    FULL    — different providers (model ids are then distinct identities)
    """
    if left.provider == right.provider and left.model == right.model:
        return IndependenceLevel.NONE
    if left.provider == right.provider:
        return IndependenceLevel.PARTIAL
    return IndependenceLevel.FULL


def classify_independence(
    author_model: ModelConfig,
    verification_model: ModelConfig,
    red_team_model: ModelConfig,
    policy: IndependencePolicy,
    *,
    architecture: ArchitectureFlags | None = None,
) -> IndependenceAssessment:
    """Deterministic independence classification.

    Semantics (model dimension):
      NONE    — same model configuration
      PARTIAL — same provider, different model
      FULL    — different providers + (if architecture given) blind/parallel review

    INVALID — configured independence policy is violated.

    AgreementType.INDEPENDENT_EVIDENCE is decided elsewhere by deterministic
    checks / recomputation. This function never sets is_evidence_independence.
    """
    av = classify_pair(author_model, verification_model)
    ar = classify_pair(author_model, red_team_model)
    vr = classify_pair(verification_model, red_team_model)
    reasons: list[str] = [
        f"author vs verification: {av.value} ({author_model.identity} / {verification_model.identity})",
        f"author vs red_team: {ar.value} ({author_model.identity} / {red_team_model.identity})",
        f"verification vs red_team: {vr.value} ({verification_model.identity} / {red_team_model.identity})",
    ]
    violations: list[str] = []

    if policy.require_different_model_from_author:
        if av == IndependenceLevel.NONE:
            violations.append(
                "independence policy requires verification model ≠ author model"
            )
        if ar == IndependenceLevel.NONE:
            violations.append(
                "independence policy requires red_team model ≠ author model"
            )
    if policy.require_different_model_between_reviewers:
        if vr == IndependenceLevel.NONE:
            violations.append(
                "independence policy requires verification model ≠ red_team model"
            )
    if policy.require_different_provider_between_reviewers:
        if verification_model.provider == red_team_model.provider:
            violations.append(
                "independence policy requires verification provider ≠ red_team provider"
            )

    if violations:
        return IndependenceAssessment(
            level=IndependenceLevel.INVALID,
            author_vs_verification=av,
            author_vs_red_team=ar,
            verification_vs_red_team=vr,
            reasons=reasons + violations,
            policy_violations=violations,
            is_evidence_independence=False,
        )

    arch = architecture or ArchitectureFlags()
    if architecture is not None:
        reasons.append(
            "architecture: "
            f"review_contexts_differ={arch.review_contexts_differ} "
            f"frozen_blind_bundle={arch.frozen_blind_bundle} "
            f"parallel_review={arch.parallel_review}"
        )

    pairs = (av, ar, vr)
    if all(p == IndependenceLevel.NONE for p in pairs):
        level = IndependenceLevel.NONE
    elif all(p == IndependenceLevel.FULL for p in pairs) and (
        architecture is None or arch.all_set
    ):
        # Different providers. FULL also needs blind+parallel review when flags are supplied.
        level = IndependenceLevel.FULL
    else:
        # Same-provider different models, mixed pairs, or FULL providers without architecture.
        level = IndependenceLevel.PARTIAL
        if architecture is not None and not arch.all_set:
            reasons.append(
                "model diversity cannot be FULL without blind bundle + parallel review"
            )

    return IndependenceAssessment(
        level=level,
        author_vs_verification=av,
        author_vs_red_team=ar,
        verification_vs_red_team=vr,
        reasons=reasons,
        policy_violations=[],
        is_evidence_independence=False,
    )
