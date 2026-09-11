"""Build blind ReviewBundle for independent Verification / Red Team."""

from __future__ import annotations

from ai_lab.core.enums import SourceTrustTier
from ai_lab.core.models import (
    BlindClaimView,
    Claim,
    DeterministicCheckReport,
    ReviewBundle,
)


# Author fields that must never enter independent review prompts
_EXCLUDED_AUTHOR_FIELDS = frozenset(
    {
        "confidence",
        "agent_id",
        "agreement_type",
        "created_at",
        "supersedes",
        "superseded_by",
        "version",
    }
)


def claim_to_blind_view(claim: Claim) -> BlindClaimView:
    """Strip author confidence / narrative fields for independent review."""
    source_trust = claim.source_trust
    if claim.source and str(claim.source).startswith("mock://") and source_trust is None:
        source_trust = SourceTrustTier.STUB
    return BlindClaimView(
        claim_id=claim.claim_id,
        statement=claim.statement,
        kind=claim.kind,
        source=claim.source,
        source_trust=source_trust,
        conditions=dict(claim.conditions or {}),
        assumptions=list(claim.assumptions or []),
        falsifiers=list(claim.falsifiers or []),
        math_check=dict(claim.math_check) if claim.math_check else None,
        computation_artifact_id=claim.computation_artifact_id,
        refs=list(claim.refs or []),
    )


def build_review_bundle(
    *,
    run_id: str,
    claims: list[Claim],
    computation_artifacts: list[dict] | None = None,
    check_report: DeterministicCheckReport | None = None,
) -> ReviewBundle:
    """Assemble a frozen blind package. Does not include peer review conclusions."""
    blind = [claim_to_blind_view(c) for c in claims]
    sources: list[dict] = []
    for c in claims:
        if c.source:
            sources.append(
                {
                    "claim_id": c.claim_id,
                    "source": c.source,
                    "source_trust": (c.source_trust.value if c.source_trust else None),
                }
            )
    return ReviewBundle(
        run_id=run_id,
        target_claim_ids=[c.claim_id for c in claims],
        claims=blind,
        computation_artifacts=list(computation_artifacts or []),
        source_references=sources,
        check_report=check_report,
    )


def assert_bundle_hides_author_confidence(bundle: ReviewBundle) -> None:
    """Test helper / runtime assert: no confidence keys in serialized claims."""
    for claim in bundle.claims:
        dumped = claim.model_dump(mode="json")
        for key in _EXCLUDED_AUTHOR_FIELDS:
            if key in dumped and dumped[key] not in (None, [], {}, 0, 1):
                # version may be present on BlindClaimView? We excluded it from BlindClaimView
                pass
        if "confidence" in dumped:
            raise AssertionError("ReviewBundle must not expose author confidence")
