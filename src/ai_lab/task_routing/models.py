"""Structured task classification and final routing decision models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_lab.task_routing.enums import (
    ComplexityBand,
    EvidenceRequirement,
    RiskBand,
    UncertaintyBand,
    WorkflowProfile,
)


def complexity_band(score: int) -> ComplexityBand:
    if score <= 2:
        return ComplexityBand.SIMPLE
    if score <= 5:
        return ComplexityBand.STANDARD
    if score <= 8:
        return ComplexityBand.COMPLEX
    return ComplexityBand.RESEARCH


def risk_band(score: int) -> RiskBand:
    if score <= 2:
        return RiskBand.LOW
    if score <= 5:
        return RiskBand.MEDIUM
    if score <= 8:
        return RiskBand.HIGH
    return RiskBand.CRITICAL


def uncertainty_band(score: int) -> UncertaintyBand:
    if score <= 2:
        return UncertaintyBand.LOW
    if score <= 5:
        return UncertaintyBand.MEDIUM
    if score <= 8:
        return UncertaintyBand.HIGH
    return UncertaintyBand.VERY_HIGH


def _score_0_10(v: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"score must be int 0..10, got {v!r}")
    if v < 0 or v > 10:
        raise ValueError(f"score must be in [0, 10], got {v}")
    return v


class TaskClassification(BaseModel):
    """LLM/heuristic proposal. Never the final authority — policy validates it."""

    model_config = ConfigDict(extra="forbid")

    task_type: str
    domain: str
    complexity: int
    risk: int
    uncertainty: int
    required_evidence: list[EvidenceRequirement] = Field(default_factory=list)
    recommended_workflow: WorkflowProfile
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("complexity", "risk", "uncertainty")
    @classmethod
    def _scores(cls, v: int) -> int:
        return _score_0_10(v)

    @field_validator("task_type", "domain", "reasoning")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        text = (v or "").strip()
        if not text:
            raise ValueError("field must be non-empty")
        return text

    @property
    def complexity_band(self) -> ComplexityBand:
        return complexity_band(self.complexity)

    @property
    def risk_band(self) -> RiskBand:
        return risk_band(self.risk)

    @property
    def uncertainty_band(self) -> UncertaintyBand:
        return uncertainty_band(self.uncertainty)


class PolicyOverride(BaseModel):
    """One deterministic rule application recorded for audit."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    reason: str
    raised_workflow: WorkflowProfile | None = None
    raised_evidence: list[EvidenceRequirement] = Field(default_factory=list)
    require_hitl: bool = False


class RoutingDecision(BaseModel):
    """Final routing after policy validation. Authoritative for pipeline selection."""

    model_config = ConfigDict(extra="forbid")

    classification: TaskClassification
    final_workflow: WorkflowProfile
    final_evidence: list[EvidenceRequirement]
    require_hitl: bool = False
    require_independent_review: bool = True
    require_red_team: bool = True
    # V2.6: quantitative engineering always needs calculation + deterministic checks.
    require_calculation: bool = False
    require_verification: bool = False
    policy_overrides: list[PolicyOverride] = Field(default_factory=list)
    policy_version: str = "1"
    classifier_id: str = "heuristic"
    # True when policy raised workflow/evidence above the classifier proposal.
    policy_escalated: bool = False
    notes: list[str] = Field(default_factory=list)

    def public_dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
