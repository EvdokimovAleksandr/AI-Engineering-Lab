"""IterationPolicy + IterationController — re-entry stage and no-progress stops.

IterationPolicy (`next_iteration_state`) выбирает стадию re-entry.
IterationController решает CONTINUE | REPLAN | ASK_USER | STOP_* по прогрессу
coverage / missing outputs / failure_class — чтобы не жечь бюджет на одинаковых дырах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.core.enums import (
    AdjudicationStatus,
    FailureClass,
    IterationAction,
    LabErrorCode,
    ProjectState,
    ResearchOutcome,
)
from ai_lab.core.models import (
    AdjudicationResult,
    DeterministicCheckReport,
    EvidenceCompletenessReport,
    VerificationReport,
)

# ---------------------------------------------------------------------------
# IterationPolicy — куда вернуться (стадия)
# ---------------------------------------------------------------------------


def next_iteration_state(
    *,
    adjudication: AdjudicationResult | None = None,
    verification: VerificationReport | None = None,
    check_report: DeterministicCheckReport | None = None,
    failure_class: FailureClass | None = None,
    replan: bool = False,
) -> ProjectState:
    """
    Choose where to re-enter after a failed/disputed review.

    Not a blind jump to ANALYSIS for every failure.
    ``replan=True`` — более широкий re-entry (DECOMPOSITION), один раз после stagnation.
    """
    if replan:
        return ProjectState.DECOMPOSITION

    if failure_class == FailureClass.UNIT:
        return ProjectState.CALCULATION
    if failure_class == FailureClass.CALCULATION:
        return ProjectState.CALCULATION
    if failure_class == FailureClass.SIMULATION:
        return ProjectState.SIMULATION
    if failure_class == FailureClass.RESEARCH:
        return ProjectState.RESEARCH
    if failure_class == FailureClass.SCOPE:
        return ProjectState.UNDERSTANDING
    if failure_class == FailureClass.PLANNING:
        return ProjectState.DECOMPOSITION
    if failure_class == FailureClass.CONTEXT:
        return ProjectState.UNDERSTANDING
    if failure_class == FailureClass.VERIFICATION:
        return ProjectState.CALCULATION
    if failure_class == FailureClass.USER_INPUT:
        return ProjectState.UNDERSTANDING

    texts: list[str] = []
    if adjudication:
        texts.extend(adjudication.reasons)
    if verification:
        texts.extend(verification.discrepancies)
        texts.append(verification.notes)
    if check_report:
        texts.extend(check_report.critical_failures)
        for r in check_report.results:
            if r.discrepancy:
                texts.append(r.discrepancy)

    blob = " ".join(texts).lower()

    if any(k in blob for k in ("unit", "recompute", "calculation", "mathcheck", "delta=")):
        return ProjectState.CALCULATION
    if any(k in blob for k in ("simulation", "sandbox", "returncode")):
        return ProjectState.SIMULATION
    if any(k in blob for k in ("source", "research", "stub", "literature", "insufficient")):
        return ProjectState.RESEARCH
    if any(k in blob for k in ("assumption", "hypothesis", "model", "physics")):
        return ProjectState.ANALYSIS
    return ProjectState.ANALYSIS


# ---------------------------------------------------------------------------
# FAILURE_CLASS mapping
# ---------------------------------------------------------------------------


def classify_failure(
    *,
    adjudication: AdjudicationResult | None = None,
    evidence: EvidenceCompletenessReport | None = None,
    research_outcome: ResearchOutcome | str | None = None,
    error_code: LabErrorCode | str | None = None,
    budget_exceeded: bool = False,
    reason_texts: Sequence[str] | None = None,
) -> FailureClass:
    """Map known codes / outcomes / reason text → FailureClass (fail loud if budget)."""
    if budget_exceeded:
        return FailureClass.BUDGET

    code = error_code.value if isinstance(error_code, LabErrorCode) else (error_code or "")
    code_u = str(code).upper()
    if code_u == LabErrorCode.CONTEXT_MISMATCH.value:
        return FailureClass.CONTEXT
    if code_u == LabErrorCode.MISSING_EXECUTION_CONTEXT.value:
        return FailureClass.CONTEXT
    if code_u in {
        LabErrorCode.CONTRACT_NOT_READY.value,
        LabErrorCode.CONTRACT_LOCKED.value,
        LabErrorCode.ILLEGAL_CONTRACT_TRANSITION.value,
    }:
        return FailureClass.SCOPE

    outcome = (
        research_outcome.value
        if isinstance(research_outcome, ResearchOutcome)
        else (research_outcome or "")
    )
    outcome_u = str(outcome).upper()
    if outcome_u == ResearchOutcome.RESEARCH_PROVIDER_ERROR.value:
        return FailureClass.PROVIDER
    if outcome_u in {
        ResearchOutcome.RESEARCH_EMPTY.value,
        ResearchOutcome.RESEARCH_FILTERED.value,
        ResearchOutcome.RESEARCH_PARTIAL.value,
    }:
        return FailureClass.RESEARCH

    texts: list[str] = list(reason_texts or [])
    if adjudication:
        texts.extend(adjudication.reasons)
        if adjudication.scope_status:
            texts.append(str(adjudication.scope_status))
        rs = adjudication.research_sufficiency or {}
        if isinstance(rs, dict) and rs.get("outcome"):
            texts.append(str(rs["outcome"]))
    if evidence:
        texts.extend(evidence.reasons)

    blob = " ".join(texts).lower()

    if any(k in blob for k in ("scope_", "contract_not_ready", "needs_clarification")):
        return FailureClass.SCOPE
    if any(k in blob for k in ("context_mismatch", "execution context", "wrong investigation")):
        return FailureClass.CONTEXT
    if any(k in blob for k in ("research_empty", "research_filtered", "research_partial", "literature")):
        return FailureClass.RESEARCH
    if "research_provider" in blob or "provider" in blob and "research" in blob:
        return FailureClass.PROVIDER
    if any(k in blob for k in ("dimension", "unit", "incompatible_dimensions", "pint")):
        return FailureClass.UNIT
    if any(k in blob for k in ("sandbox", "simulation", "returncode", "docker")):
        return FailureClass.SIMULATION
    if any(k in blob for k in ("calculation", "mathcheck", "recompute", "formula", "solver")):
        return FailureClass.CALCULATION
    if any(k in blob for k in ("verification", "check failed", "deterministic")):
        return FailureClass.VERIFICATION
    if any(k in blob for k in ("budget", "max_tokens", "max_cost", "max_iterations")):
        return FailureClass.BUDGET
    if any(k in blob for k in ("user", "clarification", "hitl", "ask_user")):
        return FailureClass.USER_INPUT
    if any(k in blob for k in ("plan", "taskgraph", "unknown_role", "planner")):
        return FailureClass.PLANNING
    if any(k in blob for k in ("infra", "filesystem", "permission", "timeout")):
        return FailureClass.INFRASTRUCTURE
    if evidence is not None and not evidence.is_complete:
        # Дыры в evidence без узкой классификации — verification/coverage gap.
        return FailureClass.VERIFICATION
    return FailureClass.VERIFICATION


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class IterationDecision(BaseModel):
    """Результат IterationController для LabRuntime."""

    model_config = ConfigDict(extra="forbid")

    action: IterationAction
    reason: str
    failure_class: FailureClass | None = None
    # Дешёвые метрики — только если уже посчитаны; иначе None (не оцениваем токены здесь).
    expected_value: float | None = None
    estimated_cost: float | None = None
    progress: "IterationProgressReport | None" = None


class IterationProgressReport(BaseModel):
    """Структурированный прогресс одной итерации (лёгкая персистенция на run)."""

    model_config = ConfigDict(extra="forbid")

    iteration: int = 0
    known: list[str] = Field(default_factory=list)
    learned: list[str] = Field(default_factory=list)
    gaps_closed: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    coverage_before: float | None = None
    coverage_after: float | None = None
    # Человекочитаемо: "4/8 → 6/8" или "0.50 → 0.75"
    coverage_display: str = ""
    no_progress: bool = False
    failure_class: FailureClass | None = None
    missing_outputs: list[str] = Field(default_factory=list)
    covered_outputs: list[str] = Field(default_factory=list)


@dataclass
class IterationObservation:
    """Снимок после adjudication для сравнения итераций."""

    coverage_ratio: float | None = None
    missing_outputs: tuple[str, ...] = ()
    covered_outputs: tuple[str, ...] = ()
    failure_class: FailureClass = FailureClass.VERIFICATION
    adjudication_status: AdjudicationStatus | None = None
    known: list[str] = field(default_factory=list)
    learned: list[str] = field(default_factory=list)
    budget_exceeded: bool = False
    # Числитель/знаменатель для display, если известны.
    covered_count: int | None = None
    required_count: int | None = None


def observation_from_adjudication(
    *,
    adjudication: AdjudicationResult | None,
    evidence: EvidenceCompletenessReport | None = None,
    research_outcome: ResearchOutcome | str | None = None,
    error_code: LabErrorCode | str | None = None,
    budget_exceeded: bool = False,
    known: Sequence[str] | None = None,
    learned: Sequence[str] | None = None,
) -> IterationObservation:
    """Собрать Observation из артефактов adjudication / evidence (без LLM)."""
    missing: list[str] = []
    covered: list[str] = []
    ratio: float | None = None
    covered_n: int | None = None
    required_n: int | None = None

    if evidence is not None:
        ratio = evidence.coverage_ratio
        missing.extend(evidence.missing_outputs)
        covered.extend(evidence.covered_outputs)
        for rr in evidence.relevance_results:
            missing.extend(rr.missing_outputs)

    if adjudication and isinstance(adjudication.evidence_completeness, dict):
        ec = adjudication.evidence_completeness
        if not missing:
            missing.extend(str(x) for x in (ec.get("missing_outputs") or []))
        if not covered:
            covered.extend(str(x) for x in (ec.get("covered_outputs") or []))
        if ratio is None and ec.get("coverage_ratio") is not None:
            ratio = float(ec["coverage_ratio"])

    missing_t = tuple(sorted({m for m in missing if m}))
    covered_t = tuple(sorted({c for c in covered if c}))
    if covered_t or missing_t:
        covered_n = len(covered_t)
        required_n = len(covered_t) + len(missing_t)
        if ratio is None and required_n > 0:
            ratio = covered_n / required_n

    fc = classify_failure(
        adjudication=adjudication,
        evidence=evidence,
        research_outcome=research_outcome,
        error_code=error_code,
        budget_exceeded=budget_exceeded,
    )
    status = adjudication.status if adjudication else None
    return IterationObservation(
        coverage_ratio=ratio,
        missing_outputs=missing_t,
        covered_outputs=covered_t,
        failure_class=fc,
        adjudication_status=status,
        known=list(known or covered_t),
        learned=list(learned or []),
        budget_exceeded=budget_exceeded,
        covered_count=covered_n,
        required_count=required_n,
    )


def _coverage_display(
    before: float | None,
    after: float | None,
    *,
    before_counts: tuple[int, int] | None = None,
    after_counts: tuple[int, int] | None = None,
) -> str:
    def _fmt(ratio: float | None, counts: tuple[int, int] | None) -> str:
        if counts is not None and counts[1] > 0:
            return f"{counts[0]}/{counts[1]}"
        if ratio is None:
            return "?"
        return f"{ratio:.2f}"

    return f"{_fmt(before, before_counts)} → {_fmt(after, after_counts)}"


def _has_coverage_improvement(before: float | None, after: float | None) -> bool:
    """Строгое улучшение coverage; None→None / равные — не прогресс."""
    if after is None:
        return False
    if before is None:
        return after > 0.0
    return after > before + 1e-12


def _same_stagnation(prev: IterationObservation, cur: IterationObservation) -> bool:
    """Одинаковые дыры / тот же failure_class / нет роста coverage."""
    same_missing = prev.missing_outputs == cur.missing_outputs
    same_class = prev.failure_class == cur.failure_class
    no_cov = not _has_coverage_improvement(prev.coverage_ratio, cur.coverage_ratio)
    return same_missing and same_class and no_cov


# ---------------------------------------------------------------------------
# IterationController
# ---------------------------------------------------------------------------


class IterationController:
    """Детектор отсутствия прогресса + решение CONTINUE/REPLAN/ASK_USER/STOP_*.

    Правила (документированы для PR-06):
    1. ``STOP_BUDGET`` — только если budget_exceeded=True (реальный удар по бюджету).
    2. PASS adjudication → ``CONTINUE`` (runtime не делает re-entry).
    3. Прогресс (coverage вырос ИЛИ missing outputs сузились) → сброс streak,
       ``CONTINUE`` (разрешить ещё одну попытку / re-entry).
    4. N подряд no-progress (default 3: те же missing + тот же failure_class +
       нет роста coverage) → один ``REPLAN``, streak сбрасывается.
    5. После REPLAN снова no-progress → ``ASK_USER`` если hitl_on_no_progress,
       иначе ``STOP_INSUFFICIENT_EVIDENCE`` (честный engineering outcome).
    6. Не продолжаем идентичную попытку до исчерпания max_tokens.
    """

    def __init__(
        self,
        *,
        no_progress_limit: int = 3,
        hitl_on_no_progress: bool = False,
    ) -> None:
        if no_progress_limit < 1:
            raise ValueError(f"no_progress_limit must be >= 1, got {no_progress_limit}")
        self.no_progress_limit = no_progress_limit
        self.hitl_on_no_progress = hitl_on_no_progress
        self.history: list[IterationProgressReport] = []
        self._last_observation: IterationObservation | None = None
        self._consecutive_no_progress = 0
        self._replan_issued_for_stagnation = False

    @classmethod
    def from_runtime_config(cls, runtime: dict[str, Any] | None) -> IterationController:
        """Читает runtime.iteration.* — без изменения max_tokens/max_cost."""
        runtime = runtime or {}
        raw = runtime.get("iteration") or {}
        if not isinstance(raw, dict):
            raise TypeError("runtime.iteration must be a mapping when present")
        limit = int(raw.get("no_progress_limit", 3))
        hitl = bool(raw.get("hitl_on_no_progress", False))
        return cls(no_progress_limit=limit, hitl_on_no_progress=hitl)

    def record_and_decide(self, obs: IterationObservation) -> IterationDecision:
        """Записать прогресс итерации и вернуть действие."""
        prev = self._last_observation
        iteration_idx = len(self.history) + 1

        gaps_closed: list[str] = []
        learned = list(obs.learned)
        if prev is not None:
            gaps_closed = sorted(set(prev.missing_outputs) - set(obs.missing_outputs))
            if gaps_closed and not learned:
                learned = [f"closed:{g}" for g in gaps_closed]

        before_counts = None
        after_counts = None
        if prev and prev.covered_count is not None and prev.required_count is not None:
            before_counts = (prev.covered_count, prev.required_count)
        if obs.covered_count is not None and obs.required_count is not None:
            after_counts = (obs.covered_count, obs.required_count)

        coverage_before = prev.coverage_ratio if prev else None
        no_progress = False
        if prev is None:
            # Первая точка — ещё не stagnation streak; сравнивать не с чем.
            no_progress = False
        else:
            narrowed = set(obs.missing_outputs) < set(prev.missing_outputs)
            improved = _has_coverage_improvement(prev.coverage_ratio, obs.coverage_ratio)
            no_progress = not (improved or narrowed) and _same_stagnation(prev, obs)
            # Если missing изменились не в сторону сужения, но class/cov те же —
            # тоже no-progress (перестановка без выигрыша).
            if not improved and not narrowed and prev.failure_class == obs.failure_class:
                if prev.missing_outputs == obs.missing_outputs:
                    no_progress = True

        report = IterationProgressReport(
            iteration=iteration_idx,
            known=list(obs.known),
            learned=learned,
            gaps_closed=gaps_closed,
            remaining=list(obs.missing_outputs),
            coverage_before=coverage_before,
            coverage_after=obs.coverage_ratio,
            coverage_display=_coverage_display(
                coverage_before,
                obs.coverage_ratio,
                before_counts=before_counts,
                after_counts=after_counts,
            ),
            no_progress=no_progress if prev is not None else False,
            failure_class=obs.failure_class,
            missing_outputs=list(obs.missing_outputs),
            covered_outputs=list(obs.covered_outputs),
        )
        self.history.append(report)
        self._last_observation = obs

        if obs.budget_exceeded:
            return IterationDecision(
                action=IterationAction.STOP_BUDGET,
                reason="Run budget exceeded",
                failure_class=FailureClass.BUDGET,
                progress=report,
            )

        if obs.adjudication_status == AdjudicationStatus.PASS:
            self._consecutive_no_progress = 0
            self._replan_issued_for_stagnation = False
            return IterationDecision(
                action=IterationAction.CONTINUE,
                reason="Adjudication PASS",
                failure_class=obs.failure_class,
                progress=report,
            )

        # Реальный прогресс — сброс stagnation episode (включая флаг REPLAN).
        if prev is not None and not no_progress:
            self._consecutive_no_progress = 0
            self._replan_issued_for_stagnation = False
            return IterationDecision(
                action=IterationAction.CONTINUE,
                reason=(
                    f"Evidence progress: coverage {report.coverage_display}; "
                    f"gaps_closed={gaps_closed or '-'}"
                ),
                failure_class=obs.failure_class,
                progress=report,
            )

        # Нет прогресса (или первая точка — начало streak).
        self._consecutive_no_progress += 1

        # После REPLAN достаточно ещё одного no-progress → STOP / ASK_USER.
        if self._replan_issued_for_stagnation:
            if self.hitl_on_no_progress:
                return IterationDecision(
                    action=IterationAction.ASK_USER,
                    reason=(
                        "No progress after REPLAN; ask user "
                        f"(failure_class={obs.failure_class.value})"
                    ),
                    failure_class=obs.failure_class,
                    progress=report,
                )
            return IterationDecision(
                action=IterationAction.STOP_INSUFFICIENT_EVIDENCE,
                reason=(
                    "No progress after REPLAN; stopping with INSUFFICIENT_EVIDENCE "
                    f"(failure_class={obs.failure_class.value}, "
                    f"missing={list(obs.missing_outputs)})"
                ),
                failure_class=obs.failure_class,
                progress=report,
            )

        if self._consecutive_no_progress >= self.no_progress_limit:
            self._replan_issued_for_stagnation = True
            self._consecutive_no_progress = 0
            return IterationDecision(
                action=IterationAction.REPLAN,
                reason=(
                    f"No evidence progress for {self.no_progress_limit} iterations "
                    f"(missing={list(obs.missing_outputs)}, "
                    f"failure_class={obs.failure_class.value}); REPLAN once"
                ),
                failure_class=obs.failure_class,
                progress=report,
            )

        return IterationDecision(
            action=IterationAction.CONTINUE,
            reason=(
                f"No progress streak {self._consecutive_no_progress}/"
                f"{self.no_progress_limit}; allow another attempt"
            ),
            failure_class=obs.failure_class,
            progress=report,
        )

    def dump_history(self) -> list[dict[str, Any]]:
        """Для лёгкой персистенции в run store."""
        return [h.model_dump(mode="json") for h in self.history]
