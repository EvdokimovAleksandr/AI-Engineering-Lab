"""Knowledge-layer models: conflicts, conclusions, approved knowledge, replay."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ai_lab.core.enums import (
    ConflictStatus,
    EvidenceStrength,
    SourceKind,
    SourceTrustTier,
    TrustLevel,
)
from ai_lab.core.models import ResearchFinding


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
    # V2.4a optional routing snapshot — old fixtures without these fields still load.
    provider: str = "replay"
    routing_policy_version: str | None = None
    routed_model: dict[str, Any] | None = None


class PromotionGateResult(BaseModel):
    allowed: bool
    reasons: list[str] = Field(default_factory=list)


# --- V2.2 research pipeline (sources / evidence / search hits) ---


class EvidenceLocation(BaseModel):
    """Locator inside a retrieved source. Fields are omitted unless actually known."""

    uri: str | None = None
    title: str | None = None
    section: str | None = None
    paragraph: int | None = None
    page: str | None = None
    fragment: str | None = None


class SourceRecord(BaseModel):
    """Resolvable retrieved source with deterministic identity and fingerprint."""

    source_id: str
    uri: str
    title: str | None = None
    publisher: str | None = None
    retrieved_at: datetime = Field(default_factory=_utc_now)
    source_type: SourceKind = SourceKind.UNKNOWN
    trust_tier: SourceTrustTier = SourceTrustTier.SECONDARY
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    # Retrieved body is data, never instructions. May be omitted when serializing to graph.
    content: str | None = None


class EvidenceRecord(BaseModel):
    """Excerpt extracted from a Source — provenance is mandatory."""

    evidence_id: str
    source_id: str
    text: str
    location: EvidenceLocation = Field(default_factory=EvidenceLocation)
    retrieved_at: datetime = Field(default_factory=_utc_now)
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    """Raw search-backend hit before fetch/resolve. URI is required."""

    uri: str
    title: str | None = None
    publisher: str | None = None
    snippet: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchLimits(BaseModel):
    """Operational caps for one research operation (sandbox-style, not a second budget)."""

    max_queries: int = 8
    max_sources: int = 12
    max_content_bytes: int = 200_000
    timeout_seconds: float = 15.0
    max_evidence_per_source: int = 3


class ResearchResult(BaseModel):
    """Structured output of a research operation."""

    query: str
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    findings: list[ResearchFinding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def provenance_rows(self) -> list[dict[str, Any]]:
        """Flatten Claim-ready provenance: evidence → source → uri/hash/query."""
        sources = {s.source_id: s for s in self.sources}
        rows: list[dict[str, Any]] = []
        for ev in self.evidence:
            src = sources.get(ev.source_id)
            rows.append(
                {
                    "evidence_id": ev.evidence_id,
                    "source_id": ev.source_id,
                    "uri": src.uri if src else None,
                    "title": src.title if src else ev.location.title,
                    "retrieved_at": (src.retrieved_at if src else ev.retrieved_at).isoformat(),
                    "content_hash": src.content_hash if src else ev.content_hash,
                    "source_type": src.source_type.value if src else None,
                    "trust_tier": src.trust_tier.value if src else None,
                    "query": self.query,
                    "location": ev.location.model_dump(mode="json"),
                }
            )
        return rows
