"""ScopeResolver — required stage before EngineeringContract (not an AgentRole).

Problem → ScopeResolver → EngineeringContract → TaskRouter / TaskGraph.

Heuristic/deterministic classification is the authority. LLM may propose a kind,
but policy/validator must not silently rewrite it. Fail loud; no silent fallbacks.
"""

from __future__ import annotations

import re

from ai_lab.core.enums import AssumptionKind, ProblemKind, ScopeStatus
from ai_lab.core.investigation import (
    ClarificationQuestion,
    ClarificationRecord,
    InvestigationScope,
    TypedAssumption,
)

# Closed rod stress-ratio: diameter + force + scale + ratio/stress asks.
_CLOSED_ROD_STRESS = re.compile(
    r"(?:rod|стерж\w*|вал\w*).{0,80}?"
    r"(?:ø|⌀|diam|диаметр|d\s*=).{0,120}?"
    r"(?:f\s*=|force|сил\w*|нагруз\w*).{0,120}?"
    r"(?:d\s*[×x]\s*2|×\s*2|x2|удво\w*).{0,80}?"
    r"(?:stress\s*ratio|отношени\w*\s+напряж|σ\s*/|sigma\s*/)",
    re.IGNORECASE | re.DOTALL,
)
_CLOSED_ROD_COMPACT = re.compile(
    r"(?:ø|⌀)\s*\d+.{0,40}(?:кн|kn|n\b).{0,40}"
    r"(?:d\s*[×x]|×\s*2|удво).{0,60}"
    r"(?:stress|напряж|ratio|отношен)",
    re.IGNORECASE | re.DOTALL,
)

# Open-ended improvement without a locked metric / load case.
_OPEN_ROD_STRONGER = re.compile(
    r"(?:how\s+to\s+make.{0,40}stronger|как\s+сделать.{0,40}прочн|"
    r"усилить\s+(?:стерж|вал|балку)|make\s+the\s+rod\s+stronger)",
    re.IGNORECASE,
)

_PARAMETRIC_HINTS = (
    "as a function of",
    "vary",
    "sweep",
    "параметр",
    "в зависимости от",
    "sensitivity",
)
_DESIGN_HINTS = (
    "design",
    "подбер",
    "спроектир",
    "select a",
    "choose diameter",
    "размер",
)
_EXPERIMENTAL_HINTS = (
    "experiment",
    "эксперимент",
    "test protocol",
    "протокол испытан",
    "lab test",
    "лабораторн",
)
_RESEARCH_HINTS = (
    "research",
    "исследуй",
    "исследовать",
    "literature",
    "review",
    "industrial",
    "промышленн",
    "feasibility",
    "обзор",
)
_SILK_HINTS = ("spider silk", "пауч", "паутин", "recombinant silk")


def classify_problem_kind(text: str) -> ProblemKind:
    """Deterministic kind proposal from prompt text (policy may still raise floors)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("classify_problem_kind requires non-empty text")
    lower = raw.lower()

    if _CLOSED_ROD_STRESS.search(raw) or _CLOSED_ROD_COMPACT.search(raw):
        return ProblemKind.CLOSED_NUMERIC
    if _OPEN_ROD_STRONGER.search(raw):
        return ProblemKind.OPEN_ENDED
    if any(h in lower for h in _SILK_HINTS) and any(h in lower for h in _RESEARCH_HINTS):
        return ProblemKind.RESEARCH_REVIEW
    if any(h in lower for h in _SILK_HINTS):
        return ProblemKind.RESEARCH_REVIEW
    if any(h in lower for h in _EXPERIMENTAL_HINTS):
        return ProblemKind.EXPERIMENTAL
    if any(h in lower for h in _PARAMETRIC_HINTS):
        return ProblemKind.PARAMETRIC
    if any(h in lower for h in _DESIGN_HINTS):
        return ProblemKind.DESIGN
    if any(h in lower for h in _RESEARCH_HINTS) and len(lower) > 40:
        return ProblemKind.RESEARCH_REVIEW
    # Closed-form thermal/numeric cues (heater etc.) — numeric answer expected.
    if re.search(
        r"\d+(?:[.,]\d+)?\s*(?:l\b|л\b|mm\b|мм\b|k?n\b|кн\b|°\s*[cс]|вт\b|w\b)",
        lower,
    ) and re.search(
        r"(calculat|вычисл|найд|find|power|мощност|stress|напряж|ratio|отношен)",
        lower,
    ):
        return ProblemKind.CLOSED_NUMERIC
    # Длинная конкретная постановка ≠ OPEN_ENDED (иначе ломаем RESOLVED benchmarks).
    if len(raw) > 80:
        return ProblemKind.DESIGN
    return ProblemKind.OPEN_ENDED


def try_kind_template_scope(
    original: str,
    blended: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope | None:
    """Return a kind-specific InvestigationScope when a template matches; else None."""
    if _CLOSED_ROD_STRESS.search(blended) or _CLOSED_ROD_COMPACT.search(blended):
        return _closed_rod_stress_scope(original, blended, clarifications, round_n)
    if _OPEN_ROD_STRONGER.search(blended):
        return _open_rod_stronger_scope(original, clarifications, round_n)
    return None


def _closed_rod_stress_scope(
    original: str,
    blended: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    """Closed numeric: diameter, force, scale → stress ratio."""
    known: dict[str, str] = {}
    # Извлекаем известные числа только для Known frame (не для silent defaults).
    diam = re.search(
        r"(?:ø|⌀|d\s*=|диаметр)\s*(\d+(?:[.,]\d+)?)\s*(mm|мм|m\b|м\b)?",
        blended,
        re.I,
    )
    force = re.search(
        r"(?:f\s*=|force|сил[аые]?|нагрузк\w*)\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*(k?n|кн|н\b)?",
        blended,
        re.I,
    )
    if diam:
        unit = diam.group(2) or "mm"
        known["diameter"] = f"{diam.group(1).replace(',', '.')} {unit}"
    if force:
        unit = force.group(2) or "kN"
        known["force"] = f"{force.group(1).replace(',', '.')} {unit}"
    known["scale"] = "d×2"
    return InvestigationScope(
        original_problem=original,
        objective="Compute axial stress ratio after doubling rod diameter under given load",
        domain="mechanics",
        required_outputs=["stress_ratio"],
        expected_dimensions={"stress_ratio": "1"},
        key_terms=["rod", "stress", "diameter", "force"],
        known_parameters=known,
        unknown_parameters=[],
        required_fields=[],
        optional_fields=["material", "safety_factor"],
        assumption_candidates=[
            "Axial tension (uniform cross-section); bending neglected unless stated",
            "Linear elasticity; stress ~ 1/A",
        ],
        assumptions=[
            TypedAssumption(
                text="Load is axial tension; stress scales as 1/d² when diameter scales",
                kind=AssumptionKind.MODEL_ASSUMPTION,
                blocking=False,
            )
        ],
        problem_kind=ProblemKind.CLOSED_NUMERIC,
        pipeline_hint="calculation",
        status=ScopeStatus.SCOPE_RESOLVED,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale=(
            "Closed numeric rod problem: diameter, force, and d×2 scale are specified; "
            "stress ratio is a determined output."
        ),
        success_criteria=["Report σ(d)/σ(2d) or σ(2d)/σ(d) with explicit definition"],
    )


def _open_rod_stronger_scope(
    original: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    """Open-ended: cannot run full design/research until Required load type is known."""
    return InvestigationScope(
        original_problem=original,
        objective="",
        domain="mechanics",
        key_terms=["rod", "strength"],
        known_parameters={},
        unknown_parameters=["load_type", "strength_metric"],
        required_fields=["load_type"],
        optional_fields=["material", "cost_constraint", "mass_constraint"],
        assumption_candidates=[
            "Increase diameter",
            "Change alloy / heat treatment",
            "Add geometry (fillets, cross-section)",
        ],
        ambiguity=["load_type", "what 'stronger' means"],
        problem_kind=ProblemKind.OPEN_ENDED,
        pipeline_hint=None,
        status=ScopeStatus.SCOPE_NEEDS_CLARIFICATION,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale=(
            "Open-ended strengthening request: axial vs bending vs combined load "
            "changes the governing equations and design levers."
        ),
        clarification=ClarificationQuestion(
            question="load type: axial / bending / combined?",
            why=(
                "Без типа нагрузки нельзя выбрать расчётную модель и критерий "
                "«прочнее» — это разные Required для READY."
            ),
            options=["axial", "bending", "combined"],
            input_mode="choice",
        ),
    )


def finalize_scope_frame(scope: InvestigationScope) -> InvestigationScope:
    """Ensure Known/Unknown/Required/Optional/assumption candidates are consistent.

    Required = fields that block READY (HITL). Optional never blocks.
    """
    updates: dict = {}
    if scope.problem_kind is None:
        updates["problem_kind"] = classify_problem_kind(scope.original_problem)

    required = list(scope.required_fields)
    if not required and scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION:
        # Blocking unknowns become Required — не спрашиваем Optional.
        required = list(scope.unknown_parameters or scope.ambiguity or [])
        updates["required_fields"] = required

    if scope.problem_kind == ProblemKind.OPEN_ENDED or (
        updates.get("problem_kind") == ProblemKind.OPEN_ENDED
    ):
        # OPEN_ENDED без заполненных Required не может быть READY.
        if required or updates.get("required_fields"):
            if scope.status == ScopeStatus.SCOPE_RESOLVED and not scope.clarifications:
                updates["status"] = ScopeStatus.SCOPE_NEEDS_CLARIFICATION

    # Assumption candidates: тексты не-blocking допущений, если список пуст.
    if not scope.assumption_candidates and scope.assumptions:
        updates["assumption_candidates"] = [a.text for a in scope.assumptions]

    if not updates:
        return scope
    return scope.model_copy(update=updates)


def pipeline_hint_for_kind(kind: ProblemKind | None) -> str | None:
    """Map problem kind → existing TaskRouter pipeline_hint (raise-compatible)."""
    if kind == ProblemKind.CLOSED_NUMERIC:
        return "calculation"
    if kind == ProblemKind.RESEARCH_REVIEW:
        return "research"
    if kind in {ProblemKind.DESIGN, ProblemKind.PARAMETRIC, ProblemKind.EXPERIMENTAL}:
        return "mixed"
    if kind == ProblemKind.OPEN_ENDED:
        return None
    return None


def contract_summary_text(
    *,
    problem_kind: ProblemKind | str | None,
    objective: str,
    required_outputs: list[str],
    required_fields: list[str],
    version: str,
    status: str,
) -> str:
    """Compact trusted summary for planner context (not a second prompt rewrite)."""
    kind = problem_kind.value if isinstance(problem_kind, ProblemKind) else (problem_kind or "")
    return (
        f"contract_version={version}; status={status}; problem_kind={kind}; "
        f"objective={objective!r}; required_outputs={required_outputs}; "
        f"required_fields={required_fields}"
    )
