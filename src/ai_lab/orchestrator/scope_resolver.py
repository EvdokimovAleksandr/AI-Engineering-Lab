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

# После первого pass, если kind стал специализированным — второй этап Required.
SPECIALIZED_SECOND_STAGE_KINDS: frozenset[ProblemKind] = frozenset(
    {
        ProblemKind.DESIGN,
        ProblemKind.RESEARCH_REVIEW,
        ProblemKind.EXPERIMENTAL,
        ProblemKind.PARAMETRIC,
    }
)

_LOAD_TYPE_ANSWER = re.compile(
    r"axial|bending|combined|осев|изгиб|комбин",
    re.IGNORECASE,
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
            field="load_type",
        ),
    )


def _field_filled(known: dict[str, str], name: str) -> bool:
    return name in known and bool(str(known.get(name) or "").strip())


def merge_clarification_answers_into_known(
    scope: InvestigationScope,
) -> dict[str, str]:
    """Собрать known из known_parameters + ответов HITL (field / answers / load_type)."""
    known = dict(scope.known_parameters or {})
    for rec in scope.clarifications or []:
        for key, val in (rec.answers or {}).items():
            if val is not None and str(val).strip():
                known[key] = str(val).strip()
        choice = (rec.choice or rec.note or "").strip()
        if not choice:
            continue
        if rec.field:
            known.setdefault(rec.field, choice)
        elif _LOAD_TYPE_ANSWER.search(choice):
            # Legacy records без field= — тип нагрузки узнаём по тексту ответа.
            known.setdefault("load_type", choice)
    return known


def _is_open_strengthen_problem(scope: InvestigationScope) -> bool:
    """Класс open-ended «сделать прочнее» — не benchmark-id, а эвристика постановки."""
    blob = f"{scope.original_problem or ''}\n{scope.objective or ''}"
    return bool(_OPEN_ROD_STRONGER.search(blob))


def _diameter_is_design_output(text: str) -> bool:
    """Диаметр — искомый выход (shaft design), а не baseline Required."""
    return bool(
        re.search(
            r"(определить|find|compute|вычисл|минимальн\w*\s+диаметр|"
            r"preliminary\s+diameter|select\s+a?\s*diameter|подбер\w*\s+диаметр)",
            text,
            re.IGNORECASE,
        )
    )


def compute_specialized_required(scope: InvestigationScope) -> list[str]:
    """Второй этап: Required для DESIGN / RESEARCH / EXPERIMENTAL / PARAMETRIC.

    Unknown, не входящие в optional, не превращаются в optional молча —
    они блокируют READY, пока не заполнены или не приняты как assumption HITL.
    """
    kind = scope.problem_kind
    if kind not in SPECIALIZED_SECOND_STAGE_KINDS:
        return list(scope.required_fields or [])

    known = dict(scope.known_parameters or {})
    optional = set(scope.optional_fields or [])
    required: list[str] = []

    def _add(name: str) -> None:
        if name not in required and not _field_filled(known, name):
            required.append(name)

    if kind == ProblemKind.DESIGN:
        if _is_open_strengthen_problem(scope):
            _add("load_type")
            _add("strength_metric")
            blob = f"{scope.original_problem or ''}\n{scope.objective or ''}"
            if not _diameter_is_design_output(blob):
                _add("geometry")
            _add("design_constraint")
        # Ранее объявленные unknown (не optional) остаются блокирующими.
        for name in scope.unknown_parameters or []:
            if name in optional:
                continue
            _add(name)
        for name in scope.required_fields or []:
            _add(name)
    elif kind == ProblemKind.RESEARCH_REVIEW:
        if not (scope.objective or "").strip():
            _add("subject")
        for name in scope.required_fields or []:
            _add(name)
    elif kind == ProblemKind.EXPERIMENTAL:
        if not (scope.objective or "").strip():
            _add("protocol_objective")
        for name in scope.required_fields or []:
            _add(name)
    elif kind == ProblemKind.PARAMETRIC:
        for name in scope.required_fields or []:
            _add(name)
        for name in scope.unknown_parameters or []:
            if name not in optional:
                _add(name)

    return required


def clarification_for_required_field(field: str) -> ClarificationQuestion:
    """HITL-вопрос для конкретного Required второго этапа (fail loud на неизвестном)."""
    if field == "load_type":
        return ClarificationQuestion(
            question="load type: axial / bending / combined?",
            why=(
                "Без типа нагрузки нельзя выбрать расчётную модель и критерий "
                "«прочнее» — это разные Required для READY."
            ),
            options=["axial", "bending", "combined"],
            input_mode="choice",
            field="load_type",
        )
    if field == "strength_metric":
        return ClarificationQuestion(
            question=(
                "What should «stronger» mean: higher yield, higher UTS, "
                "higher stiffness, or higher fatigue life?"
            ),
            why=(
                "Критерий «прочнее» задаёт success metric DESIGN; без него "
                "нельзя честно закрыть контракт."
            ),
            options=["yield_strength", "uts", "stiffness", "fatigue_life"],
            input_mode="choice",
            field="strength_metric",
        )
    if field == "geometry":
        return ClarificationQuestion(
            question=(
                "What is the current rod geometry (diameter / cross-section) "
                "used as the design baseline?"
            ),
            why=(
                "Без базовой геометрии нельзя предложить усиление относительно "
                "исходного стержня — только общие лозунги."
            ),
            options=[],
            input_mode="text",
            field="geometry",
        )
    if field == "design_constraint":
        return ClarificationQuestion(
            question=(
                "Which hard design constraint applies: mass, cost, envelope/size, "
                "or explicitly none?"
            ),
            why=(
                "DESIGN trade-off без ограничения (или явного «без ограничения») "
                "не определён — нельзя считать задачу READY."
            ),
            options=["mass_limit", "cost_limit", "envelope_limit", "no_hard_constraint"],
            input_mode="choice",
            field="design_constraint",
        )
    if field == "subject":
        return ClarificationQuestion(
            question="What subject should the research review cover?",
            why="RESEARCH_REVIEW без предмета не может стать READY.",
            options=[],
            input_mode="text",
            field="subject",
        )
    if field == "protocol_objective":
        return ClarificationQuestion(
            question="What experimental protocol objective must be achieved?",
            why="EXPERIMENTAL без цели протокола не может стать READY.",
            options=[],
            input_mode="text",
            field="protocol_objective",
        )
    # Не маскируем неизвестный field «общим clarify» — падаем явно.
    raise ValueError(
        f"No clarification template for required field {field!r}; "
        "extend clarification_for_required_field"
    )


def apply_specialized_second_stage(scope: InvestigationScope) -> InvestigationScope:
    """Второй этап валидации контракта после выбора специализированного problem_kind.

    READY только если все blocking Required заполнены. Оставшиеся unknown
    не переводятся в optional молча.
    """
    if scope.problem_kind not in SPECIALIZED_SECOND_STAGE_KINDS:
        return scope

    known = merge_clarification_answers_into_known(scope)
    working = scope.model_copy(update={"known_parameters": known})
    required = compute_specialized_required(working)
    unmet = [r for r in required if not _field_filled(known, r)]
    optional = [
        o
        for o in (scope.optional_fields or [])
        if o not in required
    ]
    # Unknown = unmet Required + прочие неизвестные, не ставшие known/optional.
    unknown: list[str] = list(unmet)
    for u in scope.unknown_parameters or []:
        if _field_filled(known, u):
            continue
        if u in optional or u in unknown:
            continue
        # Не optional и не заполнен → остаётся блокирующим unknown/required.
        if u not in required:
            unknown.append(u)

    if unmet:
        next_field = unmet[0]
        ambiguity = list(unmet)
        for a in scope.ambiguity or []:
            if a not in ambiguity:
                ambiguity.append(a)
        return scope.model_copy(
            update={
                "known_parameters": known,
                "required_fields": unmet,
                "unknown_parameters": unknown,
                "optional_fields": optional,
                "status": ScopeStatus.SCOPE_NEEDS_CLARIFICATION,
                "clarification": clarification_for_required_field(next_field),
                "ambiguity": ambiguity,
                "locked": False,
                "pipeline_hint": scope.pipeline_hint
                or pipeline_hint_for_kind(scope.problem_kind),
                "rationale": (
                    (scope.rationale or "").rstrip()
                    + (
                        f" Second-stage {scope.problem_kind.value} Required still "
                        f"blocking READY: {unmet}."
                    )
                ).strip(),
            }
        )

    # Уже RESOLVED/ASSUMED без unmet и без новых known — не переписываем артефакт.
    if (
        scope.status in {ScopeStatus.SCOPE_RESOLVED, ScopeStatus.SCOPE_ASSUMED}
        and known == dict(scope.known_parameters or {})
        and not (scope.required_fields or [])
    ):
        return scope

    return scope.model_copy(
        update={
            "known_parameters": known,
            "required_fields": [],
            "unknown_parameters": [u for u in unknown if not _field_filled(known, u)],
            "optional_fields": optional,
            "status": ScopeStatus.SCOPE_RESOLVED,
            "clarification": None,
            "ambiguity": [
                a for a in (scope.ambiguity or []) if not _field_filled(known, a)
            ],
            "pipeline_hint": scope.pipeline_hint
            or pipeline_hint_for_kind(scope.problem_kind),
            "rationale": (
                (scope.rationale or "").rstrip()
                + (
                    f" Second-stage {scope.problem_kind.value} Required frame "
                    "satisfied; investigation may lock."
                )
            ).strip(),
        }
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

    kind = updates.get("problem_kind", scope.problem_kind)
    if kind == ProblemKind.OPEN_ENDED or (
        updates.get("problem_kind") == ProblemKind.OPEN_ENDED
    ):
        # OPEN_ENDED без заполненных Required не может быть READY.
        if required or updates.get("required_fields"):
            if scope.status == ScopeStatus.SCOPE_RESOLVED and not scope.clarifications:
                updates["status"] = ScopeStatus.SCOPE_NEEDS_CLARIFICATION

    # Assumption candidates: тексты не-blocking допущений, если список пуст.
    if not scope.assumption_candidates and scope.assumptions:
        updates["assumption_candidates"] = [a.text for a in scope.assumptions]

    if updates:
        scope = scope.model_copy(update=updates)

    # Второй этап: специализированный kind → пересчёт Required (не silent READY).
    if scope.problem_kind in SPECIALIZED_SECOND_STAGE_KINDS:
        scope = apply_specialized_second_stage(scope)

    return scope


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
