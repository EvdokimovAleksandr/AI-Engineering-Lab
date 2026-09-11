"""Pydantic domain models for evidence, tasks, decisions, and agent I/O."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from ai_lab.core.enums import (
    AdjudicationStatus,
    AgentRole,
    AgreementType,
    AttackSeverity,
    ClaimLifecycle,
    DecisionStatus,
    EvidenceKind,
    EvidenceStrength,
    GraphEdgeType,
    GraphNodeType,
    ProjectState,
    SourceTrustTier,
    TrustLevel,
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
    """Atomic claim with kind, conditions, falsifiers, run scope, and versioning."""

    claim_id: str = Field(default_factory=lambda: _new_id("claim"))
    project_id: str | None = None
    run_id: str | None = None
    statement: str
    kind: EvidenceKind
    source: str | None = None
    source_trust: SourceTrustTier | None = None
    evidence: str | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    confidence: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    falsifiers: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    refs: list[str] = Field(default_factory=list)
    # Versioning: never silently mutate; create a new claim that supersedes the old one
    version: int = 1
    supersedes: str | None = None
    superseded_by: str | None = None
    lifecycle: ClaimLifecycle = ClaimLifecycle.ACTIVE
    evidence_strength: EvidenceStrength = EvidenceStrength.AI_CLAIM
    content_hash: str | None = None
    # Optional embedded math check request for deterministic verification
    math_check: dict[str, Any] | None = None
    computation_artifact_id: str | None = None
    agreement_type: AgreementType | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @property
    def canonical_id(self) -> str:
        """Stable identity: project/run/claim/version — not a filename."""
        proj = self.project_id or "unknown_project"
        run = self.run_id or "unknown_run"
        return f"{proj}/{run}/{self.claim_id}/v{self.version}"

    @model_validator(mode="after")
    def _assumptions_are_not_facts(self) -> Claim:
        if self.kind == EvidenceKind.FACT and not self.source and not self.evidence:
            raise ValueError(
                "FACT requires source or evidence; use ASSUMPTION or HYPOTHESIS otherwise"
            )
        # mock:// sources cannot be PRIMARY / silent FACT promotion
        if self.source and str(self.source).startswith("mock://"):
            if self.source_trust is None:
                self.source_trust = SourceTrustTier.STUB
            if self.source_trust != SourceTrustTier.STUB:
                raise ValueError("mock:// sources must have source_trust=STUB")
            if self.kind == EvidenceKind.FACT:
                raise ValueError("mock:// sources cannot be labeled FACT")
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
    check_report_ids: list[str] = Field(default_factory=list)
    agreement_type: AgreementType = AgreementType.CONSENSUS
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
    """Append-only engineering decision with graph traceability."""

    decision_id: str = Field(default_factory=lambda: _new_id("dec"))
    project_id: str | None = None
    run_id: str | None = None
    question: str
    hypothesis: str | None = None
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    agents_involved: list[str] = Field(default_factory=list)
    supporting_results: list[str] = Field(default_factory=list)
    contradicting_results: list[str] = Field(default_factory=list)
    accepted_claims: list[str] = Field(default_factory=list)
    rejected_claims: list[str] = Field(default_factory=list)
    disputed_claims: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    confidence: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    status: DecisionStatus = DecisionStatus.PROPOSED
    next_action: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _require_traceability_when_accepted(self) -> DecisionRecord:
        # PROPOSED may be incomplete; ACCEPTED must link evidence or claims
        if self.status == DecisionStatus.ACCEPTED:
            if not (self.accepted_claims or self.supporting_evidence or self.evidence):
                raise ValueError(
                    "ACCEPTED Decision requires accepted_claims, supporting_evidence, or evidence refs"
                )
        return self


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
    # Blind review package path (JSON) — verification/red team only
    review_bundle_path: str | None = None


class HitlRequest(BaseModel):
    reason: str
    options: list[str] = Field(default_factory=list)
    blocking: bool = True
    context: dict[str, Any] = Field(default_factory=dict)
    requested_action: str | None = None


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
    # Pending HITL payload for resume
    pending_hitl: dict[str, Any] | None = None
    adjudication_status: AdjudicationStatus | None = None


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


# --- V2: deterministic checks, review, runs, graph ---


class MathCheckRequest(BaseModel):
    """Deterministic numeric/recompute check — LLM is not the authority."""

    check_id: str = Field(default_factory=lambda: _new_id("mchk"))
    claim_id: str | None = None
    expression: str | None = None
    expected: float | None = None
    tolerance: float = 1e-6
    inputs: dict[str, float] = Field(default_factory=dict)
    # Simple unit tags (no Pint required): {"force": "N", "diameter": "m"}
    units: dict[str, str] = Field(default_factory=dict)
    # Optional sandbox recompute code; must print a single float
    code: str | None = None
    # Forbidden unit mistakes: if set, inputs with these unit labels fail
    required_units: dict[str, str] = Field(default_factory=dict)


class MathCheckResult(BaseModel):
    check_id: str
    passed: bool
    computed: float | None = None
    expected: float | None = None
    discrepancy: str | None = None
    agreement_type: AgreementType = AgreementType.INDEPENDENT_EVIDENCE
    details: dict[str, Any] = Field(default_factory=dict)


class DeterministicCheckReport(BaseModel):
    report_id: str = Field(default_factory=lambda: _new_id("chk"))
    results: list[MathCheckResult] = Field(default_factory=list)
    critical_failures: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results) and not self.critical_failures

    @property
    def has_critical_failure(self) -> bool:
        return bool(self.critical_failures) or any(not r.passed for r in self.results)


class BlindClaimView(BaseModel):
    """Claim fields safe for independent review (no author confidence/narrative)."""

    claim_id: str
    statement: str
    kind: EvidenceKind
    source: str | None = None
    source_trust: SourceTrustTier | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    falsifiers: list[str] = Field(default_factory=list)
    math_check: dict[str, Any] | None = None
    computation_artifact_id: str | None = None
    refs: list[str] = Field(default_factory=list)


class ReviewBundle(BaseModel):
    """Blind package for Verification and Red Team — no author confidence or peer reviews."""

    bundle_id: str = Field(default_factory=lambda: _new_id("rb"))
    run_id: str
    target_claim_ids: list[str] = Field(default_factory=list)
    claims: list[BlindClaimView] = Field(default_factory=list)
    computation_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    source_references: list[dict[str, Any]] = Field(default_factory=list)
    check_report: DeterministicCheckReport | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class SynthesisBundle(BaseModel):
    """Deterministic input for final report — LLM may polish prose only."""

    accepted_claims: list[dict[str, Any]] = Field(default_factory=list)
    rejected_claims: list[dict[str, Any]] = Field(default_factory=list)
    disputed_claims: list[dict[str, Any]] = Field(default_factory=list)
    verification_reports: list[dict[str, Any]] = Field(default_factory=list)
    red_team_reports: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    adjudication_status: AdjudicationStatus | None = None
    report_gate: str = "INCOMPLETE"  # PASS | DISPUTED | INCOMPLETE | INSUFFICIENT_EVIDENCE


class ComputationArtifact(BaseModel):
    """Immutable simulation/calculation record (never overwrite)."""

    artifact_id: str = Field(default_factory=lambda: _new_id("comp"))
    run_id: str
    kind: str = "simulation"  # simulation | calculation
    input_hash: str = "unknown"
    code_hash: str = "unknown"
    tool_version: str = "python.execute"
    started_at: datetime = Field(default_factory=_utc_now)
    finished_at: datetime | None = None
    status: str = "ok"
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    claim_ids: list[str] = Field(default_factory=list)


class RunBudget(BaseModel):
    max_iterations: int = 24
    max_agent_calls: int = 100
    max_tool_calls: int = 200
    max_runtime_seconds: float = 3600.0
    max_tokens: int = 500_000
    max_cost: float = 50.0
    agent_calls: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    cost_used: float = 0.0
    started_at: datetime = Field(default_factory=_utc_now)


class RunManifest(BaseModel):
    """Reproducibility package for a single lab run."""

    run_id: str
    project_id: str
    started_at: datetime = Field(default_factory=_utc_now)
    finished_at: datetime | None = None
    model_provider: str = "unknown"
    model_id: str = "unknown"
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    python_version: str = "unknown"
    dependency_version: str = "unknown"
    tool_versions: dict[str, str] = Field(default_factory=dict)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    git_commit: str = "unknown"
    configuration_hash: str = "unknown"
    manifest_hash: str | None = None
    budget: RunBudget | None = None
    final_state: str | None = None
    frozen: bool = False
    notes: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    node_id: str = Field(default_factory=lambda: _new_id("node"))
    node_type: GraphNodeType
    project_id: str | None = None
    run_id: str | None = None
    ref_id: str | None = None  # claim_id, report_id, etc.
    version: int = 1
    status: str = "ACTIVE"
    label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = None
    source_tier: SourceTrustTier | None = None
    trust_level: TrustLevel | None = None
    independence: AgreementType | None = None
    evidence_strength: EvidenceStrength | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class GraphEdge(BaseModel):
    edge_id: str = Field(default_factory=lambda: _new_id("edge"))
    edge_type: GraphEdgeType
    source_id: str
    target_id: str
    project_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class AdjudicationResult(BaseModel):
    status: AdjudicationStatus
    reasons: list[str] = Field(default_factory=list)
    verification_status: VerificationStatus | None = None
    red_team_max_severity: AttackSeverity | None = None
    deterministic_critical_failure: bool = False
    agreement_type: AgreementType = AgreementType.MIXED
