"""V2.8 investigation-scope and research-sufficiency contracts.

These are orchestrator-owned artifacts, not a new AgentRole. Locked fields
reuse VerificationPolicy/CalculationSpec names (required_outputs, dimensions).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.core.enums import AssumptionKind, ProblemKind, ResearchOutcome, ScopeStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class TypedAssumption(BaseModel):
    """Explicit assumption with taxonomy — never a silent FACT."""

    text: str
    kind: AssumptionKind
    # Safe domain default (density of water) vs blocking ambiguity (what “strength” means).
    blocking: bool = False


class ResearchRequirement(BaseModel):
    """A topic the investigation must cover. Coverage is filled after research."""

    requirement_id: str = Field(default_factory=lambda: _new_id("rreq"))
    topic: str
    # PASS | PARTIAL | FAIL — mapped to existing evidence statuses in the UI.
    coverage: str = "PENDING"
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ClarificationQuestion(BaseModel):
    """Minimum HITL question. Options feed existing HitlRequest.options."""

    question: str
    why: str
    options: list[str] = Field(default_factory=list)
    # "choice" uses options; "text" expects a free-form note (e.g. missing temperatures).
    input_mode: str = "choice"


class ClarificationRecord(BaseModel):
    """User answer applied on --resume / UI resume. Does not rewrite original_problem."""

    round: int
    choice: str | None = None
    note: str = ""
    answers: dict[str, str] = Field(default_factory=dict)
    applied_at: datetime = Field(default_factory=_utc_now)


class InvestigationScope(BaseModel):
    """What we are actually trying to determine, locked after the scope gate.

    original_problem is immutable. Downstream planners see resolved fields, not a
    rewritten user prompt.
    """

    model_config = ConfigDict(extra="forbid")

    scope_id: str = Field(default_factory=lambda: _new_id("scope"))
    original_problem: str
    objective: str = ""
    domain: str | None = None
    # Same names as VerificationPolicy / CalculationSpec so downstream can reuse them.
    required_outputs: list[str] = Field(default_factory=list)
    expected_dimensions: dict[str, str] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    evidence_requirements: list[ResearchRequirement] = Field(default_factory=list)
    ambiguity: list[str] = Field(default_factory=list)
    assumptions: list[TypedAssumption] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    known_parameters: dict[str, str] = Field(default_factory=dict)
    unknown_parameters: list[str] = Field(default_factory=list)
    # PR-04 ScopeResolver frame — ask HITL only for Required that block READY.
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)
    assumption_candidates: list[str] = Field(default_factory=list)
    problem_kind: ProblemKind | None = None
    status: ScopeStatus = ScopeStatus.SCOPE_UNRESOLVED
    locked: bool = False
    # High-level rationale for the UI — not a hidden chain-of-thought transcript.
    rationale: str = ""
    clarification: ClarificationQuestion | None = None
    clarifications: list[ClarificationRecord] = Field(default_factory=list)
    clarification_round: int = 0
    # calculation | research | mixed — hint for TaskRouter, not a second orchestrator.
    pipeline_hint: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    def public_dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ResearchQueryAttempt(BaseModel):
    """Provenance for one search query. Refinement must stay derived_from the parent."""

    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(default_factory=lambda: _new_id("q"))
    query: str
    parent_query_id: str | None = None
    parent_query: str | None = None
    strategy: str = "initial"
    reason: str = ""
    outcome: ResearchOutcome = ResearchOutcome.RESEARCH_EMPTY
    sources_found: int = 0
    sources_retained: int = 0
    provider_error: str | None = None


class ResearchSufficiencyReport(BaseModel):
    """Deterministic evidence gate input. LLM synthesis cannot override this."""

    model_config = ConfigDict(extra="forbid")

    outcome: ResearchOutcome
    attempts: list[ResearchQueryAttempt] = Field(default_factory=list)
    coverage: list[ResearchRequirement] = Field(default_factory=list)
    required_count: int = 0
    covered_count: int = 0
    evidence_gaps: list[str] = Field(default_factory=list)
    refinement_count: int = 0
    research_required: bool = False
    provider_error: str | None = None
