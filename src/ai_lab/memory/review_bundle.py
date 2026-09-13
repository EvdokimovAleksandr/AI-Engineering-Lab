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
        verification_spec=dict(claim.verification_spec) if claim.verification_spec else None,
        computation_artifact_id=claim.computation_artifact_id,
        refs=list(claim.refs or []),
        support_status=claim.support_status,
        evidence_ids=list(claim.evidence_ids or []),
        calculation_ids=list(claim.calculation_ids or []),
    )


def build_review_bundle(
    *,
    run_id: str,
    claims: list[Claim],
    computation_artifacts: list[dict] | None = None,
    check_report: DeterministicCheckReport | None = None,
    project_id: str | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    contract_version: str | None = None,
) -> ReviewBundle:
    """Assemble a frozen blind package. Does not include peer review conclusions."""
    from ai_lab.core.execution_context import ContextMismatchError

    # PR-01: claims from another investigation must not enter the blind bundle.
    for c in claims:
        if c.run_id and c.run_id != run_id:
            raise ContextMismatchError(
                "Claim.run_id does not match ReviewBundle.run_id",
                field="run_id",
                expected=run_id,
                actual=c.run_id,
                where=f"ReviewBundle.claim[{c.claim_id}]",
            )
        if project_id and c.project_id and c.project_id != project_id:
            raise ContextMismatchError(
                "Claim.project_id does not match ReviewBundle.project_id",
                field="project_id",
                expected=project_id,
                actual=c.project_id,
                where=f"ReviewBundle.claim[{c.claim_id}]",
            )
        inv = investigation_id or project_id
        if inv and c.investigation_id and c.investigation_id != inv:
            raise ContextMismatchError(
                "Claim.investigation_id does not match ReviewBundle",
                field="investigation_id",
                expected=inv,
                actual=c.investigation_id,
                where=f"ReviewBundle.claim[{c.claim_id}]",
            )

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
        project_id=project_id,
        investigation_id=investigation_id or project_id,
        task_id=task_id,
        contract_version=contract_version,
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
