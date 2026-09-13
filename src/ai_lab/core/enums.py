"""Shared enumerations for workflow, evidence, and agents."""

from enum import Enum


class ProjectState(str, Enum):
    """Workflow states for a lab project run."""

    CREATED = "CREATED"
    UNDERSTANDING = "UNDERSTANDING"
    DECOMPOSITION = "DECOMPOSITION"
    RESEARCH = "RESEARCH"
    HYPOTHESIS = "HYPOTHESIS"
    ANALYSIS = "ANALYSIS"
    CALCULATION = "CALCULATION"
    SIMULATION = "SIMULATION"
    VERIFICATION = "VERIFICATION"
    RED_TEAM = "RED_TEAM"
    EXPERIMENT = "EXPERIMENT"  # extension point
    SYNTHESIS = "SYNTHESIS"
    DISPUTED = "DISPUTED"
    ITERATION_REQUIRED = "ITERATION_REQUIRED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    COMPLETED = "COMPLETED"


class VerificationStatus(str, Enum):
    """Agent-level review status. Numeric truth lives in CheckStatus."""

    PASS = "PASS"
    FAIL = "FAIL"
    DISPUTED = "DISPUTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class CheckStatus(str, Enum):
    """Deterministic verifier outcome — not overridable by the VerificationAgent LLM.

    Distinct from VerificationStatus: this is the numeric/unit check, not the
    agent's interpretation of the whole ReviewBundle.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPATIBLE_DIMENSIONS = "INCOMPATIBLE_DIMENSIONS"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    INVALID_INPUT = "INVALID_INPUT"
    EVALUATION_ERROR = "EVALUATION_ERROR"
    TIMEOUT = "TIMEOUT"


class EvidenceKind(str, Enum):
    """Strict typing of knowledge — ASSUMPTION must never silently become FACT.

    EVIDENCE_GAP is not an assumption: it records that required evidence was not
    obtained. Missing sources must not be rewritten as “we assume there is none”.
    """

    FACT = "FACT"
    HYPOTHESIS = "HYPOTHESIS"
    ASSUMPTION = "ASSUMPTION"
    CALCULATION = "CALCULATION"
    SIMULATION_RESULT = "SIMULATION_RESULT"
    EXPERIMENT_RESULT = "EXPERIMENT_RESULT"
    OPINION = "OPINION"
    INFERENCE = "INFERENCE"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class AgentRole(str, Enum):
    CHIEF_ENGINEER = "chief_engineer"
    RESEARCH = "research"
    THEORIST = "theorist"
    SIMULATION = "simulation"
    VERIFICATION = "verification"
    RED_TEAM = "red_team"
    # Extension points (not implemented as full agents in MVP)
    ENGINEERING_DESIGNER = "engineering_designer"
    EXPERIMENTAL_SCIENTIST = "experimental_scientist"


class TaskKind(str, Enum):
    """What a TaskGraph node is — not an AgentRole and not a CheckStatus.

    MODEL_BUILD / SIMULATION / SIMULATION_VERIFICATION are runtime-owned
    (no AgentRole): the solver is a deterministic computation, not an LLM.
    """

    AGENT = "agent"
    DETERMINISTIC_CHECK = "deterministic_check"
    ADJUDICATION = "adjudication"
    MODEL_BUILD = "model_build"
    SIMULATION = "simulation"
    SIMULATION_VERIFICATION = "simulation_verification"


class TaskStatus(str, Enum):
    """Execution status of a TaskGraph node.

    Distinct from CheckStatus / VerificationStatus / ClaimLifecycle:
    a task can FAIL to run even when no verification happened.
    """

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    HITL_REQUIRED = "HITL_REQUIRED"


class TaskGraphValidationReason(str, Enum):
    """Deterministic why a TaskGraph was accepted or rejected."""

    OK = "OK"
    DUPLICATE_TASK_ID = "DUPLICATE_TASK_ID"
    INVALID_GRAPH_ID = "INVALID_GRAPH_ID"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_ROLE = "UNKNOWN_ROLE"
    UNKNOWN_TASK_KIND = "UNKNOWN_TASK_KIND"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    SELF_DEPENDENCY = "SELF_DEPENDENCY"
    CYCLE = "CYCLE"
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_OUTPUT_SCHEMA = "INVALID_OUTPUT_SCHEMA"
    INDEPENDENCE_VIOLATION = "INDEPENDENCE_VIOLATION"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INVALID_TASK_BUDGET = "INVALID_TASK_BUDGET"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    FORBIDDEN_FIELD = "FORBIDDEN_FIELD"
    HITL_REQUIRED = "HITL_REQUIRED"
    MALFORMED_PROPOSAL = "MALFORMED_PROPOSAL"
    ROUTING_VIOLATION = "ROUTING_VIOLATION"
    UNKNOWN_SOLVER = "UNKNOWN_SOLVER"
    SOLVER_POLICY_VIOLATION = "SOLVER_POLICY_VIOLATION"
    # PR-04: TaskGraph must bind to EngineeringContract (version / investigation).
    CONTRACT_BINDING_VIOLATION = "CONTRACT_BINDING_VIOLATION"


class DecisionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"


class AttackSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TrustLevel(str, Enum):
    """Taint label for tool outputs — LLM must treat EXTERNAL/UNTRUSTED as data only."""

    TRUSTED = "TRUSTED"
    INTERNAL = "INTERNAL"
    UNTRUSTED = "UNTRUSTED"
    EXTERNAL = "EXTERNAL"


class SourceTrustTier(str, Enum):
    """Research source authenticity. mock:// is always STUB.

    This is the only trust classification. Descriptive source kinds
    (patent, blog, …) live in SourceKind and must not be treated as a
    parallel quality score.
    """

    STUB = "STUB"
    SECONDARY = "SECONDARY"
    PRIMARY = "PRIMARY"


class SourceKind(str, Enum):
    """What a retrieved document is — not how much we trust it."""

    MANUFACTURER_DOCS = "manufacturer_docs"
    SCIENTIFIC_ARTICLE = "scientific_article"
    PATENT = "patent"
    GOVERNMENT = "government"
    UNIVERSITY = "university"
    REVIEW_ARTICLE = "review_article"
    ENCYCLOPEDIA = "encyclopedia"
    BLOG = "blog"
    UNKNOWN = "unknown"
    STUB = "stub"


class AgreementType(str, Enum):
    """Consensus among agents is weaker than independent recompute/measurement."""

    CONSENSUS = "CONSENSUS"
    INDEPENDENT_EVIDENCE = "INDEPENDENT_EVIDENCE"
    MIXED = "MIXED"


class GraphNodeType(str, Enum):
    PROJECT = "PROJECT"
    RUN = "RUN"
    SOURCE = "SOURCE"
    EVIDENCE = "EVIDENCE"
    CLAIM = "CLAIM"
    ASSUMPTION = "ASSUMPTION"
    HYPOTHESIS = "HYPOTHESIS"
    CALCULATION = "CALCULATION"
    SIMULATION = "SIMULATION"
    EXPERIMENT = "EXPERIMENT"
    CHECK = "CHECK"
    VERIFICATION = "VERIFICATION"
    RED_TEAM = "RED_TEAM"
    ADJUDICATION = "ADJUDICATION"
    DECISION = "DECISION"
    CONCLUSION = "CONCLUSION"
    CONFLICT = "CONFLICT"
    # Plan provenance: one node per validated TaskGraph version (not per task)
    TASK_GRAPH = "TASK_GRAPH"


class GraphEdgeType(str, Enum):
    DERIVED_FROM = "DERIVED_FROM"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    TESTS = "TESTS"
    VERIFIED_BY = "VERIFIED_BY"
    REJECTED_BY = "REJECTED_BY"
    REFUTES = "REFUTES"
    DEPENDS_ON = "DEPENDS_ON"
    SUPERSEDES = "SUPERSEDES"
    PART_OF = "PART_OF"
    CREATED_IN = "CREATED_IN"
    USES = "USES"
    SAME_AS = "SAME_AS"
    ACCEPTS = "ACCEPTS"
    CITES = "CITES"
    DEMOTED_BY = "DEMOTED_BY"


class ClaimLifecycle(str, Enum):
    """Persistence / history state of a claim record (not epistemic support).

    Mapping vs ClaimSupportStatus (PR-05):
    - ACTIVE ↔ any support_status except REJECTED (still in the run store)
    - REJECTED ↔ support_status REJECTED (or lifecycle demotion after reject)
    - DISPUTED ↔ support_status CONTRADICTED (or open conflict)
    - SUPERSEDED / ARCHIVED / DEMOTED — history only; support_status frozen on the version
    """

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"
    ARCHIVED = "ARCHIVED"
    DEMOTED = "DEMOTED"


class ClaimSupportStatus(str, Enum):
    """Epistemic / synthesis gate for a claim — distinct from ClaimLifecycle.

    Synthesis must not treat PROPOSED / UNVERIFIED as proven quantitative truth.
    SUPPORTED requires evidence_ids and/or calculation_ids (fail loud otherwise).

    Mapping onto existing enums (no duplicate truth):
    - EvidenceKind = *what* the statement is (FACT, CALCULATION, …)
    - ClaimLifecycle = store lifecycle (ACTIVE, SUPERSEDED, …)
    - VerificationStatus / CheckStatus = agent / numeric check outcomes
    - ClaimSupportStatus = whether the claim may enter grounded synthesis
    """

    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"


class EvidenceType(str, Enum):
    """Provenance class of supporting evidence (attach to EvidenceRecord / source).

    Compatible with SourceTrustTier (how much we trust) and EvidenceKind (claim shape):
    LITERATURE ↔ retrieved source; CALCULATED ↔ ComputationArtifact;
    SIMULATED ↔ simulation artifact; EXPERIMENTAL ↔ experiment;
    ASSUMED ↔ typed assumption; USER_PROVIDED ↔ operator input.
    """

    LITERATURE = "LITERATURE"
    EXPERIMENTAL = "EXPERIMENTAL"
    CALCULATED = "CALCULATED"
    SIMULATED = "SIMULATED"
    ASSUMED = "ASSUMED"
    USER_PROVIDED = "USER_PROVIDED"


class ClaimVisibility(str, Enum):
    """What claim set an agent/query may see."""

    CURRENT_RUN = "CURRENT_RUN"
    PROJECT_HISTORY = "PROJECT_HISTORY"
    APPROVED_KNOWLEDGE = "APPROVED_KNOWLEDGE"


class EvidenceStrength(str, Enum):
    """Provenance classification — not a magic confidence score."""

    PRIMARY_EXPERIMENT = "PRIMARY_EXPERIMENT"
    PRIMARY_CALCULATION = "PRIMARY_CALCULATION"
    INDEPENDENT_RECOMPUTE = "INDEPENDENT_RECOMPUTE"
    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    SECONDARY_SOURCE = "SECONDARY_SOURCE"
    AI_CLAIM = "AI_CLAIM"
    CONSENSUS = "CONSENSUS"


class ConflictStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    ACCEPTED_A = "ACCEPTED_A"
    ACCEPTED_B = "ACCEPTED_B"
    BOTH_UNRESOLVED = "BOTH_UNRESOLVED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class AdjudicationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    DISPUTED = "DISPUTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class IndependenceLevel(str, Enum):
    """Architectural model-routing independence — not AgreementType.

    FULL / PARTIAL / NONE classify *model configuration diversity*.
    They must never be treated as INDEPENDENT_EVIDENCE (that requires
    actual recompute / independent supporting evidence).
    """

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"
    INVALID = "INVALID"


class SandboxStatus(str, Enum):
    """Outcome of one ComputeSandbox invocation — not TaskStatus / CheckStatus.

    A timeout or spawn failure is an execution outcome, not a scientific FAIL.
    """

    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    CPU_LIMIT = "CPU_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    NETWORK_DENIED = "NETWORK_DENIED"
    FILESYSTEM_DENIED = "FILESYSTEM_DENIED"
    INVALID_SPEC = "INVALID_SPEC"
    PROCESS_ERROR = "PROCESS_ERROR"
    NONZERO_EXIT = "NONZERO_EXIT"
    CANCELLED = "CANCELLED"
    UNSUPPORTED_LIMIT = "UNSUPPORTED_LIMIT"


class EnforcementLevel(str, Enum):
    """How strongly a sandbox capability is actually enforced on this platform."""

    HARD = "HARD"
    BEST_EFFORT = "BEST_EFFORT"
    UNSUPPORTED = "UNSUPPORTED"


class NetworkPolicy(str, Enum):
    """Requested network policy. REQUESTED is not the same as enforced isolation."""

    DENY = "deny"
    ALLOW = "allow"


class FilesystemPolicy(str, Enum):
    """Requested filesystem policy. Local subprocess cannot claim a hard OS jail."""

    RUN_SCOPED = "run_scoped"


class ReproductionVerdict(str, Enum):
    """Identity/output comparison of two ComputationArtifacts — not a scientific judgement."""

    REPRODUCTION_MATCH = "REPRODUCTION_MATCH"
    REPRODUCTION_MISMATCH = "REPRODUCTION_MISMATCH"
    IDENTITY_DIFFERENT = "IDENTITY_DIFFERENT"
    # Image digest / environment identity incomplete — not strong equivalence.
    PARTIAL_ENVIRONMENT = "PARTIAL_ENVIRONMENT"


class SimulationStatus(str, Enum):
    """Outcome of EngineeringSolver — not CheckStatus / TaskStatus / SandboxStatus.

    Solver failure means the simulation did not produce a trustworthy result.
    It does not mean the physical hypothesis is false.
    """

    SUCCESS = "SUCCESS"
    INVALID_MODEL = "INVALID_MODEL"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    SOLVER_ERROR = "SOLVER_ERROR"
    NON_CONVERGED = "NON_CONVERGED"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"
    TIMEOUT = "TIMEOUT"
    COMPUTATION_ERROR = "COMPUTATION_ERROR"


class ParameterTrust(str, Enum):
    """Provenance of an engineering parameter — never silent FACT promotion."""

    TRUSTED = "TRUSTED"
    INPUT_UNVERIFIED = "INPUT_UNVERIFIED"
    STUB = "STUB"


class ModelValidity(str, Enum):
    """Did the structured model pass deterministic validation? Not physics truth."""

    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class NumericalCorrectness(str, Enum):
    """Did the solver converge / recompute agree? Not real-world validity."""

    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    NON_CONVERGED = "NON_CONVERGED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class PhysicalValidity(str, Enum):
    """Empirical / domain status. Deterministic math PASS does not imply VALID."""

    UNASSESSED = "UNASSESSED"
    IN_DOMAIN = "IN_DOMAIN"
    OUT_OF_DOMAIN = "OUT_OF_DOMAIN"


class EvidenceConfidence(str, Enum):
    """How much the evidence graph may trust a simulation-backed claim."""

    STUB = "STUB"
    INPUT_UNVERIFIED = "INPUT_UNVERIFIED"
    VERIFIED_CALCULATION = "VERIFIED_CALCULATION"
    UNASSESSED = "UNASSESSED"


class ScopeStatus(str, Enum):
    """Investigation-scope gate. Not ProjectState and not AdjudicationStatus.

    SCOPE_ASSUMED means safe domain defaults were locked explicitly.
    SCOPE_NEEDS_CLARIFICATION pauses via existing HITL (AWAITING_HUMAN).
    """

    SCOPE_RESOLVED = "SCOPE_RESOLVED"
    SCOPE_NEEDS_CLARIFICATION = "SCOPE_NEEDS_CLARIFICATION"
    SCOPE_ASSUMED = "SCOPE_ASSUMED"
    SCOPE_UNRESOLVED = "SCOPE_UNRESOLVED"


class ResearchOutcome(str, Enum):
    """Diagnostic of one research attempt / the recovery loop.

    Distinct from AdjudicationStatus: a timeout is not “no evidence exists”.
    """

    RESEARCH_SUCCESS = "RESEARCH_SUCCESS"
    RESEARCH_PARTIAL = "RESEARCH_PARTIAL"
    RESEARCH_EMPTY = "RESEARCH_EMPTY"
    RESEARCH_PROVIDER_ERROR = "RESEARCH_PROVIDER_ERROR"
    RESEARCH_FILTERED = "RESEARCH_FILTERED"


class AssumptionKind(str, Enum):
    """Why an assumption exists — SCOPE_UNCERTAINTY must not hide as ASSUMPTION."""

    INPUT_ASSUMPTION = "INPUT_ASSUMPTION"
    MODEL_ASSUMPTION = "MODEL_ASSUMPTION"
    PARAMETER_ESTIMATE = "PARAMETER_ESTIMATE"
    SCOPE_ASSUMPTION = "SCOPE_ASSUMPTION"
    EVIDENCE_LIMITATION = "EVIDENCE_LIMITATION"


class ContractStatus(str, Enum):
    """EngineeringContract lifecycle. Distinct from ScopeStatus / ProjectState.

    READY = defined enough to investigate (not “everything known”).
    LOCKED = immutable for the active run; material change → new version.
    """

    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    LOCKED = "LOCKED"


class ProblemKind(str, Enum):
    """ScopeResolver taxonomy — how the posed problem should be investigated.

    Distinct from WorkflowProfile (depth) and TaskKind (graph node type).
    Policy/validator are authority; LLM may only propose.
    """

    CLOSED_NUMERIC = "CLOSED_NUMERIC"
    PARAMETRIC = "PARAMETRIC"
    DESIGN = "DESIGN"
    RESEARCH_REVIEW = "RESEARCH_REVIEW"
    EXPERIMENTAL = "EXPERIMENTAL"
    OPEN_ENDED = "OPEN_ENDED"


class LabErrorCode(str, Enum):
    """Stable machine-readable failure codes for deterministic lab gates.

    Distinct from CheckStatus / AdjudicationStatus — these are runtime isolation
    and contract errors, not engineering PASS/FAIL of a claim.
    """

    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    # Pipeline stages that need a contract must not run on DRAFT / NEEDS_CLARIFICATION.
    CONTRACT_NOT_READY = "CONTRACT_NOT_READY"
    # LOCKED contract cannot be mutated in place; use a version bump.
    CONTRACT_LOCKED = "CONTRACT_LOCKED"
    ILLEGAL_CONTRACT_TRANSITION = "ILLEGAL_CONTRACT_TRANSITION"


class FailureClass(str, Enum):
    """Taxonomy of why an iteration stalled (PR-06 stop/replan reasons).

    Maps from LabErrorCode / ResearchOutcome / adjudication text where practical.
    Dashboards / spider_silk 2.0 (PR-07) consume the same enum — do not fork.
    """

    USER_INPUT = "USER_INPUT"
    SCOPE = "SCOPE"
    PLANNING = "PLANNING"
    CONTEXT = "CONTEXT"
    UNIT = "UNIT"
    CALCULATION = "CALCULATION"
    SIMULATION = "SIMULATION"
    RESEARCH = "RESEARCH"
    VERIFICATION = "VERIFICATION"
    BUDGET = "BUDGET"
    PROVIDER = "PROVIDER"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class IterationAction(str, Enum):
    """Decision from IterationController after adjudication (PR-06)."""

    CONTINUE = "CONTINUE"
    REPLAN = "REPLAN"
    ASK_USER = "ASK_USER"
    STOP_INSUFFICIENT_EVIDENCE = "STOP_INSUFFICIENT_EVIDENCE"
    STOP_BUDGET = "STOP_BUDGET"
