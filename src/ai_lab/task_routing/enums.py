"""Task-routing enumerations — distinct from LLM model routing and CheckStatus."""

from enum import Enum


class WorkflowProfile(str, Enum):
    """Named execution profile compiled into a TaskGraph template."""

    SIMPLE = "SIMPLE"
    STANDARD = "STANDARD"
    COMPLEX = "COMPLEX"
    RESEARCH = "RESEARCH"


class EvidenceRequirement(str, Enum):
    """Minimum evidence depth the policy demands for a task."""

    DETERMINISTIC = "DETERMINISTIC"
    DETERMINISTIC_PLUS_VERIFICATION = "DETERMINISTIC_PLUS_VERIFICATION"
    VERIFICATION_PLUS_REDTEAM = "VERIFICATION_PLUS_REDTEAM"
    RESEARCH_PLUS_VERIFICATION = "RESEARCH_PLUS_VERIFICATION"
    FULL_RESEARCH_CYCLE = "FULL_RESEARCH_CYCLE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class ComplexityBand(str, Enum):
    SIMPLE = "SIMPLE"
    STANDARD = "STANDARD"
    COMPLEX = "COMPLEX"
    RESEARCH = "RESEARCH"


class RiskBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class UncertaintyBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class EvalVerdict(str, Enum):
    """Benchmark / router evaluation outcome — not CheckStatus."""

    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
