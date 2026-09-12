"""YAML-backed TaskRoutingPolicy — deterministic; LLM cannot waive floors."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_lab.core.models import LabConfig
from ai_lab.task_routing.enums import EvidenceRequirement, WorkflowProfile
from ai_lab.task_routing.models import (
    PolicyOverride,
    RoutingDecision,
    TaskClassification,
)

# Rank: higher = more expensive / thorough. Policy may only raise, never lower.
WORKFLOW_RANK: dict[WorkflowProfile, int] = {
    WorkflowProfile.SIMPLE: 0,
    WorkflowProfile.STANDARD: 1,
    WorkflowProfile.COMPLEX: 2,
    WorkflowProfile.RESEARCH: 3,
}

EVIDENCE_RANK: dict[EvidenceRequirement, int] = {
    EvidenceRequirement.DETERMINISTIC: 0,
    EvidenceRequirement.DETERMINISTIC_PLUS_VERIFICATION: 1,
    EvidenceRequirement.VERIFICATION_PLUS_REDTEAM: 2,
    EvidenceRequirement.RESEARCH_PLUS_VERIFICATION: 3,
    EvidenceRequirement.FULL_RESEARCH_CYCLE: 4,
    EvidenceRequirement.HUMAN_REVIEW: 5,
}


def max_workflow(a: WorkflowProfile, b: WorkflowProfile) -> WorkflowProfile:
    return a if WORKFLOW_RANK[a] >= WORKFLOW_RANK[b] else b


def max_evidence(
    items: list[EvidenceRequirement],
) -> list[EvidenceRequirement]:
    """Keep unique evidence requirements sorted by rank (ascending)."""
    best: dict[EvidenceRequirement, int] = {}
    for item in items:
        best[item] = EVIDENCE_RANK[item]
    return [e for e, _ in sorted(best.items(), key=lambda kv: kv[1])]


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complexity_simple_max: int = 2
    complexity_standard_max: int = 5
    complexity_complex_max: int = 8
    risk_low_max: int = 2
    risk_medium_max: int = 5
    risk_high_max: int = 8
    uncertainty_low_max: int = 2
    uncertainty_medium_max: int = 5
    uncertainty_high_max: int = 8


class EscalationRule(BaseModel):
    """One configurable IF → THEN rule. Evaluated in list order."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    # Match conditions (all present conditions must hold).
    risk_gte: int | None = None
    uncertainty_gte: int | None = None
    complexity_gte: int | None = None
    # all_low: complexity/risk/uncertainty each <= low_max
    all_low: bool = False
    # Evidence forced into final set when matched.
    force_evidence: list[EvidenceRequirement] = Field(default_factory=list)
    min_workflow: WorkflowProfile | None = None
    # When true and all_low matches, set workflow to SIMPLE (subject to later max()).
    prefer_simple: bool = False
    require_hitl: bool = False
    reason: str = ""

    @field_validator("rule_id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        text = (v or "").strip()
        if not text or ".." in text or "/" in text or "\\" in text:
            raise ValueError(f"Invalid rule_id: {v!r}")
        return text


class TaskRoutingPolicy(BaseModel):
    """Deterministic floors over classifier proposals. Loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    enabled: bool = True
    classifier: str = "heuristic"  # heuristic | llm (llm proposes only)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    rules: list[EscalationRule] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _version_ok(cls, v: str) -> str:
        if not v or ".." in v or "/" in v or "\\" in v:
            raise ValueError(f"Invalid task_routing policy version: {v!r}")
        return v

    @field_validator("classifier")
    @classmethod
    def _classifier_ok(cls, v: str) -> str:
        allowed = {"heuristic", "llm"}
        if v not in allowed:
            raise ValueError(f"task_routing.classifier must be one of {allowed}, got {v!r}")
        return v

    def apply(self, classification: TaskClassification) -> RoutingDecision:
        """Raise workflow/evidence floors; never lower below classifier + rules."""
        final_wf = classification.recommended_workflow
        evidence = list(classification.required_evidence)
        overrides: list[PolicyOverride] = []
        require_hitl = False
        notes: list[str] = []
        low_max = self.thresholds.risk_low_max

        for rule in self.rules:
            if not self._matches(rule, classification, low_max=low_max):
                continue
            raised_wf: WorkflowProfile | None = None
            if rule.min_workflow is not None:
                before = final_wf
                final_wf = max_workflow(final_wf, rule.min_workflow)
                if final_wf != before:
                    raised_wf = final_wf
            if rule.prefer_simple and rule.all_low:
                # Prefer SIMPLE only when no prior escalation raised above SIMPLE.
                if WORKFLOW_RANK[final_wf] <= WORKFLOW_RANK[WorkflowProfile.SIMPLE]:
                    final_wf = WorkflowProfile.SIMPLE
            if rule.force_evidence:
                evidence.extend(rule.force_evidence)
            if rule.require_hitl:
                require_hitl = True
            overrides.append(
                PolicyOverride(
                    rule_id=rule.rule_id,
                    reason=rule.reason or f"matched {rule.rule_id}",
                    raised_workflow=raised_wf,
                    raised_evidence=list(rule.force_evidence),
                    require_hitl=rule.require_hitl,
                )
            )

        # Safety: HUMAN_REVIEW evidence always implies HITL.
        evidence = max_evidence(evidence)
        if EvidenceRequirement.HUMAN_REVIEW in evidence:
            require_hitl = True
            notes.append("HUMAN_REVIEW evidence forces HITL")

        # Evidence depth implies minimum workflow (policy floor, not classifier whim).
        evidence_floor = self._workflow_floor_from_evidence(evidence)
        if WORKFLOW_RANK[evidence_floor] > WORKFLOW_RANK[final_wf]:
            notes.append(
                f"evidence floor raised workflow {final_wf.value} → {evidence_floor.value}"
            )
            final_wf = evidence_floor

        # High risk never stays on SIMPLE even if complexity is low.
        if classification.risk > self.thresholds.risk_low_max and final_wf == WorkflowProfile.SIMPLE:
            final_wf = WorkflowProfile.STANDARD
            notes.append("risk above LOW blocks SIMPLE workflow")

        proposed_rank = WORKFLOW_RANK[classification.recommended_workflow]
        final_rank = WORKFLOW_RANK[final_wf]
        policy_escalated = final_rank > proposed_rank or any(
            EVIDENCE_RANK[e] > max(
                (EVIDENCE_RANK[x] for x in classification.required_evidence),
                default=-1,
            )
            for e in evidence
        )

        require_red_team = final_wf in {
            WorkflowProfile.COMPLEX,
            WorkflowProfile.RESEARCH,
        } or EvidenceRequirement.VERIFICATION_PLUS_REDTEAM in evidence
        require_independent_review = final_wf != WorkflowProfile.SIMPLE or require_red_team
        if EvidenceRequirement.DETERMINISTIC_PLUS_VERIFICATION in evidence:
            require_independent_review = True
        if EvidenceRequirement.HUMAN_REVIEW in evidence:
            require_independent_review = True
            require_red_team = True

        # SIMPLE path: only deterministic evidence unless policy raised.
        if final_wf == WorkflowProfile.SIMPLE and not require_independent_review:
            require_red_team = False

        return RoutingDecision(
            classification=classification,
            final_workflow=final_wf,
            final_evidence=evidence,
            require_hitl=require_hitl,
            require_independent_review=require_independent_review,
            require_red_team=require_red_team,
            policy_overrides=overrides,
            policy_version=self.version,
            classifier_id=self.classifier,
            policy_escalated=policy_escalated,
            notes=notes,
        )

    def _matches(
        self,
        rule: EscalationRule,
        classification: TaskClassification,
        *,
        low_max: int,
    ) -> bool:
        if rule.all_low:
            return (
                classification.complexity <= low_max
                and classification.risk <= low_max
                and classification.uncertainty <= low_max
            )
        ok = False
        if rule.risk_gte is not None:
            if classification.risk < rule.risk_gte:
                return False
            ok = True
        if rule.uncertainty_gte is not None:
            if classification.uncertainty < rule.uncertainty_gte:
                return False
            ok = True
        if rule.complexity_gte is not None:
            if classification.complexity < rule.complexity_gte:
                return False
            ok = True
        # Rule with no numeric predicates and not all_low never matches.
        return ok

    def _workflow_floor_from_evidence(
        self, evidence: list[EvidenceRequirement]
    ) -> WorkflowProfile:
        floor = WorkflowProfile.SIMPLE
        for item in evidence:
            if item in {
                EvidenceRequirement.RESEARCH_PLUS_VERIFICATION,
                EvidenceRequirement.FULL_RESEARCH_CYCLE,
            }:
                floor = max_workflow(floor, WorkflowProfile.RESEARCH)
            elif item == EvidenceRequirement.VERIFICATION_PLUS_REDTEAM:
                floor = max_workflow(floor, WorkflowProfile.COMPLEX)
            elif item == EvidenceRequirement.DETERMINISTIC_PLUS_VERIFICATION:
                floor = max_workflow(floor, WorkflowProfile.STANDARD)
            elif item == EvidenceRequirement.HUMAN_REVIEW:
                floor = max_workflow(floor, WorkflowProfile.COMPLEX)
        return floor


def default_rules() -> list[EscalationRule]:
    """Built-in rule set used when YAML omits `rules` (still overridable via config)."""
    return [
        EscalationRule(
            rule_id="critical_risk",
            risk_gte=9,
            min_workflow=WorkflowProfile.COMPLEX,
            force_evidence=[
                EvidenceRequirement.VERIFICATION_PLUS_REDTEAM,
                EvidenceRequirement.HUMAN_REVIEW,
            ],
            require_hitl=True,
            reason="CRITICAL risk mandates enhanced verification + HITL",
        ),
        EscalationRule(
            rule_id="high_risk",
            risk_gte=7,
            min_workflow=WorkflowProfile.COMPLEX,
            force_evidence=[EvidenceRequirement.VERIFICATION_PLUS_REDTEAM],
            reason="HIGH risk requires red-team verification",
        ),
        EscalationRule(
            rule_id="high_uncertainty",
            uncertainty_gte=7,
            min_workflow=WorkflowProfile.RESEARCH,
            force_evidence=[EvidenceRequirement.RESEARCH_PLUS_VERIFICATION],
            reason="HIGH uncertainty increases research/evidence requirements",
        ),
        EscalationRule(
            rule_id="research_complexity",
            complexity_gte=9,
            min_workflow=WorkflowProfile.RESEARCH,
            force_evidence=[EvidenceRequirement.FULL_RESEARCH_CYCLE],
            reason="RESEARCH-band complexity requires full research cycle",
        ),
        EscalationRule(
            rule_id="complex_complexity",
            complexity_gte=7,
            min_workflow=WorkflowProfile.COMPLEX,
            force_evidence=[EvidenceRequirement.VERIFICATION_PLUS_REDTEAM],
            reason="COMPLEX-band complexity requires full lab verification",
        ),
        EscalationRule(
            rule_id="all_axes_low",
            all_low=True,
            prefer_simple=True,
            force_evidence=[EvidenceRequirement.DETERMINISTIC],
            reason="Low complexity+risk+uncertainty → SIMPLE candidate",
        ),
    ]


def task_routing_policy_from_config(config: LabConfig) -> TaskRoutingPolicy:
    """Typed view over LabConfig.task_routing. Missing section → safe defaults."""
    raw = dict(getattr(config, "task_routing", None) or {})
    thresholds_raw = raw.get("thresholds") or {}
    if not isinstance(thresholds_raw, dict):
        raise ValueError("task_routing.thresholds must be a mapping")
    thresholds = Thresholds.model_validate(thresholds_raw)
    rules_raw = raw.get("rules")
    if rules_raw is None:
        rules = default_rules()
    elif isinstance(rules_raw, list):
        rules = [EscalationRule.model_validate(r) for r in rules_raw]
    else:
        raise ValueError("task_routing.rules must be a list")
    return TaskRoutingPolicy(
        version=str(raw.get("version") or "1"),
        enabled=bool(raw.get("enabled", True)),
        classifier=str(raw.get("classifier") or "heuristic"),
        thresholds=thresholds,
        rules=rules,
    )


def validate_task_routing_policy(policy: TaskRoutingPolicy) -> list[str]:
    """Fail-before-run checks. Empty list means OK."""
    errors: list[str] = []
    seen: set[str] = set()
    for rule in policy.rules:
        if rule.rule_id in seen:
            errors.append(f"duplicate rule_id {rule.rule_id!r}")
        seen.add(rule.rule_id)
        if (
            rule.risk_gte is None
            and rule.uncertainty_gte is None
            and rule.complexity_gte is None
            and not rule.all_low
        ):
            errors.append(f"rule {rule.rule_id!r} has no match predicates")
    return errors
