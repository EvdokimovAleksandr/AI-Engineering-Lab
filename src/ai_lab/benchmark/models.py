"""Benchmark evaluation models — structured, not free-text scoring."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.task_routing.enums import EvalVerdict, WorkflowProfile


class ExpectedBehavior(str, Enum):
    """What the lab should do — not only numeric PASS (PR-07)."""

    PASS = "PASS"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAIL = "FAIL"
    # Routing / structure / honesty matter; numeric engineering PASS not required.
    ORCHESTRATION_ONLY = "ORCHESTRATION_ONLY"


class CategoryScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    verdict: EvalVerdict
    rationale: str = ""


class OutputAcceptance(BaseModel):
    """Required quantitative output for benchmark acceptance (generic, not runtime hack)."""

    model_config = ConfigDict(extra="forbid")

    output_id: str
    aliases: list[str] = Field(default_factory=list)
    # Unit whose *dimension* must match declared output (Pint); W matches kW.
    dimension_unit: str
    min_value: float | None = None
    max_value: float | None = None
    # Magnitude band compared after conversion into this unit (defaults to dimension_unit).
    band_unit: str = ""


class InputAcceptance(BaseModel):
    """Optional oracle on math_check / computation inputs (wrong m/ΔT/t must fail)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    value: float
    unit: str = ""
    relative_tolerance: float = Field(default=0.02, ge=0.0)
    absolute_tolerance: float | None = None
    # When False, missing input is skipped; wrong present value still fails (V2.6.1).
    required: bool = True


class AcceptanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    reasons: list[str] = Field(default_factory=list)
    covered_outputs: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class BenchmarkExpectation(BaseModel):
    """Declared expected behavior for a benchmark (not a hardcoded numeric answer)."""

    model_config = ConfigDict(extra="forbid")

    benchmark_id: str
    expected_workflow: WorkflowProfile
    # Over-routing to a richer profile is PARTIAL if still correct; under-routing is FAIL.
    acceptable_over_route: list[WorkflowProfile] = Field(default_factory=list)
    forbidden_workflows: list[WorkflowProfile] = Field(default_factory=list)
    must_include_tasks: list[str] = Field(default_factory=list)
    must_exclude_tasks: list[str] = Field(default_factory=list)
    notes: str = ""
    # V2.6.1 generic acceptance contract (empty ⇒ no numeric oracle).
    acceptance_outputs: list[OutputAcceptance] = Field(default_factory=list)
    acceptance_inputs: list[InputAcceptance] = Field(default_factory=list)
    # PR-07: refusal / clarification / budget-stop expectations.
    expected_behavior: ExpectedBehavior = ExpectedBehavior.PASS
    # FailureClass.value when FAIL / INSUFFICIENT_EVIDENCE is the intended outcome.
    expected_failure_class: str | None = None
    expected_scope_status: str | None = None
    expected_problem_kind: str | None = None
    required_clarification_fields: list[str] = Field(default_factory=list)
    # Optional mock simulation fixture for closed numeric PASS path (e.g. heater_correct).
    simulation_fixture: str | None = None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_id: str
    run_id: str
    overall: EvalVerdict
    categories: list[CategoryScore] = Field(default_factory=list)
    observed_workflow: str | None = None
    expected_workflow: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    def public_dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
