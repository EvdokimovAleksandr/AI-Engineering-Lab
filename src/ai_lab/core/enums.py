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
