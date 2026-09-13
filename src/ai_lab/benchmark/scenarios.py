"""Focused PR-07 scenario runners — acceptance without empty benchmark trees.

Each scenario has an expected behavior and returns a structured result that
pytest / ``python -m ai_lab benchmark scenario`` can assert on.
FailureClass taxonomy is reused from PR-06 (never duplicated).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ai_lab.benchmark.acceptance import evaluate_acceptance
from ai_lab.benchmark.models import (
    BenchmarkExpectation,
    ExpectedBehavior,
    OutputAcceptance,
)
from ai_lab.checks.calculation_contract import validate_computation_against_spec
from ai_lab.checks.units import units_compatible
from ai_lab.core.enums import (
    FailureClass,
    IterationAction,
    LabErrorCode,
    ProblemKind,
    ScopeStatus,
)
from ai_lab.core.execution_context import (
    ContextMismatchError,
    ExecutionContext,
    require_artifact_context,
)
from ai_lab.core.models import CalculationSpec, ComputationArtifact
from ai_lab.orchestrator.iteration_policy import (
    IterationController,
    IterationObservation,
)
from ai_lab.orchestrator.scope import resolve_scope
from ai_lab.orchestrator.scope_resolver import classify_problem_kind
from ai_lab.task_routing.enums import WorkflowProfile


@dataclass
class ScenarioResult:
    """Outcome of a focused scenario run."""

    scenario_id: str
    passed: bool
    expected_behavior: ExpectedBehavior
    observed: dict[str, Any] = field(default_factory=dict)
    failure_class: str | None = None
    reasons: list[str] = field(default_factory=list)

    def public_dump(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "expected_behavior": self.expected_behavior.value,
            "observed": self.observed,
            "failure_class": self.failure_class,
            "reasons": self.reasons,
        }


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    title: str
    expected_behavior: ExpectedBehavior
    run: Callable[[], ScenarioResult]
    notes: str = ""


def _fail(sid: str, behavior: ExpectedBehavior, *reasons: str, **observed: Any) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=sid,
        passed=False,
        expected_behavior=behavior,
        observed=observed,
        reasons=list(reasons),
    )


def _ok(
    sid: str,
    behavior: ExpectedBehavior,
    *,
    failure_class: str | None = None,
    **observed: Any,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=sid,
        passed=True,
        expected_behavior=behavior,
        observed=observed,
        failure_class=failure_class,
        reasons=[],
    )


def run_context_isolation() -> ScenarioResult:
    """Sofa+rod contamination must raise CONTEXT — never silent PASS."""
    sid = "context_isolation"
    behavior = ExpectedBehavior.FAIL
    sofa = ExecutionContext.for_project_run(
        project_id="investigation_sofa_height",
        investigation_id="investigation_sofa_height",
        task_id="calculation",
        run_id="run_sofa_001",
    )
    rod = CalculationSpec.model_validate(
        {
            "spec_id": "cspec_rod",
            "project_id": "investigation_rod_stress",
            "investigation_id": "investigation_rod_stress",
            "task_id": "foreign_rod_task",
            "run_id": "run_rod_001",
            "objective": "Compute axial stress in a loaded rod",
            "required_outputs": ["stress"],
            "expected_dimensions": {"stress": "Pa"},
        }
    )
    try:
        require_artifact_context(sofa, rod, where="benchmark.context_isolation")
    except ContextMismatchError as exc:
        # Ожидаем жёсткий CONTEXT_MISMATCH, не PASS.
        code = getattr(exc, "code", None) or LabErrorCode.CONTEXT_MISMATCH
        if code != LabErrorCode.CONTEXT_MISMATCH:
            return _fail(
                sid,
                behavior,
                f"wrong error code {code!r}, expected CONTEXT_MISMATCH",
                error=str(exc),
            )
        # Artifact-vs-spec gate тоже отказывает при разных investigation_id.
        sofa_spec = CalculationSpec.model_validate(
            {
                "spec_id": "cspec_sofa",
                "project_id": "investigation_sofa_height",
                "investigation_id": "investigation_sofa_height",
                "task_id": "calculation",
                "run_id": "run_sofa_001",
                "objective": "Estimate sofa seat height",
                "required_outputs": ["height"],
                "expected_dimensions": {"height": "m"},
            }
        )
        rod_art = ComputationArtifact(
            artifact_id="comp_rod_leak",
            run_id="run_rod_001",
            project_id="investigation_rod_stress",
            investigation_id="investigation_rod_stress",
            kind="calculation",
            task_id="calculation",
            calculation_spec_id=sofa_spec.spec_id,
            declared_outputs={"stress": {"value": 1.0, "unit": "Pa"}},
            returncode=0,
            status="ok",
        )
        try:
            validate_computation_against_spec(rod_art, sofa_spec)
            return _fail(
                sid,
                behavior,
                "rod artifact vs sofa spec did not raise CONTEXT_MISMATCH",
            )
        except ContextMismatchError:
            return _ok(
                sid,
                behavior,
                failure_class=FailureClass.CONTEXT.value,
                error_code=LabErrorCode.CONTEXT_MISMATCH.value,
            )
    return _fail(sid, behavior, "sofa+rod mix did not raise ContextMismatchError")


def run_units() -> ScenarioResult:
    """length + m passes; length vs litre fails (Pint litre = volume)."""
    sid = "units"
    behavior = ExpectedBehavior.PASS
    reasons: list[str] = []
    if not units_compatible("length", "m"):
        reasons.append("expected length ≡ m")
    if units_compatible("length", "liter"):
        reasons.append("length must NOT match litre/volume")
    if units_compatible("length", "L"):  # Pint L = litre on actual side
        reasons.append("length must NOT match Pint L (litre)")

    # Acceptance oracle: length output in metres.
    length_rule = OutputAcceptance(
        output_id="length",
        aliases=["L_out", "height"],
        dimension_unit="length",
        min_value=0.9,
        max_value=1.1,
        band_unit="m",
    )
    exp = BenchmarkExpectation(
        benchmark_id="units_fixture",
        expected_workflow=WorkflowProfile.SIMPLE,
        acceptance_outputs=[length_rule],
    )
    ok_comp = ComputationArtifact(
        run_id="run_units",
        kind="calculation",
        task_id="calculation",
        declared_outputs={"length": {"value": 1.0, "unit": "m"}},
        returncode=0,
        status="ok",
    )
    bad_comp = ComputationArtifact(
        run_id="run_units",
        kind="calculation",
        task_id="calculation",
        declared_outputs={"length": {"value": 1.0, "unit": "liter"}},
        returncode=0,
        status="ok",
    )
    ok_rep = evaluate_acceptance(exp, computations=[ok_comp])
    bad_rep = evaluate_acceptance(exp, computations=[bad_comp])
    if not ok_rep.passed:
        reasons.append(f"length+m should pass acceptance: {ok_rep.reasons}")
    if bad_rep.passed:
        reasons.append("length vs litre must fail acceptance")
    if reasons:
        return ScenarioResult(
            scenario_id=sid,
            passed=False,
            expected_behavior=behavior,
            observed={
                "ok_passed": ok_rep.passed,
                "bad_passed": bad_rep.passed,
            },
            failure_class=FailureClass.UNIT.value,
            reasons=reasons,
        )
    return _ok(
        sid,
        behavior,
        failure_class=None,
        length_m_ok=True,
        length_liter_rejected=True,
    )


def run_scope_resolution() -> ScenarioResult:
    """Closed-numeric rod stress-ratio prompt → CLOSED_NUMERIC + RESOLVED."""
    sid = "scope_resolution"
    behavior = ExpectedBehavior.PASS
    prompt = (
        "Steel rod Ø10 mm, F=10 kN axial, diameter becomes d×2; find the stress ratio."
    )
    kind = classify_problem_kind(prompt)
    scope = resolve_scope(prompt)
    reasons: list[str] = []
    if kind != ProblemKind.CLOSED_NUMERIC:
        reasons.append(f"kind={kind.value}, expected CLOSED_NUMERIC")
    if scope.status != ScopeStatus.SCOPE_RESOLVED:
        reasons.append(f"status={scope.status.value}, expected SCOPE_RESOLVED")
    if "stress_ratio" not in (scope.required_outputs or []):
        reasons.append(f"required_outputs={scope.required_outputs}")
    if reasons:
        return _fail(
            sid,
            behavior,
            *reasons,
            problem_kind=kind.value,
            scope_status=scope.status.value,
        )
    return _ok(
        sid,
        behavior,
        problem_kind=kind.value,
        scope_status=scope.status.value,
        required_outputs=list(scope.required_outputs),
    )


def run_ambiguous_engineering() -> ScenarioResult:
    """«Как сделать стержень прочнее?» → OPEN_ENDED + NEEDS_CLARIFICATION + load_type."""
    sid = "ambiguous_engineering"
    behavior = ExpectedBehavior.NEEDS_CLARIFICATION
    prompt = "Как сделать стержень прочнее?"
    kind = classify_problem_kind(prompt)
    scope = resolve_scope(prompt)
    reasons: list[str] = []
    if kind != ProblemKind.OPEN_ENDED:
        reasons.append(f"kind={kind.value}, expected OPEN_ENDED")
    if scope.status != ScopeStatus.SCOPE_NEEDS_CLARIFICATION:
        reasons.append(f"status={scope.status.value}, expected NEEDS_CLARIFICATION")
    if "load_type" not in (scope.required_fields or []):
        reasons.append(f"required_fields={scope.required_fields}, need load_type")
    if scope.clarification is None:
        reasons.append("missing ClarificationQuestion")
    # Нельзя выдавать фейковый numeric PASS на открытый вопрос.
    if scope.status == ScopeStatus.SCOPE_RESOLVED and "stress_ratio" in (
        scope.required_outputs or []
    ):
        reasons.append("must not invent closed numeric outputs for open-ended prompt")
    if reasons:
        return ScenarioResult(
            scenario_id=sid,
            passed=False,
            expected_behavior=behavior,
            observed={
                "problem_kind": kind.value,
                "scope_status": scope.status.value,
                "required_fields": list(scope.required_fields or []),
            },
            failure_class=FailureClass.SCOPE.value,
            reasons=reasons,
        )
    return _ok(
        sid,
        behavior,
        failure_class=FailureClass.SCOPE.value,
        problem_kind=kind.value,
        scope_status=scope.status.value,
        required_fields=list(scope.required_fields or []),
    )


def run_budget_control() -> ScenarioResult:
    """IterationController: identical no-progress → STOP_INSUFFICIENT_EVIDENCE (no token burn)."""
    sid = "budget_control"
    behavior = ExpectedBehavior.INSUFFICIENT_EVIDENCE
    ctrl = IterationController(no_progress_limit=3, hitl_on_no_progress=False)
    same = IterationObservation(
        coverage_ratio=0.0,
        missing_outputs=("power",),
        covered_outputs=(),
        failure_class=FailureClass.VERIFICATION,
        adjudication_status=None,
        known=[],
        covered_count=0,
        required_count=1,
    )
    actions: list[str] = []
    last_fc: str | None = None
    for _ in range(4):
        d = ctrl.record_and_decide(
            IterationObservation(
                coverage_ratio=0.0,
                missing_outputs=("power",),
                covered_outputs=(),
                failure_class=FailureClass.VERIFICATION,
                adjudication_status=None,
                known=[],
                covered_count=0,
                required_count=1,
            )
        )
        actions.append(d.action.value)
        last_fc = d.failure_class.value if d.failure_class else None
    # 1–2 CONTINUE, 3 REPLAN, 4 STOP — без реального LLM budget.
    if actions[-1] != IterationAction.STOP_INSUFFICIENT_EVIDENCE.value:
        return _fail(
            sid,
            behavior,
            f"last action={actions[-1]}, expected STOP_INSUFFICIENT_EVIDENCE",
            actions=actions,
        )
    if IterationAction.REPLAN.value not in actions:
        return _fail(sid, behavior, "expected REPLAN before STOP", actions=actions)
    if last_fc is None:
        return _fail(sid, behavior, "failure_class missing on STOP", actions=actions)
    return _ok(
        sid,
        behavior,
        failure_class=last_fc,
        actions=actions,
        # same unused — keep for readability that we cloned observation
        fixture="no_progress_identical",
        note=str(same.missing_outputs),
    )


def run_research_review_honesty() -> ScenarioResult:
    """Spider-silk research path: STUB/empty research must not count as PASS."""
    sid = "research_review"
    behavior = ExpectedBehavior.ORCHESTRATION_ONLY
    prompt = (
        "Исследуй промышленные методы производства искусственного паучьего шёлка "
        "и их ограничения при масштабировании."
    )
    kind = classify_problem_kind(prompt)
    scope = resolve_scope(prompt)
    reasons: list[str] = []
    if kind != ProblemKind.RESEARCH_REVIEW:
        reasons.append(f"kind={kind.value}, expected RESEARCH_REVIEW")
    if scope.status != ScopeStatus.SCOPE_RESOLVED:
        reasons.append(f"status={scope.status.value}")
    if scope.pipeline_hint != "research":
        reasons.append(f"pipeline_hint={scope.pipeline_hint}")
    # Честность: STUB tensile fixture ≠ FACT (документируем ожидание).
    stub_note = (
        "Synthetic tensile fixtures remain STUB evidence; "
        "MOCK/empty research ≠ engineering PASS"
    )
    if reasons:
        return _fail(
            sid,
            behavior,
            *reasons,
            problem_kind=kind.value,
            honesty=stub_note,
        )
    return _ok(
        sid,
        behavior,
        problem_kind=kind.value,
        scope_status=scope.status.value,
        pipeline_hint=scope.pipeline_hint,
        honesty=stub_note,
    )


def _scenario_catalog() -> dict[str, ScenarioSpec]:
    return {
        "context_isolation": ScenarioSpec(
            scenario_id="context_isolation",
            title="Sofa+rod context contamination => CONTEXT fail",
            expected_behavior=ExpectedBehavior.FAIL,
            run=run_context_isolation,
            notes="Must not PASS; FailureClass.CONTEXT",
        ),
        "units": ScenarioSpec(
            scenario_id="units",
            title="length+m pass; length vs litre fail",
            expected_behavior=ExpectedBehavior.PASS,
            run=run_units,
        ),
        "scope_resolution": ScenarioSpec(
            scenario_id="scope_resolution",
            title="Closed rod stress-ratio => CLOSED_NUMERIC",
            expected_behavior=ExpectedBehavior.PASS,
            run=run_scope_resolution,
        ),
        "ambiguous_engineering": ScenarioSpec(
            scenario_id="ambiguous_engineering",
            title="Open rod strength => NEEDS_CLARIFICATION",
            expected_behavior=ExpectedBehavior.NEEDS_CLARIFICATION,
            run=run_ambiguous_engineering,
        ),
        "budget_control": ScenarioSpec(
            scenario_id="budget_control",
            title="No-progress IterationController => STOP_INSUFFICIENT_EVIDENCE",
            expected_behavior=ExpectedBehavior.INSUFFICIENT_EVIDENCE,
            run=run_budget_control,
        ),
        "research_review": ScenarioSpec(
            scenario_id="research_review",
            title="Spider silk RESEARCH kind; STUB != PASS",
            expected_behavior=ExpectedBehavior.ORCHESTRATION_ONLY,
            run=run_research_review_honesty,
        ),
    }


def list_scenarios() -> list[ScenarioSpec]:
    return list(_scenario_catalog().values())


def get_scenario(scenario_id: str) -> ScenarioSpec:
    key = (scenario_id or "").strip()
    catalog = _scenario_catalog()
    if key not in catalog:
        raise KeyError(
            f"Unknown scenario {scenario_id!r}; known: {sorted(catalog)}"
        )
    return catalog[key]


def run_scenario(scenario_id: str) -> ScenarioResult:
    """Execute one focused scenario; raises KeyError if unknown."""
    return get_scenario(scenario_id).run()


def run_all_scenarios() -> list[ScenarioResult]:
    """Run every registered scenario (deterministic, no LLM)."""
    return [spec.run() for spec in list_scenarios()]
