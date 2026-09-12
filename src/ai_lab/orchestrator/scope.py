"""Investigation scope resolution — Chief Engineer / orchestrator capability.

Not a new AgentRole. Deterministic heuristics decide whether the question is
answerable; HITL is used only when ambiguity would materially change the result.
"""

from __future__ import annotations

import hashlib
import re
from ai_lab.core.enums import AssumptionKind, ScopeStatus
from ai_lab.core.investigation import (
    ClarificationQuestion,
    ClarificationRecord,
    InvestigationScope,
    ResearchRequirement,
    TypedAssumption,
)
from ai_lab.core.models import HitlRequest
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# Safe engineering defaults that do not change the *meaning* of a heater problem.
_WATER_DENSITY = TypedAssumption(
    text="Water density approximated as 1 kg/L",
    kind=AssumptionKind.PARAMETER_ESTIMATE,
    blocking=False,
)
_WATER_CP = TypedAssumption(
    text="Specific heat of water approximated as 4180 J/(kg·K)",
    kind=AssumptionKind.PARAMETER_ESTIMATE,
    blocking=False,
)

_STOP = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "to",
    "for",
    "in",
    "on",
    "this",
    "that",
    "как",
    "что",
    "для",
    "при",
    "это",
    "этот",
    "эта",
    "можно",
    "нужно",
    "сколько",
}

_HEATER_HINTS = (
    "heater",
    "нагрев",
    "нагревател",
    "heat the water",
    "нагреть",
    "нагреть воду",
    "нагреть 20",
    "мощност",
    "power for",
    "q = mc",
    "p = q/t",
)
_VOLUME_RE = re.compile(
    r"(?P<n>\d+(?:[.,]\d+)?)\s*(?:l\b|л\b|литр|liter|litre)",
    re.IGNORECASE,
)
_TEMP_PAIR_RE = re.compile(
    r"(?:с|from)\s*(?P<t0>-?\d+(?:[.,]\d+)?)\s*°?\s*[cсf]?\b.{0,24}?"
    r"(?:до|to)\s*(?P<t1>-?\d+(?:[.,]\d+)?)\s*°?\s*[cсf]?",
    re.IGNORECASE | re.DOTALL,
)
_TIME_RE = re.compile(
    r"(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>мин(?:ут)?|min(?:ute)?s?|час(?:ов|а)?|hours?|h\b|сек|sec)",
    re.IGNORECASE,
)
_LOSS_RE = re.compile(r"(?P<n>\d+(?:[.,]\d+)?)\s*%")

_STRENGTH_AMBIGUOUS = re.compile(
    r"(насколько\s+проч\w*|how\s+strong|strength\s+of\s+this|"
    r"прочност\w*\s+(этого|материал)|"
    r"how\s+strong\s+is\s+(this|the)\s+material)",
    re.IGNORECASE,
)
_STRENGTH_SPECIFIC = re.compile(
    r"(tensile|yield\s+strength|compressive|fracture\s+toughness|fatigue\s+strength|"
    r"ударн\w*\s+вязкост|предел\s+прочности|предел\s+текучести|toughness|elongation)",
    re.IGNORECASE,
)

_SILK_HINTS = ("spider silk", "пауч", "паутин", "recombinant silk")
_RESEARCH_HINTS = (
    "research",
    "исследуй",
    "исследовать",
    "industrial",
    "промышленн",
    "feasibility",
    "перспективн",
    "literature",
)


def original_problem_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def lock_scope(scope: InvestigationScope) -> InvestigationScope:
    """Mark resolved/assumed scope immutable for downstream planner/agents."""
    if scope.status not in {ScopeStatus.SCOPE_RESOLVED, ScopeStatus.SCOPE_ASSUMED}:
        logger.error("Refusing to lock unresolved scope status=%s", scope.status.value)
        raise ValueError(f"Cannot lock scope in status {scope.status.value}")
    return scope.model_copy(update={"locked": True})


def hitl_request_for_scope(scope: InvestigationScope) -> HitlRequest:
    """Map a clarification question onto the existing HITL contract."""
    q = scope.clarification
    if q is None:
        raise ValueError("Scope has no clarification question")
    return HitlRequest(
        reason=q.question,
        options=list(q.options),
        blocking=True,
        requested_action="clarify_scope",
        context={
            "kind": "scope_clarification",
            "question": q.question,
            "why": q.why,
            "input_mode": q.input_mode,
            "scope_id": scope.scope_id,
            "round": scope.clarification_round,
            "original_problem": scope.original_problem,
        },
    )


def apply_clarification(
    scope: InvestigationScope,
    *,
    choice: str | None,
    note: str = "",
    answers: dict[str, str] | None = None,
) -> InvestigationScope:
    """Record the user answer without mutating original_problem."""
    record = ClarificationRecord(
        round=scope.clarification_round + 1,
        choice=choice,
        note=note or "",
        answers=dict(answers or {}),
    )
    return scope.model_copy(
        update={
            "clarification_round": record.round,
            "clarifications": list(scope.clarifications) + [record],
            "locked": False,
        }
    )


def resolve_scope(
    original_problem: str,
    *,
    prior: InvestigationScope | None = None,
    max_clarification_rounds: int = 2,
) -> InvestigationScope:
    """Deterministic scope gate. original_problem is never rewritten."""
    text = (original_problem or "").strip()
    if not text:
        logger.error("Scope resolution called with empty original_problem")
        raise ValueError("original_problem must be non-empty")

    original = prior.original_problem if prior is not None else text
    if prior is not None and prior.original_problem != original:
        # Defence: callers must not swap the immutable user prompt.
        logger.error("Attempt to replace original_problem during scope resolution")
        raise ValueError("original_problem is immutable")

    clarifications = list(prior.clarifications) if prior is not None else []
    round_n = prior.clarification_round if prior is not None else 0
    scope_id = prior.scope_id if prior is not None else None

    blended = _plain(_blend_for_heuristics(original, clarifications))
    scope = _heuristics(original, blended, clarifications, round_n)
    if scope_id:
        scope = scope.model_copy(update={"scope_id": scope_id})

    if clarifications:
        scope = finalize_after_clarification(scope)

    if (
        scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
        and round_n >= max_clarification_rounds
    ):
        logger.info(
            "Clarification budget exhausted (%s/%s); marking SCOPE_UNRESOLVED",
            round_n,
            max_clarification_rounds,
        )
        scope = scope.model_copy(
            update={
                "status": ScopeStatus.SCOPE_UNRESOLVED,
                "rationale": (
                    scope.rationale
                    + " Clarification budget exhausted; the investigation cannot "
                    "lock a precise objective."
                ),
                "clarification": None,
            }
        )
    return scope


def _plain(text: str) -> str:
    """Strip markdown emphasis so numeric extractors see 20°C, not **20°C**."""
    return re.sub(r"[*_`#]+", " ", text)


def _blend_for_heuristics(original: str, clarifications: list[ClarificationRecord]) -> str:
    """Resolver input: original plus user answers. Original stays stored separately."""
    parts = [original]
    for rec in clarifications:
        chunk = " ".join(
            x for x in (rec.choice, rec.note, " ".join(rec.answers.values())) if x
        )
        if chunk.strip():
            parts.append(f"User clarification (round {rec.round}): {chunk}")
    return "\n".join(parts)


def _heuristics(
    original: str,
    blended: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    lower = blended.lower()

    if _is_heater(lower):
        return _heater_scope(original, blended, clarifications, round_n)
    if _STRENGTH_AMBIGUOUS.search(blended) and not _STRENGTH_SPECIFIC.search(blended):
        return _ambiguous_strength_scope(original, clarifications, round_n)
    if _looks_like_named_research(lower):
        return _research_scope(original, blended, clarifications, round_n)
    if not clarifications and _too_vague(blended):
        return _vague_scope(original, clarifications, round_n)
    return _default_resolved(original, blended, clarifications, round_n)


def _is_heater(lower: str) -> bool:
    has_heat = any(h in lower for h in _HEATER_HINTS)
    has_water = "вод" in lower or "water" in lower or _VOLUME_RE.search(lower) is not None
    return has_heat and has_water


def _heater_scope(
    original: str,
    blended: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    volume = _match_num(_VOLUME_RE, blended)
    temps = _TEMP_PAIR_RE.search(blended)
    duration = _TIME_RE.search(blended)
    losses = _LOSS_RE.search(blended)

    known: dict[str, str] = {}
    unknown: list[str] = []
    if volume:
        known["volume"] = f"{volume} L"
    else:
        unknown.append("volume")
    if temps:
        known["t_initial"] = temps.group("t0").replace(",", ".")
        known["t_final"] = temps.group("t1").replace(",", ".")
    else:
        unknown.extend(["t_initial", "t_final"])
    if duration:
        known["duration"] = f"{duration.group('n')} {duration.group('u')}"
    else:
        unknown.append("duration")
    if losses:
        known["losses"] = f"{losses.group('n')}%"

    assumptions = [_WATER_DENSITY, _WATER_CP]
    if not losses:
        # Missing loss factor changes the number, not the physical question.
        assumptions.append(
            TypedAssumption(
                text="Heat losses omitted (treated as 0%) unless the user specifies otherwise",
                kind=AssumptionKind.PARAMETER_ESTIMATE,
                blocking=False,
            )
        )

    blocking = [u for u in unknown if u in {"t_initial", "t_final", "duration", "volume"}]
    if blocking:
        return InvestigationScope(
            original_problem=original,
            objective="Calculate required heater power to heat a known water volume",
            domain="thermal",
            required_outputs=["power"],
            expected_dimensions={"power": "W"},
            key_terms=["heater", "water", "power", "heat capacity"],
            known_parameters=known,
            unknown_parameters=blocking,
            assumptions=assumptions,
            ambiguity=blocking,
            pipeline_hint="calculation",
            status=ScopeStatus.SCOPE_NEEDS_CLARIFICATION,
            clarification_round=round_n,
            clarifications=clarifications,
            rationale=(
                "Heater power depends on temperature rise and heating time. "
                "Those parameters materially change the numerical answer."
            ),
            clarification=ClarificationQuestion(
                question=(
                    "При какой начальной и конечной температуре и за какое время "
                    "нужно нагреть воду?"
                ),
                why=(
                    "Без ΔT и времени мощность не определена: "
                    "это разные инженерные задачи, а не одно допущение."
                ),
                options=[],
                input_mode="text",
            ),
        )

    return InvestigationScope(
        original_problem=original,
        objective="Calculate required heater power for the specified water heating duty",
        domain="thermal",
        required_outputs=["power"],
        expected_dimensions={"power": "W"},
        key_terms=["heater", "water", "power", "heat capacity"],
        known_parameters=known,
        unknown_parameters=[],
        assumptions=assumptions,
        success_criteria=["Power in watts from Q=mcΔT and P=Q/t with explicit losses"],
        pipeline_hint="calculation",
        status=ScopeStatus.SCOPE_RESOLVED,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale="Closed-form heater duty with volume, temperatures, and duration specified.",
        locked=False,
    )


def _ambiguous_strength_scope(
    original: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    return InvestigationScope(
        original_problem=original,
        objective="Evaluate material strength (metric not yet chosen)",
        domain="materials",
        required_outputs=[],
        key_terms=["strength", "material"],
        ambiguity=["strength metric"],
        unknown_parameters=["strength_metric"],
        pipeline_hint="research",
        status=ScopeStatus.SCOPE_NEEDS_CLARIFICATION,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale=(
            "The term “strength” is ambiguous: tensile, yield, toughness, and "
            "fatigue require different evidence and experiments."
        ),
        clarification=ClarificationQuestion(
            question=(
                "Под «прочностью» вы имеете в виду предел прочности при растяжении, "
                "предел текучести, ударную вязкость или все эти характеристики?"
            ),
            why=(
                "Эти варианты существенно меняют критерии успеха и набор "
                "необходимых источников."
            ),
            options=[
                "Предел прочности при растяжении (UTS)",
                "Предел текучести",
                "Ударная вязкость",
                "Все перечисленное",
            ],
            input_mode="choice",
        ),
    )


def _research_scope(
    original: str,
    blended: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    lower = blended.lower()
    silk = any(h in lower for h in _SILK_HINTS)
    if silk:
        reqs = [
            ResearchRequirement(topic="recombinant / host protein production"),
            ResearchRequirement(topic="fiber spinning and post-processing"),
            ResearchRequirement(topic="mechanical properties of produced fiber"),
            ResearchRequirement(topic="industrial scale-up constraints"),
            ResearchRequirement(topic="production economics / bottlenecks"),
        ]
        return InvestigationScope(
            original_problem=original,
            objective="Assess technical feasibility of industrial spider-silk production",
            domain="biomaterials",
            key_terms=[
                "spider silk",
                "recombinant",
                "fiber spinning",
                "industrial production",
            ],
            evidence_requirements=reqs,
            required_outputs=[],
            success_criteria=[
                "Cover manufacturing, spinning, properties, scale-up, and bottlenecks"
            ],
            out_of_scope=[
                "detailed business plan",
                "commercial market forecast",
            ],
            pipeline_hint="research",
            status=ScopeStatus.SCOPE_RESOLVED,
            clarification_round=round_n,
            clarifications=clarifications,
            rationale=(
                "The industrial-production question is specific enough to lock "
                "research topics without silently choosing a single process route."
            ),
        )

    subject = _subject_phrase(original)
    reqs = [
        ResearchRequirement(topic="current approaches"),
        ResearchRequirement(topic="constraints / bottlenecks"),
        ResearchRequirement(topic="evidence quality / open questions"),
    ]
    return InvestigationScope(
        original_problem=original,
        objective=f"Investigate {subject}" if subject else "Investigate the posed research question",
        domain="research",
        key_terms=_key_terms(blended),
        evidence_requirements=reqs,
        pipeline_hint="research",
        status=ScopeStatus.SCOPE_RESOLVED,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale="Research objective named a subject; required topics are locked.",
    )


def _vague_scope(
    original: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    return InvestigationScope(
        original_problem=original,
        objective="",
        ambiguity=["subject", "required outputs"],
        unknown_parameters=["subject", "success_criteria"],
        pipeline_hint=None,
        status=ScopeStatus.SCOPE_NEEDS_CLARIFICATION,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale="The prompt does not name a subject or a measurable objective.",
        clarification=ClarificationQuestion(
            question=(
                "Что именно нужно установить: какой материал/систему исследовать "
                "и какой результат считать достаточным?"
            ),
            why="Без предмета и критерия успеха лаборатория не может выбрать pipeline.",
            options=[],
            input_mode="text",
        ),
    )


def _default_resolved(
    original: str,
    blended: str,
    clarifications: list[ClarificationRecord],
    round_n: int,
) -> InvestigationScope:
    """Long, named problems (benchmarks) that do not match a special template."""
    first = original.strip().split("\n", 1)[0].strip("# ").strip()
    return InvestigationScope(
        original_problem=original,
        objective=first or "Formalize and investigate the posed engineering problem",
        key_terms=_key_terms(blended),
        pipeline_hint="mixed",
        status=ScopeStatus.SCOPE_RESOLVED,
        clarification_round=round_n,
        clarifications=clarifications,
        rationale="The problem statement is specific enough to plan work without blocking questions.",
    )


def _too_vague(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text.strip())
    if len(compact) < 24:
        return True
    # Demonstrative without a named subject: "исследуй этот материал".
    if re.search(r"(этот|этого|this)\s+(материал|material|веществ)", compact, re.I):
        if not re.search(r"[A-ZА-Я][a-zа-я]{3,}", compact.replace("Исследуй", "")):
            return True
    return False


def _looks_like_named_research(lower: str) -> bool:
    if any(h in lower for h in _SILK_HINTS):
        return True
    # Demonstrative "this material" is not a named subject — ask, don't invent one.
    if re.search(r"(этот|этого|this)\s+(материал|material|веществ)", lower):
        return False
    if any(h in lower for h in _RESEARCH_HINTS) and len(lower) > 40:
        return True
    return False


def _match_num(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    return m.group("n").replace(",", ".")


def _subject_phrase(text: str) -> str:
    line = text.strip().split("\n", 1)[0].strip("# ").strip()
    return line[:180]


def _key_terms(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-zА-Яа-яёЁ0-9\-]{4,}", text.lower())
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if tok in _STOP or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= 8:
            break
    return out


def strength_choice_to_outputs(choice: str) -> tuple[str, list[str], dict[str, str]]:
    """Map a HITL strength choice to locked required outputs."""
    c = (choice or "").lower()
    if "все" in c or "all" in c:
        return (
            "Evaluate tensile strength, yield strength, and impact toughness",
            ["uts", "yield_strength", "impact_toughness"],
            {"uts": "Pa", "yield_strength": "Pa"},
        )
    if "текуч" in c or "yield" in c:
        return (
            "Evaluate yield strength",
            ["yield_strength"],
            {"yield_strength": "Pa"},
        )
    if "удар" in c or "tough" in c or "вязк" in c:
        return (
            "Evaluate impact toughness",
            ["impact_toughness"],
            {},
        )
    return (
        "Evaluate ultimate tensile strength",
        ["uts"],
        {"uts": "Pa"},
    )


def finalize_after_clarification(scope: InvestigationScope) -> InvestigationScope:
    """Apply the last user choice onto locked fields when heuristics still need help."""
    if not scope.clarifications:
        return scope
    last = scope.clarifications[-1]
    choice = last.choice or last.note
    strengthish = (
        scope.domain == "materials"
        or "strength" in (scope.objective or "").lower()
        or "проч" in (scope.original_problem or "").lower()
    )
    if strengthish and choice:
        objective, outputs, dims = strength_choice_to_outputs(choice)
        return scope.model_copy(
            update={
                "objective": objective,
                "required_outputs": outputs,
                "expected_dimensions": dims,
                "domain": "materials",
                "status": ScopeStatus.SCOPE_RESOLVED,
                "clarification": None,
                "ambiguity": [],
                "unknown_parameters": [],
                "pipeline_hint": "research",
                "rationale": (
                    "The term “strength” was ambiguous. The investigation now "
                    f"focuses on: {objective}."
                ),
            }
        )
    return scope
