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
    PASS = "PASS"
    FAIL = "FAIL"
    DISPUTED = "DISPUTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceKind(str, Enum):
    """Strict typing of knowledge — ASSUMPTION must never silently become FACT."""

    FACT = "FACT"
    HYPOTHESIS = "HYPOTHESIS"
    ASSUMPTION = "ASSUMPTION"
    CALCULATION = "CALCULATION"
    SIMULATION_RESULT = "SIMULATION_RESULT"
    EXPERIMENT_RESULT = "EXPERIMENT_RESULT"
    OPINION = "OPINION"
    INFERENCE = "INFERENCE"


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
    """Research source authenticity. mock:// is always STUB."""

    STUB = "STUB"
    SECONDARY = "SECONDARY"
    PRIMARY = "PRIMARY"


class AgreementType(str, Enum):
    """Consensus among agents is weaker than independent recompute/measurement."""

    CONSENSUS = "CONSENSUS"
    INDEPENDENT_EVIDENCE = "INDEPENDENT_EVIDENCE"
    MIXED = "MIXED"


class GraphNodeType(str, Enum):
    PROJECT = "PROJECT"
    RUN = "RUN"
    SOURCE = "SOURCE"
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
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"
    ARCHIVED = "ARCHIVED"
    DEMOTED = "DEMOTED"


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
