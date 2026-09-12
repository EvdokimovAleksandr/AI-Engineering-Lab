"""Task classifiers — propose TaskClassification; never finalize routing."""

from __future__ import annotations

import re
from typing import Any, Protocol

from ai_lab.task_routing.enums import EvidenceRequirement, WorkflowProfile
from ai_lab.task_routing.models import TaskClassification

# Keyword tables are data, not hidden policy — policy module still owns floors.
_SIMPLE_HINTS = (
    "heater",
    "нагрев",
    "нагревател",
    "liters",
    "литров",
    "mcδt",
    "mcΔt",
    "q = mc",
    "p = q/t",
    "heat capacity",
    "теплоёмкост",
    "теплоемкост",
    "water from",
    "воды с",
)
_STANDARD_HINTS = (
    "shaft",
    "вал",
    "diameter",
    "диаметр",
    "torque",
    "крут",
    "rpm",
    "об/мин",
    "yield strength",
    "запас прочност",
    "factor of safety",
    "steel",
    "стальн",
    "power transmission",
    "передач",
)
_RESEARCH_HINTS = (
    "spider silk",
    "паутин",
    "industrial production",
    "промышленн",
    "literature review",
    "competing hypotheses",
    "competing approaches",
    "bottleneck",
    "масштабирован",
    "research",
    "исследовать",
    "современные подходы",
    "production platforms",
)
_HIGH_RISK_HINTS = (
    "safety-critical",
    "life-critical",
    "hazard",
    "опасн",
    "explosive",
    "toxic",
    "ядовит",
    "nuclear",
    "fail-deadly",
    "high-voltage",
    "high voltage",
    "высоковольт",
    "pressure vessel",
    "сосуд под давлен",
)
_HIGH_UNCERTAINTY_HINTS = (
    "uncertain",
    "неопределён",
    "неопределен",
    "sparse evidence",
    "contradict",
    "противоречи",
    "no consensus",
    "unknown mechanism",
    "poorly characterized",
)


class TaskClassifier(Protocol):
    """Produces a proposal only — TaskRoutingPolicy decides."""

    name: str

    def classify(self, context: Any) -> TaskClassification: ...


def _haystack(context: Any) -> str:
    parts = [
        str(getattr(context, "project_id", "") or ""),
        str(getattr(context, "problem_text", "") or ""),
        str(getattr(context, "resolved_objective", "") or ""),
        str(getattr(context, "requirements_text", "") or ""),
        str(getattr(context, "assumptions_text", "") or ""),
    ]
    extra = getattr(context, "extra_data", None) or {}
    if isinstance(extra, dict):
        parts.extend(str(v) for v in extra.values())
    return "\n".join(parts).lower()


def _count_hits(text: str, hints: tuple[str, ...]) -> int:
    return sum(1 for h in hints if h.lower() in text)


class HeuristicTaskClassifier:
    """Deterministic keyword/structure classifier for offline tests and CI."""

    name = "heuristic"

    def classify(self, context: Any) -> TaskClassification:
        text = _haystack(context)
        simple_hits = _count_hits(text, _SIMPLE_HINTS)
        standard_hits = _count_hits(text, _STANDARD_HINTS)
        research_hits = _count_hits(text, _RESEARCH_HINTS)
        risk_hits = _count_hits(text, _HIGH_RISK_HINTS)
        unc_hits = _count_hits(text, _HIGH_UNCERTAINTY_HINTS)

        extra = getattr(context, "extra_data", None) or {}
        hint = str(extra.get("pipeline_hint") or "") if isinstance(extra, dict) else ""
        if hint == "research":
            research_hits += 2
        elif hint == "calculation":
            simple_hits += 2
        if research_hits >= max(simple_hits, standard_hits, 1):
            task_type = "research_review"
            domain = "materials_biotech"
            complexity = min(10, 7 + research_hits)
            uncertainty = min(10, 6 + unc_hits + research_hits // 2)
            workflow = WorkflowProfile.RESEARCH
            evidence = [
                EvidenceRequirement.RESEARCH_PLUS_VERIFICATION,
                EvidenceRequirement.FULL_RESEARCH_CYCLE,
            ]
            reasoning = (
                "Research-style wording (platforms, bottlenecks, competing approaches) "
                f"dominates (hits={research_hits})."
            )
        elif standard_hits >= max(simple_hits, 1):
            task_type = "preliminary_design"
            domain = "mechanical_engineering"
            # Keep proposal inside STANDARD band (3–5); policy may still escalate on risk.
            complexity = min(5, max(3, 3 + min(2, standard_hits // 2)))
            uncertainty = min(5, 2 + unc_hits)
            workflow = WorkflowProfile.STANDARD
            evidence = [EvidenceRequirement.DETERMINISTIC_PLUS_VERIFICATION]
            reasoning = (
                "Standard engineering design signals (shaft/torque/strength) "
                f"(hits={standard_hits})."
            )
        elif simple_hits >= 1 or _looks_like_closed_form(text):
            task_type = "deterministic_calculation"
            domain = "thermal_electrical"
            complexity = min(2, max(0, 1 + simple_hits // 3))
            uncertainty = min(2, unc_hits)
            workflow = WorkflowProfile.SIMPLE
            evidence = [EvidenceRequirement.DETERMINISTIC]
            reasoning = (
                "Closed-form thermal/electrical calculation signals "
                f"(hits={simple_hits})."
            )
        else:
            task_type = "general_engineering"
            domain = "general"
            complexity = 4
            uncertainty = min(5, 2 + unc_hits)
            workflow = WorkflowProfile.STANDARD
            evidence = [EvidenceRequirement.DETERMINISTIC_PLUS_VERIFICATION]
            reasoning = "No strong SIMPLE/RESEARCH signals; defaulting to STANDARD proposal."

        risk = min(10, risk_hits * 3)
        if risk_hits and risk < 7:
            risk = 7  # any explicit hazard cue floors at HIGH band for proposal
        if "critical" in text and risk_hits:
            risk = max(risk, 9)

        confidence = 0.55
        if max(simple_hits, standard_hits, research_hits) >= 2:
            confidence = 0.8
        if max(simple_hits, standard_hits, research_hits) >= 4:
            confidence = 0.9

        return TaskClassification(
            task_type=task_type,
            domain=domain,
            complexity=complexity,
            risk=risk,
            uncertainty=uncertainty,
            required_evidence=evidence,
            recommended_workflow=workflow,
            reasoning=reasoning,
            confidence=confidence,
        )


def _looks_like_closed_form(text: str) -> bool:
    """Cheap structural cue: explicit formulas + numeric units."""
    has_formula = bool(re.search(r"\b[qp]\s*=\s*", text) or "δt" in text or "Δt" in text.lower())
    has_numbers = bool(re.search(r"\d+\s*(l|литр|°c|c\b|min|минут|kw|вт)", text))
    return has_formula and has_numbers


class FixedTaskClassifier:
    """Test double: returns a pre-baked classification (policy still applies)."""

    name = "fixed"

    def __init__(self, classification: TaskClassification) -> None:
        self._classification = classification

    def classify(self, context: Any) -> TaskClassification:
        return self._classification.model_copy(deep=True)


def create_classifier(kind: str) -> TaskClassifier:
    key = (kind or "heuristic").strip().lower()
    if key == "heuristic":
        return HeuristicTaskClassifier()
    if key == "llm":
        # LLM classifier is an extension point — not implemented yet.
        # Fail loud rather than silently pretending heuristic is LLM.
        raise ValueError(
            "task_routing.classifier=llm is not implemented; use heuristic "
            "(LLM must not be the final routing authority anyway)"
        )
    raise ValueError(f"Unknown task_routing.classifier {kind!r}")
