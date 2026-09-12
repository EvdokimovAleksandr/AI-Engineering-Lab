"""Benchmark evaluation models — structured, not free-text scoring."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_lab.task_routing.enums import EvalVerdict, WorkflowProfile


class CategoryScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    verdict: EvalVerdict
    rationale: str = ""


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
