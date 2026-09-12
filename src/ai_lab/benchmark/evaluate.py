"""Evaluate a benchmark run against structured expectations."""

from __future__ import annotations

import json
from pathlib import Path

from ai_lab.benchmark.models import (
    BenchmarkExpectation,
    CategoryScore,
    EvaluationReport,
)
from ai_lab.benchmark.registry import get_benchmark
from ai_lab.task_routing.enums import EvalVerdict, WorkflowProfile


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Missing artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _workflow_verdict(
    observed: WorkflowProfile | None,
    expectation: BenchmarkExpectation,
) -> CategoryScore:
    if observed is None:
        return CategoryScore(
            category="workflow_selection",
            verdict=EvalVerdict.FAIL,
            rationale="No workflow_profile on run manifest / routing decision",
        )
    if observed == expectation.expected_workflow:
        return CategoryScore(
            category="workflow_selection",
            verdict=EvalVerdict.PASS,
            rationale=f"Router selected expected {observed.value}",
        )
    if observed in expectation.forbidden_workflows:
        return CategoryScore(
            category="workflow_selection",
            verdict=EvalVerdict.FAIL,
            rationale=(
                f"Router selected forbidden {observed.value}; "
                f"expected {expectation.expected_workflow.value}"
            ),
        )
    if observed in expectation.acceptable_over_route:
        return CategoryScore(
            category="workflow_selection",
            verdict=EvalVerdict.PARTIAL,
            rationale=(
                f"Over-routed to {observed.value} (acceptable but heavier than "
                f"{expectation.expected_workflow.value})"
            ),
        )
    # Under-route relative to expected (lower rank) → FAIL
    ranks = {
        WorkflowProfile.SIMPLE: 0,
        WorkflowProfile.STANDARD: 1,
        WorkflowProfile.COMPLEX: 2,
        WorkflowProfile.RESEARCH: 3,
    }
    if ranks[observed] < ranks[expectation.expected_workflow]:
        return CategoryScore(
            category="workflow_selection",
            verdict=EvalVerdict.FAIL,
            rationale=(
                f"Under-routed to {observed.value}; "
                f"expected {expectation.expected_workflow.value}"
            ),
        )
    return CategoryScore(
        category="workflow_selection",
        verdict=EvalVerdict.PARTIAL,
        rationale=f"Unexpected profile {observed.value}",
    )


def _task_set(graph: dict) -> set[str]:
    return {t["task_id"] for t in graph.get("tasks") or []}


def evaluate_run(
    benchmark_id: str,
    run_id: str,
    *,
    repo_root: Path,
    work_root: Path | None = None,
) -> EvaluationReport:
    """Score a completed (or planned) run without relying on free-text answers."""
    spec = get_benchmark(repo_root, benchmark_id)
    base = work_root or (repo_root / "benchmarks" / ".workspace")
    run_dir = base / benchmark_id / ".runs" / run_id
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    manifest = _load_json(run_dir / "manifest.json")
    routing_path = run_dir / "planner" / "task_routing.json"
    graph_path = run_dir / "planner" / "task_graph.json"
    routing = _load_json(routing_path) if routing_path.is_file() else {}
    graph = _load_json(graph_path) if graph_path.is_file() else {}

    observed_raw = manifest.get("workflow_profile") or routing.get("final_workflow")
    observed: WorkflowProfile | None = None
    if observed_raw:
        observed = WorkflowProfile(str(observed_raw))

    categories: list[CategoryScore] = [
        _workflow_verdict(observed, spec.expectation),
    ]

    tasks = _task_set(graph)
    missing = [t for t in spec.expectation.must_include_tasks if t not in tasks]
    forbidden_present = [t for t in spec.expectation.must_exclude_tasks if t in tasks]
    if missing:
        categories.append(
            CategoryScore(
                category="resource_efficiency",
                verdict=EvalVerdict.FAIL,
                rationale=f"Missing required tasks: {missing}",
            )
        )
    elif forbidden_present:
        categories.append(
            CategoryScore(
                category="resource_efficiency",
                verdict=EvalVerdict.FAIL,
                rationale=f"Unnecessary full-lab tasks present: {forbidden_present}",
            )
        )
    else:
        categories.append(
            CategoryScore(
                category="resource_efficiency",
                verdict=EvalVerdict.PASS,
                rationale="Task set matches profile expectations",
            )
        )

    # Deterministic validation / verification presence
    has_det = "deterministic_verify" in tasks
    categories.append(
        CategoryScore(
            category="deterministic_validation",
            verdict=EvalVerdict.PASS if has_det else EvalVerdict.FAIL,
            rationale="deterministic_verify node present" if has_det else "missing",
        )
    )
    if observed == WorkflowProfile.SIMPLE:
        categories.append(
            CategoryScore(
                category="verification",
                verdict=EvalVerdict.NOT_APPLICABLE,
                rationale="SIMPLE profile does not require independent verification agent",
            )
        )
    else:
        has_v = "verification" in tasks
        categories.append(
            CategoryScore(
                category="verification",
                verdict=EvalVerdict.PASS if has_v else EvalVerdict.FAIL,
                rationale="verification node present" if has_v else "missing verification",
            )
        )

    # Evidence / assumptions / uncertainty — orchestration-oriented for research.
    if observed == WorkflowProfile.RESEARCH:
        categories.extend(
            [
                CategoryScore(
                    category="evidence_quality",
                    verdict=EvalVerdict.PASS if "research" in tasks else EvalVerdict.FAIL,
                    rationale="research node present for RESEARCH profile",
                ),
                CategoryScore(
                    category="uncertainty_handling",
                    verdict=EvalVerdict.PASS if "hypothesis" in tasks else EvalVerdict.PARTIAL,
                    rationale="hypothesis stage exercises uncertainty articulation",
                ),
                CategoryScore(
                    category="assumption_quality",
                    verdict=EvalVerdict.PASS if (base / benchmark_id / "assumptions.md").is_file() else EvalVerdict.FAIL,
                    rationale="assumptions.md present in benchmark project",
                ),
            ]
        )
    else:
        categories.append(
            CategoryScore(
                category="evidence_quality",
                verdict=EvalVerdict.NOT_APPLICABLE,
                rationale="Not a RESEARCH orchestration benchmark",
            )
        )
        categories.append(
            CategoryScore(
                category="uncertainty_handling",
                verdict=EvalVerdict.NOT_APPLICABLE
                if observed == WorkflowProfile.SIMPLE
                else EvalVerdict.PARTIAL,
                rationale="Checked via routing axes / analysis stage when present",
            )
        )
        categories.append(
            CategoryScore(
                category="assumption_quality",
                verdict=EvalVerdict.PASS
                if (base / benchmark_id / "assumptions.md").is_file()
                else EvalVerdict.PARTIAL,
                rationale="Project assumptions file present",
            )
        )

    categories.append(
        CategoryScore(
            category="traceability",
            verdict=EvalVerdict.PASS
            if (run_dir / "manifest.json").is_file() and routing_path.is_file()
            else EvalVerdict.FAIL,
            rationale="RunManifest + task_routing.json persisted under run namespace",
        )
    )

    # Correctness: order-of-magnitude / structure only — never hardcode heater kW.
    categories.append(
        CategoryScore(
            category="correctness",
            verdict=EvalVerdict.PARTIAL
            if observed is not None
            else EvalVerdict.FAIL,
            rationale=(
                "Structural/orchestration correctness only; numeric truth requires "
                "deterministic checks on claims (see evaluation.md)"
            ),
        )
    )

    overall = _rollup(categories)
    report = EvaluationReport(
        benchmark_id=benchmark_id,
        run_id=run_id,
        overall=overall,
        categories=categories,
        observed_workflow=observed.value if observed else None,
        expected_workflow=spec.expectation.expected_workflow.value,
        details={
            "graph_id": graph.get("graph_id"),
            "task_ids": sorted(tasks),
            "final_state": manifest.get("final_state"),
        },
    )
    out_path = run_dir / "reviews" / "benchmark_evaluation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.public_dump(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _rollup(categories: list[CategoryScore]) -> EvalVerdict:
    verdicts = [c.verdict for c in categories if c.verdict != EvalVerdict.NOT_APPLICABLE]
    if not verdicts:
        return EvalVerdict.NOT_APPLICABLE
    if any(v == EvalVerdict.FAIL for v in verdicts):
        return EvalVerdict.FAIL
    if any(v == EvalVerdict.PARTIAL for v in verdicts):
        return EvalVerdict.PARTIAL
    return EvalVerdict.PASS
