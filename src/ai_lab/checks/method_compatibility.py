"""Method/Domain Compatibility Gate (PR-B).

Детерминированный семантический контракт: CalculationSpec не может
формально биндиться к task_id и при этом решать чужую инженерную задачу.
LLM-суждение не используется — только явные домены / методы / I/O маркеры.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.core.models import CalculationSpec
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# Канонические домены задачи (не путать с свободной строкой CalculationSpec.domain).
DOMAIN_MECHANICAL_SHAFT = "mechanical_shaft"
DOMAIN_MECHANICAL_ROD = "mechanical_rod"
DOMAIN_THERMAL_HEATING = "thermal_heating"
DOMAIN_BIOMATERIALS_FIBER = "biomaterials_fiber"
DOMAIN_GENERAL = "general"

# Семейства методов расчёта (из objective + I/O, не из LLM narrative).
METHOD_FIBER_STRESS = "fiber_stress"
METHOD_HEATER_POWER = "heater_power"
METHOD_SHAFT_MECHANICS = "shaft_mechanics"
METHOD_ROD_STRESS = "rod_stress"
METHOD_UNKNOWN = "unknown"

# Коды жёсткого отказа — видны в reasons / audit.
CODE_DOMAIN_MISMATCH = "DOMAIN_MISMATCH"
CODE_METHOD_MISMATCH = "METHOD_MISMATCH"
CODE_OBJECTIVE_MISMATCH = "OBJECTIVE_MISMATCH"
CODE_INPUT_MISMATCH = "INPUT_MISMATCH"
CODE_OUTPUT_MISMATCH = "OUTPUT_MISMATCH"

# Явно разрешённые методы на домен. Неизвестный метод на известном домене → отказ.
_ALLOWED_METHODS: dict[str, frozenset[str]] = {
    DOMAIN_MECHANICAL_SHAFT: frozenset({METHOD_SHAFT_MECHANICS}),
    DOMAIN_MECHANICAL_ROD: frozenset({METHOD_ROD_STRESS, METHOD_SHAFT_MECHANICS}),
    DOMAIN_THERMAL_HEATING: frozenset({METHOD_HEATER_POWER}),
    DOMAIN_BIOMATERIALS_FIBER: frozenset({METHOD_FIBER_STRESS, METHOD_ROD_STRESS}),
}

# Синонимы домена из scope / contract (до текстовой эвристики).
_DOMAIN_ALIASES: dict[str, str] = {
    "thermal": DOMAIN_THERMAL_HEATING,
    "thermal_heating": DOMAIN_THERMAL_HEATING,
    "thermal_electrical": DOMAIN_THERMAL_HEATING,
    "biomaterials": DOMAIN_BIOMATERIALS_FIBER,
    "materials_biotech": DOMAIN_BIOMATERIALS_FIBER,
    "mechanical_shaft": DOMAIN_MECHANICAL_SHAFT,
    "mechanical_rod": DOMAIN_MECHANICAL_ROD,
}

_SHAFT_CUES = re.compile(
    r"\bshaft\b|вал[аеу]?\b|круглого\s+вала|preliminary\s+diameter|"
    r"крутящ\w*\s+момент|torsional|rpm|об/?мин|"
    r"16\s*\*?\s*t\s*/\s*\(?\s*pi|"
    r"\u03c4\s*=\s*16",
    re.IGNORECASE,
)
_ROD_CUES = re.compile(
    r"\brod\b|стерж\w*|make.{0,40}stronger|сделать.{0,40}прочн|"
    r"усилить|stress\s*ratio|осев\w*\s+нагруз|axial\s+load",
    re.IGNORECASE,
)
_HEATER_CUES = re.compile(
    r"heater|нагрев|нагреть|water\s+volume|объ[её]м\w*\s+вод|"
    r"heat(?:ing)?\s+power|мощност\w*\s+нагревател",
    re.IGNORECASE,
)
_FIBER_CUES = re.compile(
    r"spider\s*silk|пауч\w*|паутин|recombinant\s+silk|fiber\s+spinning|"
    r"biomaterial|шёлк|шелк",
    re.IGNORECASE,
)

# Objective / method tokens → семейство.
_FIBER_METHOD_RE = re.compile(
    r"fiber|silk|spider|calculate_fiber|tensile_fiber",
    re.IGNORECASE,
)
_HEATER_METHOD_RE = re.compile(
    r"heater|heating_power|calculate_heater|thermal_power|heat_duty",
    re.IGNORECASE,
)
_SHAFT_METHOD_RE = re.compile(
    r"torsional_stress|bending_stress|combined_loading|buckling|"
    r"shaft_diameter|calculate_shaft|shaft_stress|torque.*diameter|"
    r"diameter.*shaft|preliminary_diameter",
    re.IGNORECASE,
)
_ROD_METHOD_RE = re.compile(
    r"axial_stress|rod_stress|stress_ratio|calculate_rod|"
    r"bending_stress|combined_loading",
    re.IGNORECASE,
)

_FIBER_INPUTS = frozenset({"force_n", "diameter_m", "fiber_diameter", "fiber_force"})
_FIBER_OUTPUTS = frozenset({"stress_gpa", "fiber_stress", "silk_stress"})
_HEATER_INPUTS = frozenset(
    {
        "water_volume_l",
        "initial_temperature_c",
        "target_temperature_c",
        "heating_time_s",
        "loss_fraction",
        "m_kg",
        "dT",
        "t_s",
    }
)
_HEATER_OUTPUTS = frozenset({"power", "heater_power", "required_heater_power"})
_SHAFT_INPUTS = frozenset(
    {
        "power_w",
        "power_kw",
        "rpm",
        "omega",
        "allowable_tau",
        "allowable_stress",
        "safety_factor",
        "torque",
    }
)
_SHAFT_OUTPUTS = frozenset(
    {"diameter", "diameter_m", "diameter_mm", "shaft_diameter", "d_min", "min_diameter"}
)
_ROD_INPUTS = frozenset({"force", "force_n", "diameter", "load", "area"})
_ROD_OUTPUTS = frozenset({"stress", "stress_pa", "stress_mpa", "stress_ratio", "sigma"})


class ProblemEngineeringFrame(BaseModel):
    """Явный семантический контракт задачи для MethodCompatibilityGate."""

    model_config = ConfigDict(extra="forbid")

    domain: str = DOMAIN_GENERAL
    objective: str = ""
    original_problem: str = ""
    required_outputs: list[str] = Field(default_factory=list)
    declared_domain: str | None = None


class MethodCompatibilityResult(BaseModel):
    """Исход детерминированной проверки method/domain."""

    model_config = ConfigDict(extra="forbid")

    compatible: bool
    problem_domain: str
    method_family: str
    codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def infer_problem_domain(
    *,
    declared_domain: str | None = None,
    objective: str = "",
    original_problem: str = "",
) -> str:
    """Вывести канонический домен задачи из contract/scope/текста (без LLM)."""
    raw_domain = (declared_domain or "").strip().lower()
    blob = f"{raw_domain}\n{objective}\n{original_problem}"

    # Текстовые маркеры приоритетнее свободной строки domain (shaft часто domain=null).
    if _FIBER_CUES.search(blob):
        return DOMAIN_BIOMATERIALS_FIBER
    if _HEATER_CUES.search(blob):
        return DOMAIN_THERMAL_HEATING
    if _SHAFT_CUES.search(blob):
        return DOMAIN_MECHANICAL_SHAFT
    if _ROD_CUES.search(blob):
        return DOMAIN_MECHANICAL_ROD

    if raw_domain in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[raw_domain]
    if raw_domain in {"mechanics", "mechanical_engineering"}:
        # Механика без shaft/silk cues: стержень/напряжения, не fiber.
        return DOMAIN_MECHANICAL_ROD
    if raw_domain == "materials":
        return DOMAIN_GENERAL
    return DOMAIN_GENERAL


def classify_calculation_method(spec: CalculationSpec) -> str:
    """Классифицировать метод CalculationSpec по objective / domain / I/O."""
    obj = (spec.objective or "").strip()
    domain = (spec.domain or "").strip()
    inputs = {str(x).strip().lower() for x in (spec.required_inputs or [])}
    outputs = {str(x).strip().lower() for x in (spec.required_outputs or [])}
    blob = f"{obj}\n{domain}\n{' '.join(sorted(inputs))}\n{' '.join(sorted(outputs))}"

    # Fiber: явный objective или характерные I/O (force_n + diameter_m → stress_gpa).
    if _FIBER_METHOD_RE.search(blob) or (
        inputs & _FIBER_INPUTS and outputs & _FIBER_OUTPUTS
    ):
        return METHOD_FIBER_STRESS
    if "stress_gpa" in outputs and ("fiber" in blob or "diameter_m" in inputs):
        return METHOD_FIBER_STRESS
    if _HEATER_METHOD_RE.search(blob) or (
        outputs & _HEATER_OUTPUTS and inputs & _HEATER_INPUTS
    ):
        return METHOD_HEATER_POWER
    if domain.lower() in {"thermal_heating", "thermal"} and outputs & _HEATER_OUTPUTS:
        return METHOD_HEATER_POWER
    if _SHAFT_METHOD_RE.search(blob) or (
        outputs & _SHAFT_OUTPUTS and inputs & _SHAFT_INPUTS
    ):
        return METHOD_SHAFT_MECHANICS
    if _ROD_METHOD_RE.search(obj) or (
        outputs & _ROD_OUTPUTS and inputs & _ROD_INPUTS and "fiber" not in blob.lower()
    ):
        return METHOD_ROD_STRESS
    return METHOD_UNKNOWN


def problem_frame_from_sources(
    *,
    engineering_contract: Any | None = None,
    investigation_scope: Any | None = None,
    original_problem: str | None = None,
    objective: str | None = None,
    declared_domain: str | None = None,
    required_outputs: list[str] | None = None,
) -> ProblemEngineeringFrame:
    """Собрать frame из EngineeringContract / InvestigationScope / явных полей."""
    domain_s = declared_domain
    obj = objective or ""
    original = original_problem or ""
    outs = list(required_outputs or [])

    if engineering_contract is not None:
        scope = getattr(engineering_contract, "scope", None)
        if domain_s is None and scope is not None:
            domain_s = getattr(scope, "domain", None)
        if not obj:
            co = getattr(engineering_contract, "objective", None)
            obj = str(getattr(co, "statement", None) or co or "")
        if not original:
            original = str(getattr(engineering_contract, "original_problem", None) or "")
        if not outs:
            raw_outs = getattr(engineering_contract, "required_outputs", None) or []
            for item in raw_outs:
                name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
                if name:
                    outs.append(str(name))

    if investigation_scope is not None:
        if domain_s is None:
            domain_s = getattr(investigation_scope, "domain", None)
        if not obj:
            obj = str(getattr(investigation_scope, "objective", None) or "")
        if not original:
            original = str(getattr(investigation_scope, "original_problem", None) or "")
        if not outs:
            outs = list(getattr(investigation_scope, "required_outputs", None) or [])

    canonical = infer_problem_domain(
        declared_domain=domain_s,
        objective=obj,
        original_problem=original,
    )
    return ProblemEngineeringFrame(
        domain=canonical,
        objective=obj,
        original_problem=original,
        required_outputs=outs,
        declared_domain=domain_s,
    )


def problem_frame_from_agent_context(ctx: Any) -> ProblemEngineeringFrame:
    """Достать frame из AgentContext.extra / ProblemContext (simulation path)."""
    extra = getattr(ctx, "extra", None) or {}
    if not isinstance(extra, dict):
        extra = {}
    contract = extra.get("engineering_contract")
    scope = extra.get("investigation_scope")
    problem = getattr(ctx, "problem", None)
    original = None
    objective = None
    if problem is not None:
        original = getattr(problem, "original_problem", None) or getattr(
            problem, "problem_text", None
        )
        objective = getattr(problem, "resolved_objective", None)
    return problem_frame_from_sources(
        engineering_contract=contract,
        investigation_scope=scope,
        original_problem=original,
        objective=objective,
    )


def check_method_compatibility(
    spec: CalculationSpec,
    frame: ProblemEngineeringFrame,
) -> MethodCompatibilityResult:
    """Шесть минимальных проверок: domain / objective / inputs / outputs / allowed / forbidden."""
    codes: list[str] = []
    reasons: list[str] = []
    method = classify_calculation_method(spec)
    domain = frame.domain

    allowed = _ALLOWED_METHODS.get(domain)
    # 1 + 5 + 6: domain / allowed method / forbidden mismatch
    if allowed is not None:
        if method == METHOD_UNKNOWN:
            codes.append(CODE_METHOD_MISMATCH)
            reasons.append(
                f"{CODE_METHOD_MISMATCH}: domain={domain!r} rejects unclassified "
                f"method objective={spec.objective!r} (allowed={sorted(allowed)})"
            )
        elif method not in allowed:
            code = (
                CODE_DOMAIN_MISMATCH
                if method
                in {METHOD_FIBER_STRESS, METHOD_HEATER_POWER, METHOD_SHAFT_MECHANICS}
                and domain
                in {
                    DOMAIN_MECHANICAL_SHAFT,
                    DOMAIN_MECHANICAL_ROD,
                    DOMAIN_THERMAL_HEATING,
                    DOMAIN_BIOMATERIALS_FIBER,
                }
                else CODE_METHOD_MISMATCH
            )
            # Fiber на shaft/rod — явный METHOD_MISMATCH по ТЗ.
            if method == METHOD_FIBER_STRESS and domain in {
                DOMAIN_MECHANICAL_SHAFT,
                DOMAIN_MECHANICAL_ROD,
            }:
                code = CODE_METHOD_MISMATCH
            if method == METHOD_FIBER_STRESS and domain == DOMAIN_THERMAL_HEATING:
                code = CODE_DOMAIN_MISMATCH
            if method in {METHOD_SHAFT_MECHANICS, METHOD_ROD_STRESS, METHOD_FIBER_STRESS} and domain == DOMAIN_THERMAL_HEATING:
                code = CODE_DOMAIN_MISMATCH
            codes.append(code)
            reasons.append(
                f"{code}: calculation method={method!r} incompatible with "
                f"problem domain={domain!r} (objective={spec.objective!r})"
            )

    # 2: objective compatibility — fiber/heater слова в objective при чужом домене
    obj_l = (spec.objective or "").lower()
    if domain in {DOMAIN_MECHANICAL_SHAFT, DOMAIN_MECHANICAL_ROD} and _FIBER_METHOD_RE.search(
        obj_l
    ):
        if CODE_OBJECTIVE_MISMATCH not in codes:
            codes.append(CODE_OBJECTIVE_MISMATCH)
        reasons.append(
            f"{CODE_OBJECTIVE_MISMATCH}: objective {spec.objective!r} is fiber/silk "
            f"while problem domain is {domain!r}"
        )
    if domain == DOMAIN_THERMAL_HEATING and (
        _FIBER_METHOD_RE.search(obj_l) or _SHAFT_METHOD_RE.search(obj_l) or "stress" in obj_l
    ):
        if "heater" not in obj_l and "power" not in obj_l:
            if CODE_OBJECTIVE_MISMATCH not in codes:
                codes.append(CODE_OBJECTIVE_MISMATCH)
            reasons.append(
                f"{CODE_OBJECTIVE_MISMATCH}: objective {spec.objective!r} is not a "
                f"heater-power method for domain={domain!r}"
            )

    inputs = {str(x).strip().lower() for x in (spec.required_inputs or [])}
    outputs = {str(x).strip().lower() for x in (spec.required_outputs or [])}

    # 3: required inputs compatibility
    if domain == DOMAIN_MECHANICAL_SHAFT and inputs & _FIBER_INPUTS and not inputs & _SHAFT_INPUTS:
        codes.append(CODE_INPUT_MISMATCH)
        reasons.append(
            f"{CODE_INPUT_MISMATCH}: shaft problem got fiber-like inputs {sorted(inputs & _FIBER_INPUTS)}"
        )
    if domain == DOMAIN_THERMAL_HEATING and inputs & _FIBER_INPUTS and not inputs & _HEATER_INPUTS:
        codes.append(CODE_INPUT_MISMATCH)
        reasons.append(
            f"{CODE_INPUT_MISMATCH}: heater problem got fiber-like inputs {sorted(inputs & _FIBER_INPUTS)}"
        )
    if domain in {DOMAIN_MECHANICAL_SHAFT, DOMAIN_MECHANICAL_ROD} and inputs & _HEATER_INPUTS and not (
        inputs & (_SHAFT_INPUTS | _ROD_INPUTS)
    ):
        codes.append(CODE_INPUT_MISMATCH)
        reasons.append(
            f"{CODE_INPUT_MISMATCH}: mechanical problem got heater inputs {sorted(inputs & _HEATER_INPUTS)}"
        )

    # 4: output compatibility
    frame_outs = {str(x).strip().lower() for x in frame.required_outputs}
    if domain == DOMAIN_MECHANICAL_SHAFT and outputs & _FIBER_OUTPUTS:
        codes.append(CODE_OUTPUT_MISMATCH)
        reasons.append(
            f"{CODE_OUTPUT_MISMATCH}: shaft problem produced fiber outputs {sorted(outputs & _FIBER_OUTPUTS)}"
        )
    if domain == DOMAIN_THERMAL_HEATING and outputs & (_FIBER_OUTPUTS | {"stress", "stress_pa", "stress_mpa"}):
        if not outputs & _HEATER_OUTPUTS:
            codes.append(CODE_OUTPUT_MISMATCH)
            reasons.append(
                f"{CODE_OUTPUT_MISMATCH}: heater problem produced stress outputs "
                f"without power: {sorted(outputs)}"
            )
    if frame_outs and outputs and not (frame_outs & outputs):
        # Locked contract outputs vs spec — мягкий сигнал только вместе с method fail,
        # но для thermal power vs stress_gpa — жёсткий.
        if domain == DOMAIN_THERMAL_HEATING and "power" in frame_outs and "power" not in outputs:
            codes.append(CODE_OUTPUT_MISMATCH)
            reasons.append(
                f"{CODE_OUTPUT_MISMATCH}: contract requires {sorted(frame_outs)}, "
                f"spec outputs {sorted(outputs)}"
            )

    # GENERAL: всё равно запрещаем fiber, если в постановке нет silk/fiber cues.
    if domain == DOMAIN_GENERAL and method == METHOD_FIBER_STRESS:
        blob = f"{frame.objective}\n{frame.original_problem}"
        if not _FIBER_CUES.search(blob):
            codes.append(CODE_DOMAIN_MISMATCH)
            reasons.append(
                f"{CODE_DOMAIN_MISMATCH}: fiber/silk calculation without biomaterials "
                f"cues in problem (objective={spec.objective!r})"
            )

    compatible = not codes
    if not compatible:
        logger.error(
            "MethodCompatibilityGate FAIL domain=%s method=%s codes=%s reasons=%s",
            domain,
            method,
            codes,
            reasons,
        )
    return MethodCompatibilityResult(
        compatible=compatible,
        problem_domain=domain,
        method_family=method,
        codes=codes,
        reasons=reasons,
    )


def evaluate_specs_method_compatibility(
    specs: list[CalculationSpec],
    frame: ProblemEngineeringFrame,
) -> MethodCompatibilityResult:
    """Агрегат по всем CalculationSpec: любой FAIL → incompatible."""
    if not specs:
        return MethodCompatibilityResult(
            compatible=True,
            problem_domain=frame.domain,
            method_family=METHOD_UNKNOWN,
            codes=[],
            reasons=[],
        )
    all_codes: list[str] = []
    all_reasons: list[str] = []
    methods: list[str] = []
    for spec in specs:
        result = check_method_compatibility(spec, frame)
        methods.append(result.method_family)
        if not result.compatible:
            all_codes.extend(result.codes)
            all_reasons.extend(result.reasons)
    return MethodCompatibilityResult(
        compatible=not all_codes,
        problem_domain=frame.domain,
        method_family=methods[0] if len(methods) == 1 else ",".join(methods),
        codes=list(dict.fromkeys(all_codes)),
        reasons=list(dict.fromkeys(all_reasons)),
    )
