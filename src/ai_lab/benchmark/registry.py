"""Benchmark registry — discovers benchmarks/ trees without cross-namespace pollution."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ai_lab.benchmark.models import BenchmarkExpectation
from ai_lab.task_routing.enums import WorkflowProfile

# Required files every benchmark project must ship.
REQUIRED_DOCS = ("problem.md", "expected_capabilities.md", "evaluation.md")


class BenchmarkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_id: str
    root: Path
    expectation: BenchmarkExpectation
    title: str = ""
    summary: str = ""


def _default_expectations() -> dict[str, BenchmarkExpectation]:
    from ai_lab.benchmark.models import (
        ExpectedBehavior,
        InputAcceptance,
        OutputAcceptance,
    )

    # simple_heater: order-of-magnitude band from evaluation.md (~3–3.5 kW).
    # Both loss interpretations (×1.15 and /(1-0.15)) fall inside this band.
    heater_power = OutputAcceptance(
        output_id="power",
        aliases=[
            "heater_electrical_power",
            "required_electric_power",
            "required_electrical_power",
            "required_heater_power",
            "heater_power",
            "electrical_power",
            "P_elec",
            "P",
            "P_heater",
        ],
        dimension_unit="W",
        min_value=3000.0,
        max_value=3500.0,
        band_unit="W",
    )
    # Input oracle catches wrong m / ΔT / t while accidental numeric match.
    heater_inputs = [
        InputAcceptance(
            name="m_kg", aliases=["m", "mass_kg", "water_mass_kg"], value=20.0, required=False
        ),
        InputAcceptance(
            name="dT", aliases=["delta_T", "delta_t", "dT_K"], value=60.0, required=False
        ),
        InputAcceptance(
            name="t_s",
            aliases=["t", "time_s", "heating_time_s"],
            value=1800.0,
            relative_tolerance=0.01,
            required=False,
        ),
        InputAcceptance(
            name="loss",
            aliases=["loss_fraction", "losses"],
            value=0.15,
            absolute_tolerance=0.001,
            required=False,
        ),
    ]
    return {
        "simple_heater": BenchmarkExpectation(
            benchmark_id="simple_heater",
            expected_workflow=WorkflowProfile.SIMPLE,
            acceptable_over_route=[WorkflowProfile.STANDARD],
            forbidden_workflows=[WorkflowProfile.RESEARCH],
            must_include_tasks=["calculation", "deterministic_verify"],
            must_exclude_tasks=["research", "red_team", "hypothesis"],
            notes=(
                "Closed-form heater power; must not start full research workflow. "
                "Acceptance: power in [3.0, 3.5] kW with correct inputs. "
                "PASS path under mock: simulation_fixture=heater_correct."
            ),
            acceptance_outputs=[heater_power],
            acceptance_inputs=heater_inputs,
            expected_behavior=ExpectedBehavior.PASS,
            expected_problem_kind="CLOSED_NUMERIC",
            simulation_fixture="heater_correct",
        ),
        "shaft_design": BenchmarkExpectation(
            benchmark_id="shaft_design",
            expected_workflow=WorkflowProfile.STANDARD,
            acceptable_over_route=[WorkflowProfile.COMPLEX],
            forbidden_workflows=[WorkflowProfile.SIMPLE],
            must_include_tasks=["decomposition", "calculation", "verification"],
            must_exclude_tasks=[],
            notes="Preliminary shaft diameter; under-routing to SIMPLE is FAIL.",
            expected_behavior=ExpectedBehavior.PASS,
        ),
        "spider_silk_review": BenchmarkExpectation(
            benchmark_id="spider_silk_review",
            expected_workflow=WorkflowProfile.RESEARCH,
            acceptable_over_route=[],
            forbidden_workflows=[WorkflowProfile.SIMPLE, WorkflowProfile.STANDARD],
            must_include_tasks=["research", "hypothesis", "verification", "red_team"],
            must_exclude_tasks=[],
            notes=(
                "Orchestration benchmark; research backend may be MOCK. "
                "STUB/empty research ≠ engineering PASS (ORCHESTRATION_ONLY). "
                "See phases/00_problem_definition for spider silk 2.0 cost-bottleneck focus."
            ),
            expected_behavior=ExpectedBehavior.ORCHESTRATION_ONLY,
            expected_problem_kind="RESEARCH_REVIEW",
        ),
        # PR-07: open-ended strength question must stop for clarification, not invent numbers.
        "ambiguous_rod_strength": BenchmarkExpectation(
            benchmark_id="ambiguous_rod_strength",
            expected_workflow=WorkflowProfile.SIMPLE,
            acceptable_over_route=[
                WorkflowProfile.STANDARD,
                WorkflowProfile.COMPLEX,
                WorkflowProfile.RESEARCH,
            ],
            forbidden_workflows=[],
            must_include_tasks=[],
            must_exclude_tasks=[],
            notes=(
                "OPEN_ENDED: «Как сделать стержень прочнее?» → NEEDS_CLARIFICATION; "
                "required field load_type. Fake numeric PASS is FAIL."
            ),
            expected_behavior=ExpectedBehavior.NEEDS_CLARIFICATION,
            expected_scope_status="SCOPE_NEEDS_CLARIFICATION",
            expected_problem_kind="OPEN_ENDED",
            expected_failure_class="SCOPE",
            required_clarification_fields=["load_type"],
        ),
    }


def benchmarks_root(repo_root: Path) -> Path:
    return repo_root / "benchmarks"


def list_benchmarks(repo_root: Path) -> list[BenchmarkSpec]:
    root = benchmarks_root(repo_root)
    if not root.is_dir():
        return []
    expectations = _default_expectations()
    out: list[BenchmarkSpec] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        # Skip isolated run workspaces and other hidden dirs (e.g. benchmarks/.workspace).
        if child.name.startswith("."):
            continue
        bid = child.name
        missing = [name for name in REQUIRED_DOCS if not (child / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"Benchmark {bid!r} missing required files: {missing}"
            )
        exp = expectations.get(bid)
        if exp is None:
            raise KeyError(
                f"Benchmark {bid!r} has no registered expectation "
                "(add it to benchmark.registry)"
            )
        title = ""
        summary = ""
        problem = (child / "problem.md").read_text(encoding="utf-8")
        for line in problem.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        summary = problem.strip().split("\n\n", 1)[0][:240]
        out.append(
            BenchmarkSpec(
                benchmark_id=bid,
                root=child,
                expectation=exp,
                title=title,
                summary=summary,
            )
        )
    return out


def get_benchmark(repo_root: Path, benchmark_id: str) -> BenchmarkSpec:
    key = (benchmark_id or "").strip()
    if not key or ".." in key or "/" in key or "\\" in key:
        raise ValueError(f"Invalid benchmark_id: {benchmark_id!r}")
    for spec in list_benchmarks(repo_root):
        if spec.benchmark_id == key:
            return spec
    raise KeyError(f"Unknown benchmark {benchmark_id!r}")
