"""Pydantic domain models for evidence, tasks, decisions, and agent I/O."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from ai_lab.core.enums import (
    AgentRole,
    AttackSeverity,
    DecisionStatus,
    EvidenceKind,
    ProjectState,
    VerificationStatus,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ConfidenceBreakdown(BaseModel):
    """Confidence is derived from evidence quality — not a free-floating magic number."""

    source_quality: float = Field(ge=0.0, le=1.0, default=0.0)
    independent_confirmations: float = Field(ge=0.0, le=1.0, default=0.0)
    compute_check: float = Field(ge=0.0, le=1.0, default=0.0)
    experiment_check: float = Field(ge=0.0, le=1.0, default=0.0)
    contradictions: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="1.0 = strong contradictions present (reduces score)",
    )
    assumption_quality: float = Field(ge=0.0, le=1.0, default=0.0)

    def score(self) -> float:
        """Weighted score; contradictions reduce confidence."""
        positive = (
            0.25 * self.source_quality
            + 0.25 * self.independent_confirmations
            + 0.20 * self.compute_check
            + 0.15 * self.experiment_check
            + 0.15 * self.assumption_quality
        )
        return max(0.0, min(1.0, positive * (1.0 - 0.7 * self.contradictions)))


class Claim(BaseModel):
    """Atomic claim with kind, conditions, and falsifiers."""

    claim_id: str = Field(default_factory=lambda: _new_id("claim"))
    statement: str
    kind: EvidenceKind
    source: str | None = None
    evidence: str | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    confidence: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    falsifiers: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _assumptions_are_not_facts(self) -> Claim:
        # Early guard: callers must not label unsupported statements as FACT.
        if self.kind == EvidenceKind.FACT and not self.source and not self.evidence:
            raise ValueError(
                "FACT requires source or evidence; use ASSUMPTION or HYPOTHESIS otherwise"
            )
        return self


class ResearchFinding(BaseModel):
    claim: Claim
    relevance: float = Field(ge=0.0, le=1.0, default=0.5)
    potential_contradiction: str | None = None


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: _new_id("hyp"))
    statement: str
    prediction: str
    falsification_criteria: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    related_claim_ids: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class VerificationReport(BaseModel):
    report_id: str = Field(default_factory=lambda: _new_id("ver"))
    target_claim_ids: list[str] = Field(default_factory=list)
    status: VerificationStatus
    discrepancies: list[str] = Field(default_factory=list)
    recomputed: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    agent_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class RedTeamAttack(BaseModel):
    attack_id: str = Field(default_factory=lambda: _new_id("atk"))
    target_claim_ids: list[str] = Field(default_factory=list)
    description: str
    severity: AttackSeverity
    category: str = Field(
        description="e.g. counterexample, bad_assumption, physical_law, numerical_instability"
    )


class RedTeamReport(BaseModel):
    report_id: str = Field(default_factory=lambda: _new_id("rt"))
    attacks: list[RedTeamAttack] = Field(default_factory=list)
    recommended_reject: bool = False
    summary: str = ""
    agent_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @property
    def max_severity(self) -> AttackSeverity | None:
        if not self.attacks:
            return None
        order = [
            AttackSeverity.LOW,
            AttackSeverity.MEDIUM,
            AttackSeverity.HIGH,
            AttackSeverity.CRITICAL,
        ]
        return max(self.attacks, key=lambda a: order.index(a.severity)).severity


class DecisionRecord(BaseModel):
    """Append-only engineering decision journal entry."""

    decision_id: str = Field(default_factory=lambda: _new_id("dec"))
    question: str
    hypothesis: str | None = None
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    agents_involved: list[str] = Field(default_factory=list)
    supporting_results: list[str] = Field(default_factory=list)
    contradicting_results: list[str] = Field(default_factory=list)
    confidence: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    status: DecisionStatus = DecisionStatus.PROPOSED
    next_action: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class TaskSpec(BaseModel):
    """Work unit dispatched by the Chief Engineer / orchestrator."""

    task_id: str = Field(default_factory=lambda: _new_id("task"))
    role: AgentRole
    objective: str
    inputs: list[str] = Field(
        default_factory=list,
        description="Artifact paths or claim IDs available to the agent",
    )
    allowed_tools: list[str] = Field(default_factory=list)
    independence_group: str | None = Field(
        default=None,
        description="Tasks with the same group may run in parallel without sharing drafts",
    )
    state_context: ProjectState | None = None


class HitlRequest(BaseModel):
    reason: str
    options: list[str] = Field(default_factory=list)
    blocking: bool = True


class AgentResult(BaseModel):
    agent_role: AgentRole
    task_id: str
    summary: str = ""
    claims: list[Claim] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    research_findings: list[ResearchFinding] = Field(default_factory=list)
    verification: VerificationReport | None = None
    red_team: RedTeamReport | None = None
    decisions: list[DecisionRecord] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    follow_up_tasks: list[TaskSpec] = Field(default_factory=list)
    hitl_request: HitlRequest | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: str  # system | user | assistant
    content: str


class LLMRequest(BaseModel):
    messages: list[LLMMessage]
    model: str | None = None
    response_schema_name: str | None = None
    temperature: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str
    parsed: dict[str, Any] | None = None
    model: str | None = None
    provider: str
    run_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    """Observability event for a single agent/tool step."""

    event_id: str = Field(default_factory=lambda: _new_id("evt"))
    run_id: str
    timestamp: datetime = Field(default_factory=_utc_now)
    agent_role: str | None = None
    task_id: str | None = None
    tool_name: str | None = None
    duration_ms: float | None = None
    status: str = "ok"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ProjectSnapshot(BaseModel):
    project_name: str
    state: ProjectState = ProjectState.CREATED
    iteration: int = 0
    run_id: str | None = None
    updated_at: datetime = Field(default_factory=_utc_now)


class LabConfig(BaseModel):
    """Loaded from config/default.yaml."""

    provider: str = "mock"
    models: dict[str, str] = Field(default_factory=dict)
    runtime: dict[str, Any] = Field(default_factory=dict)
    sandbox: dict[str, Any] = Field(default_factory=dict)
    agents: dict[str, Any] = Field(default_factory=dict)
    observability: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        allowed = {"mock", "cursor_sdk"}
        if v not in allowed:
            raise ValueError(f"provider must be one of {allowed}, got {v!r}")
        return v
