"""Pydantic domain models for evidence, tasks, decisions, and agent I/O."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_lab.core.enums import (
    AdjudicationStatus,
    AgentRole,
    AgreementType,
    AttackSeverity,
    CheckStatus,
    ClaimLifecycle,
    DecisionStatus,
    EvidenceKind,
    EvidenceStrength,
    GraphEdgeType,
    GraphNodeType,
    IndependenceLevel,
    ProjectState,
    SourceTrustTier,
    TaskGraphValidationReason,
    TaskKind,
    TaskStatus,
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
    # Structured unit-aware spec (V2.2). Takes precedence over math_check when both set.
    verification_spec: dict[str, Any] | None = None
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


class BudgetSlice(BaseModel):
    """Planned caps for one task. Runtime accounting stays on RunBudget only."""

    model_config = ConfigDict(extra="forbid")

    max_agent_calls: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=0, ge=0)
    max_cost: float = Field(default=0.0, ge=0.0)


class TaskSpec(BaseModel):
    """Work unit in a validated TaskGraph (evolved from the original dispatch spec).

    Describes *what* to do, never a shell/python command. Extra keys are forbidden
    so LLM proposals cannot smuggle `command` / `script` / `cwd` into execution.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str = Field(default_factory=lambda: _new_id("task"))
    # Required for AGENT tasks; must be unset for runtime-owned kinds.
    role: AgentRole | None = None
    task_kind: TaskKind = TaskKind.AGENT
    objective: str
    # Existing field name; `input_node_ids` accepted on ingest for V2.3 proposals.
    inputs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("inputs", "input_node_ids"),
        description="Artifact ids / task ids this task may read (must be in depends_on if they are tasks)",
    )
    output_schema: str = "agent_result"
    depends_on: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    independence_group: str | None = Field(
        default=None,
        description="Tasks with the same group may run in parallel without sharing drafts",
    )
    budget_slice: BudgetSlice | None = None
    priority: int = 0
    # Stage-table compatibility metadata — not the execution DAG.
    state_context: ProjectState | None = None
    # Blind review package path — set by LabRuntime, never by the LLM planner.
    review_bundle_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def input_node_ids(self) -> list[str]:
        """Spec alias for `inputs` (existing project field name)."""
        return self.inputs

    @model_validator(mode="after")
    def _kind_and_role_agree(self) -> TaskSpec:
        if self.task_kind == TaskKind.AGENT:
            if self.role is None:
                raise ValueError("AGENT TaskSpec requires role")
        elif self.role is not None:
            raise ValueError(
                f"{self.task_kind.value} TaskSpec must not set an AgentRole "
                "(runtime-owned node, not an LLM agent)"
            )
        return self


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
    # Advisory only — LabRuntime never treats these as an execution plan.
    follow_up_tasks: list[TaskSpec] = Field(default_factory=list)
    hitl_request: HitlRequest | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TaskGraph(BaseModel):
    """Serializable DAG of TaskSpec nodes. Authoritative only after validation."""

    model_config = ConfigDict(extra="forbid")

    graph_id: str
    tasks: list[TaskSpec]
    version: int = Field(default=1, ge=1)
    supersedes: str | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskGraphProposal(BaseModel):
    """Untrusted structured planner output. Never executed without validation."""

    model_config = ConfigDict(extra="forbid")

    graph_id: str
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    supersedes: str | None = None
    reason: str | None = None
    requires_human_approval: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskGraphValidationResult(BaseModel):
    """Deterministic validator outcome. LLM cannot set `ok`."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    reason: TaskGraphValidationReason
    errors: list[str] = Field(default_factory=list)
    topo_order: list[str] = Field(default_factory=list)
    graph_hash: str | None = None


class TaskExecutionRecord(BaseModel):
    """Per-task execution ledger inside `.runs/<run_id>/planner/executions.json`."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: TaskStatus
    role: str | None = None
    task_kind: TaskKind = TaskKind.AGENT
    artifact_paths: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


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
    # Routing / provenance identity (optional so old constructors keep working)
    model_version: str | None = None
    routing_policy_version: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_hash: str | None = None
    prompt_hash: str | None = None
    response_hash: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Observability only — never a second RunBudget. Null if the provider did not report it.
    estimated_cost: float | None = None
    routing: dict[str, Any] = Field(default_factory=dict)


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
    # Same YAML dict pattern as sandbox/runtime — not a second config system.
    research: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    # V2.4a: typed in llm/config.py. Dict here so old YAML without these keys still loads.
    routing: dict[str, Any] = Field(default_factory=dict)
    independence: dict[str, Any] = Field(default_factory=dict)
    # V2.5: trusted solver registry / pipeline. Not accepted from UI or LLM.
    simulation: dict[str, Any] = Field(default_factory=dict)
    # Task Router (workflow profile). Distinct from routing: (LLM model routing).
    task_routing: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        allowed = {"mock", "cursor_sdk", "replay"}
        if v not in allowed:
            raise ValueError(f"provider must be one of {allowed}, got {v!r}")
        return v


# --- V2: deterministic checks, review, runs, graph ---


class Quantity(BaseModel):
    """Scalar with a Pint unit string. Empty unit means dimensionless."""

    value: float
    unit: str = ""


class ToleranceSpec(BaseModel):
    """Explicit numeric tolerance — no hidden epsilon.

    PASS iff abs(actual - expected) <= absolute + relative * abs(expected)
    after both sides are converted to a common unit. At least one of
    absolute / relative must be set.
    """

    absolute: Quantity | None = None
    relative: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _require_explicit_tolerance(self) -> ToleranceSpec:
        if self.absolute is None and self.relative is None:
            raise ValueError("ToleranceSpec requires absolute and/or relative")
        return self


class BoundsSpec(BaseModel):
    """Inclusive min/max on the computed actual value (unit-aware)."""

    minimum: Quantity | None = None
    maximum: Quantity | None = None


class SanityCheck(BaseModel):
    """Extension point: boolean condition over inputs/actual/expected. Not a physics engine."""

    name: str
    condition: str
    failure_message: str


class VerificationLimits(BaseModel):
    """Operational caps for in-process verification (sandbox-style, not a second RunBudget)."""

    max_computation_seconds: float = 2.0
    max_expression_chars: int = 2000
    max_ast_nodes: int = 200
    max_quantities: int = 32


class VerificationSpec(BaseModel):
    """Structured deterministic check. LLM may propose this; it must never execute it."""

    spec_id: str = Field(default_factory=lambda: _new_id("vspec"))
    claim_id: str | None = None
    inputs: dict[str, Quantity] = Field(default_factory=dict)
    expression: str | None = None
    expected: Quantity
    actual: Quantity | None = None
    tolerance: ToleranceSpec
    bounds: BoundsSpec | None = None
    sanity_checks: list[SanityCheck] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_expression_or_actual(self) -> VerificationSpec:
        if self.expression is None and self.actual is None:
            raise ValueError("VerificationSpec requires expression or actual")
        return self


class VerificationPolicy(BaseModel):
    """Trusted verification requirements — LLM cannot weaken these fields.

    Distinct from RoutingDecision flags: this is the contract for quantitative
    engineering evidence completeness (checks / outputs / dimensions).
    """

    model_config = ConfigDict(extra="forbid")

    verification_required: bool = True
    calculation_required: bool = True
    minimum_checks: int = Field(default=1, ge=0)
    required_outputs: list[str] = Field(default_factory=list)
    required_output_dimensions: dict[str, str] = Field(default_factory=dict)
    # Explicit opt-out only — never implied by empty check reports.
    verification_not_required: bool = False

    @model_validator(mode="after")
    def _opt_out_consistency(self) -> VerificationPolicy:
        if self.verification_not_required:
            # Explicit waiver: no mandatory checks.
            object.__setattr__(self, "verification_required", False)
            object.__setattr__(self, "minimum_checks", 0)
        elif self.verification_required and self.minimum_checks < 1:
            object.__setattr__(self, "minimum_checks", 1)
        return self


class CalculationSpec(BaseModel):
    """Contract between a calculation Task and ComputationArtifact outputs.

    LLM may propose objective/equations; policy-locked fields cannot be removed
    to make an irrelevant computation PASS.
    """

    model_config = ConfigDict(extra="ignore")

    spec_id: str = Field(default_factory=lambda: _new_id("cspec"))
    task_id: str | None = None
    run_id: str | None = None
    objective: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    expected_dimensions: dict[str, str] = Field(
        default_factory=dict,
        description="output_name → unit string (e.g. power → W)",
    )
    expected_relations: list[str] = Field(
        default_factory=list,
        description="Advisory equations (untrusted prose/symbols)",
    )
    domain: str | None = None
    model_kind: str | None = None
    verification_required: bool = True
    minimum_checks: int = Field(default=1, ge=0)
    # Names of fields locked by VerificationPolicy / runtime (not LLM-trustable).
    trusted_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComputationRelevanceResult(BaseModel):
    """Outcome of validate_computation_against_spec — not CheckStatus."""

    model_config = ConfigDict(extra="forbid")

    relevant: bool
    calculation_spec_id: str | None = None
    computation_artifact_id: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    missing_outputs: list[str] = Field(default_factory=list)
    dimension_mismatches: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class EvidenceCompletenessReport(BaseModel):
    """Deterministic gate: PASS requires all mandatory evidence present and green."""

    model_config = ConfigDict(extra="forbid")

    computation_complete: bool = False
    computation_relevant: bool = False
    verification_complete: bool = False
    required_checks_pass: bool = False
    provenance_complete: bool = False
    # V2.6.1: every locked/required output needs claim←computation←verification chain.
    required_output_coverage: bool = True
    # V2.6.1: optional benchmark acceptance (True when no contract applies).
    acceptance_passed: bool = True
    reasons: list[str] = Field(default_factory=list)
    relevance_results: list[ComputationRelevanceResult] = Field(default_factory=list)
    required_checks: int = 0
    executed_checks: int = 0

    @property
    def is_complete(self) -> bool:
        return (
            self.computation_complete
            and self.computation_relevant
            and self.verification_complete
            and self.required_checks_pass
            and self.provenance_complete
            and self.required_output_coverage
            and self.acceptance_passed
        )


class CheckStepResult(BaseModel):
    """One pipeline stage (normalize, compute, compare, bounds, sanity)."""

    name: str
    passed: bool
    status: CheckStatus | None = None
    message: str = ""


class VerificationProvenance(BaseModel):
    """Enough to replay why a Claim was marked verified — without the LLM."""

    verifier_version: str
    spec_id: str
    claim_id: str | None = None
    spec_hash: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    formula: str | None = None
    tolerance: dict[str, Any] = Field(default_factory=dict)
    numerical_policy: str = ""
    bounds: dict[str, Any] | None = None


class VerificationResult(BaseModel):
    """Structured deterministic outcome. Status is authoritative over any LLM prose."""

    result_id: str = Field(default_factory=lambda: _new_id("vres"))
    spec_id: str
    claim_id: str | None = None
    status: CheckStatus
    expected: Quantity | None = None
    actual: Quantity | None = None
    normalized_expected: Quantity | None = None
    normalized_actual: Quantity | None = None
    diagnostics: list[str] = Field(default_factory=list)
    checks: list[CheckStepResult] = Field(default_factory=list)
    provenance: VerificationProvenance | None = None

    @property
    def passed(self) -> bool:
        return self.status == CheckStatus.PASS


class MathCheckRequest(BaseModel):
    """Legacy numeric/recompute check — adapted into VerificationSpec for expressions.

    Unit *tags* (required_units) remain string equality for backward compatibility.
    New unit-aware work should use VerificationSpec + Pint.
    """

    check_id: str = Field(default_factory=lambda: _new_id("mchk"))
    claim_id: str | None = None
    expression: str | None = None
    expected: float | None = None
    tolerance: float = 1e-6
    inputs: dict[str, float] = Field(default_factory=dict)
    # Legacy unit tags (string equality via required_units). Prefer VerificationSpec.
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
    status: CheckStatus | None = None
    verification_result: VerificationResult | None = None

    @model_validator(mode="after")
    def _default_status_from_passed(self) -> MathCheckResult:
        if self.status is None:
            self.status = CheckStatus.PASS if self.passed else CheckStatus.FAIL
        return self


class DeterministicCheckReport(BaseModel):
    report_id: str = Field(default_factory=lambda: _new_id("chk"))
    results: list[MathCheckResult] = Field(default_factory=list)
    verification_results: list[VerificationResult] = Field(default_factory=list)
    critical_failures: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)

    @property
    def all_passed(self) -> bool:
        math_ok = bool(self.results) and all(r.passed for r in self.results)
        ver_ok = all(r.status == CheckStatus.PASS for r in self.verification_results)
        # A report with only verification_results (no legacy math rows) can still pass.
        has_any = bool(self.results) or bool(self.verification_results)
        return has_any and (not self.results or math_ok) and ver_ok and not self.critical_failures

    @property
    def has_critical_failure(self) -> bool:
        if self.critical_failures:
            return True
        if any(not r.passed for r in self.results):
            return True
        return any(r.status != CheckStatus.PASS for r in self.verification_results)


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
    verification_spec: dict[str, Any] | None = None
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
    """Deterministic input for final report — LLM may polish prose only.

    Narrative is separated from accepted/verified engineering results.
    """

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
    # V2.6: separate LLM wording from verified quantitative results
    narrative: str = ""
    verified_results: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)


class ComputationArtifact(BaseModel):
    """Immutable simulation/calculation record (never overwrite).

    Identity is `computation_hash` (code + inputs + environment + sandbox policy),
    not `code_hash` alone. Pre-V2.4b artifacts still load: new fields default to
    unknown / empty and are not a second artifact type.
    """

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
    # V2.4b reproducibility — optional so older JSON still validates
    environment_hash: str = "unknown"
    python_version: str | None = None
    dependency_hash: str = "unknown"
    sandbox_backend: str | None = None
    sandbox_policy_version: str | None = None
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    stdout_hash: str = "unknown"
    stderr_hash: str = "unknown"
    computation_hash: str = "unknown"
    duration_ms: float | None = None
    sandbox_status: str | None = None
    enforcement: dict[str, str] = Field(default_factory=dict)
    workspace: str | None = None
    # V2.4c Docker identity. Optional so local / pre-V2.4c artifacts still load.
    image_ref: str | None = None
    image_digest: str | None = None
    environment_reproducibility: str | None = None  # full | partial | unknown
    determinism: str | None = None  # unknown | seeded — never auto-patched
    # V2.6 calculation contract binding (optional for older artifacts)
    task_id: str | None = None
    calculation_spec_id: str | None = None
    objective: str | None = None
    # Structured outputs only — stdout text is never auto-promoted to claims.
    declared_outputs: dict[str, Any] = Field(default_factory=dict)
    input_claim_ids: list[str] = Field(default_factory=list)
    output_claim_ids: list[str] = Field(default_factory=list)


class RunBudget(BaseModel):
    max_iterations: int = 24
    max_agent_calls: int = 100
    max_tool_calls: int = 200
    max_runtime_seconds: float = 3600.0
    max_tokens: int = 500_000
    max_cost: float = 50.0
    agent_calls: int = 0
    tool_calls: int = 0
    # None = unknown usage (provider did not report); 0 = known zero spend.
    tokens_used: int | None = 0
    # Sticky: any LLM completion without usage marks the ledger unknown.
    tokens_unknown: bool = False
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
    # Validated TaskGraph identity (hash is over semantic contents, not JSON whitespace)
    task_graph_id: str | None = None
    task_graph_hash: str | None = None
    task_graph_version: int | None = None
    # V2.4a routing snapshot. Optional so pre-V2.4a manifests still load.
    routing_policy_version: str | None = None
    model_routing: dict[str, dict[str, Any]] = Field(default_factory=dict)
    independence_policy: dict[str, Any] | None = None
    # V2.4b run-level sandbox configuration (invocation details live on ComputationArtifact)
    sandbox_backend: str | None = None
    sandbox_policy_version: str | None = None
    sandbox_capabilities: dict[str, Any] | None = None
    # Task Router decision snapshot (workflow profile). Optional for older manifests.
    task_routing_decision: dict[str, Any] | None = None
    workflow_profile: str | None = None
    # V2.6 engineering outcome (PASS/FAIL/INSUFFICIENT_EVIDENCE) — not run lifecycle.
    engineering_outcome: str | None = None
    calculation_spec_ids: list[str] = Field(default_factory=list)


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
    # Provenance only — does not affect the verdict.
    routing_policy_version: str | None = None
    model_routing: dict[str, Any] | None = None
    independence_level: IndependenceLevel | None = None
    # V2.6 evidence completeness snapshot (optional for older artifacts)
    evidence_completeness: dict[str, Any] | None = None
    # Engineering outcome may differ from run lifecycle COMPLETED.
    engineering_outcome: AdjudicationStatus | None = None
