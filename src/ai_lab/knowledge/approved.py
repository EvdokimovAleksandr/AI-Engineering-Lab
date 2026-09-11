"""ApprovedKnowledge promotion / demotion — policy layer, not LLM."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_lab.core.enums import (
    AdjudicationStatus,
    AgreementType,
    ClaimLifecycle,
    ClaimVisibility,
    EvidenceKind,
    EvidenceStrength,
    VerificationStatus,
)
from ai_lab.core.models import Claim
from ai_lab.knowledge.hashing import claim_content_hash
from ai_lab.knowledge.models import ApprovedKnowledgeEntry, PromotionGateResult
from ai_lab.memory.project_store import ProjectStore


def _index_rel() -> str:
    return "knowledge/approved/index.json"


def _entry_rel(entry_id: str) -> str:
    return f"knowledge/approved/{entry_id}.json"


def list_approved(store: ProjectStore) -> list[ApprovedKnowledgeEntry]:
    try:
        index = store.read_json(_index_rel())
    except FileNotFoundError:
        return []
    out = []
    for entry_id, path in index.items():
        out.append(ApprovedKnowledgeEntry.model_validate(store.read_json(path)))
    return out


def list_approved_as_claims(store: ProjectStore) -> list[Claim]:
    """Visibility APPROVED_KNOWLEDGE returns Claim-shaped views of active entries."""
    claims: list[Claim] = []
    for e in list_approved(store):
        if e.status != "ACTIVE":
            continue
        claims.append(
            Claim(
                claim_id=e.claim_id,
                project_id=e.project_id,
                run_id=e.promoted_from_run_id,
                statement=e.statement,
                kind=EvidenceKind(e.kind) if e.kind in EvidenceKind._value2member_map_ else EvidenceKind.INFERENCE,
                content_hash=e.content_hash,
                evidence_strength=e.evidence_strength,
                source_trust=e.source_tier,
                lifecycle=ClaimLifecycle.ACTIVE,
                evidence=f"approved:{e.entry_id}",
            )
        )
    return claims


def check_promotion_gates(
    *,
    claim: Claim,
    verification_status: VerificationStatus | None,
    adjudication_status: AdjudicationStatus | None,
    red_team_completed: bool,
    deterministic_critical_ok: bool,
    agreement_type: AgreementType | None,
) -> PromotionGateResult:
    reasons: list[str] = []
    if claim.lifecycle in {ClaimLifecycle.DISPUTED, ClaimLifecycle.REJECTED, ClaimLifecycle.DEMOTED}:
        reasons.append(f"claim lifecycle={claim.lifecycle.value} blocks promotion")
    if claim.superseded_by:
        reasons.append("superseded claims cannot be promoted")
    if not deterministic_critical_ok:
        reasons.append("deterministic critical checks not PASS")
    if verification_status != VerificationStatus.PASS:
        reasons.append(f"verification status={verification_status} (need PASS)")
    if not red_team_completed:
        reasons.append("red team not completed")
    if adjudication_status != AdjudicationStatus.PASS:
        reasons.append(f"adjudication={adjudication_status} (need PASS/ACCEPT)")
    if agreement_type == AgreementType.CONSENSUS:
        reasons.append("CONSENSUS alone is not sufficient for ApprovedKnowledge")
    # Provenance
    if not claim.content_hash and not claim.evidence:
        reasons.append("missing provenance (content_hash/evidence)")
    return PromotionGateResult(allowed=not reasons, reasons=reasons)


def promote_to_approved_knowledge(
    store: ProjectStore,
    *,
    claim: Claim,
    verification_status: VerificationStatus | None,
    adjudication_status: AdjudicationStatus | None,
    red_team_completed: bool,
    deterministic_critical_ok: bool,
    agreement_type: AgreementType | None,
    decision_id: str | None = None,
    verification_report_id: str | None = None,
) -> ApprovedKnowledgeEntry:
    gate = check_promotion_gates(
        claim=claim,
        verification_status=verification_status,
        adjudication_status=adjudication_status,
        red_team_completed=red_team_completed,
        deterministic_critical_ok=deterministic_critical_ok,
        agreement_type=agreement_type,
    )
    if not gate.allowed:
        raise PermissionError("Promotion denied: " + "; ".join(gate.reasons))

    content_hash = claim.content_hash or claim_content_hash(
        claim.statement, claim.kind.value, claim.evidence, claim.math_check
    )
    entry = ApprovedKnowledgeEntry(
        project_id=claim.project_id or store.name,
        claim_id=claim.claim_id,
        canonical_id=claim.canonical_id,
        statement=claim.statement,
        kind=claim.kind.value,
        promoted_from_run_id=claim.run_id or "unknown",
        decision_id=decision_id,
        verification_report_id=verification_report_id,
        adjudication_status=adjudication_status.value if adjudication_status else None,
        evidence_strength=(
            EvidenceStrength.INDEPENDENT_RECOMPUTE
            if agreement_type == AgreementType.INDEPENDENT_EVIDENCE
            else claim.evidence_strength
        ),
        source_tier=claim.source_trust,
        content_hash=content_hash,
        status="ACTIVE",
    )
    path = _entry_rel(entry.entry_id)
    store.write_json(path, entry.model_dump(mode="json"))
    try:
        index = store.read_json(_index_rel())
    except FileNotFoundError:
        index = {}
    index[entry.entry_id] = path
    # Also index by claim_id for lookup
    index[f"claim:{claim.claim_id}"] = path
    store.write_json(_index_rel(), index)
    return entry


def demote_approved_knowledge(
    store: ProjectStore,
    *,
    claim_id: str,
    reason: str,
    demoted_by_claim_id: str | None = None,
) -> ApprovedKnowledgeEntry:
    try:
        index = store.read_json(_index_rel())
    except FileNotFoundError as exc:
        raise KeyError(f"No approved knowledge for claim {claim_id}") from exc
    path = index.get(f"claim:{claim_id}")
    if not path:
        # search entries
        for eid, p in index.items():
            if eid.startswith("claim:"):
                continue
            entry = ApprovedKnowledgeEntry.model_validate(store.read_json(p))
            if entry.claim_id == claim_id and entry.status == "ACTIVE":
                path = p
                break
    if not path:
        raise KeyError(f"No active approved knowledge for claim {claim_id}")
    entry = ApprovedKnowledgeEntry.model_validate(store.read_json(path))
    entry.status = "DEMOTED"
    entry.demotion_reason = reason
    entry.demoted_by_claim_id = demoted_by_claim_id
    entry.demoted_at = datetime.now(timezone.utc)
    store.write_json(path, entry.model_dump(mode="json"))
    return entry
