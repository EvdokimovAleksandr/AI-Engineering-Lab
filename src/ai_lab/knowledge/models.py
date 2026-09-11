"""Knowledge-layer models: conflicts, conclusions, approved knowledge, replay."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ai_lab.core.enums import ConflictStatus, EvidenceStrength, SourceTrustTier, TrustLevel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ConflictRecord(BaseModel):
    conflict_id: str = Field(default_factory=lambda: _new_id("conf"))
    project_id: str
    run_id: str | None = None
    claim_a: str
    claim_b: str
    evidence: list[str] = Field(default_factory=list)
    status: ConflictStatus = ConflictStatus.OPEN
    resolution: str | None = None
    resolution_reason: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ConclusionRecord(BaseModel):
    conclusion_id: str = Field(default_factory=lambda: _new_id("conc"))
    project_id: str
    run_id: str | None = None
    statement: str
    status: str = "PROPOSED"  # PROPOSED | ACCEPTED | DISPUTED | DEMOTED
    decision_id: str | None = None
    supporting_nodes: list[str] = Field(default_factory=list)
    contradicting_nodes: list[str] = Field(default_factory=list)
    remaining_unknowns: list[str] = Field(default_factory=list)
    # Provenance metadata — not a single magic score
    confidence_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class ApprovedKnowledgeEntry(BaseModel):
    """Project-level knowledge that passed promotion gates — not mere consensus."""

    entry_id: str = Field(default_factory=lambda: _new_id("ak"))
    project_id: str
    claim_id: str
    canonical_id: str
    statement: str
    kind: str
    promoted_from_run_id: str
    decision_id: str | None = None
    verification_report_id: str | None = None
    adjudication_status: str | None = None
    evidence_strength: EvidenceStrength = EvidenceStrength.INDEPENDENT_RECOMPUTE
    source_tier: SourceTrustTier | None = None
    trust_level: TrustLevel = TrustLevel.INTERNAL
    content_hash: str
    status: str = "ACTIVE"  # ACTIVE | DEMOTED
    demotion_reason: str | None = None
    demoted_by_claim_id: str | None = None
    promoted_at: datetime = Field(default_factory=_utc_now)
    demoted_at: datetime | None = None


class RunComparison(BaseModel):
    run_a: str
    run_b: str
    new_claims: list[str] = Field(default_factory=list)
    changed_claims: list[str] = Field(default_factory=list)
    superseded_claims: list[str] = Field(default_factory=list)
    new_evidence_nodes: list[str] = Field(default_factory=list)
    new_contradictions: list[str] = Field(default_factory=list)
    changed_decisions: list[str] = Field(default_factory=list)
    new_simulations: list[str] = Field(default_factory=list)
    new_verification_results: list[str] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    run_id: str
    timestamp: datetime | None = None
    kind: str
    summary: str
    refs: list[str] = Field(default_factory=list)


class ReplayRecord(BaseModel):
    """Saved LLM (or tool) interaction for record/replay tests."""

    fixture_id: str = Field(default_factory=lambda: _new_id("fx"))
    role: str
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    model: str = "unknown"
    timestamp: datetime = Field(default_factory=_utc_now)
    schema_version: str = "1.0"


class PromotionGateResult(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
