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

    # Correctness / integrity dimensions (V2.6) — never compensated by LLM prose.
    adj_path = run_dir / "reviews" / "last_adjudication.json"
    completeness_path = run_dir / "reviews" / "evidence_completeness.json"
    synthesis_path = run_dir / "reviews" / "synthesis_bundle.json"
    adj = _load_json(adj_path) if adj_path.is_file() else {}
    completeness = _load_json(completeness_path) if completeness_path.is_file() else {}
    synthesis = _load_json(synthesis_path) if synthesis_path.is_file() else {}

    eng_outcome = manifest.get("engineering_outcome") or adj.get("engineering_outcome") or adj.get("status")
    technical_ok = manifest.get("final_state") == "COMPLETED"
    categories.append(
        CategoryScore(
            category="technical_success",
            verdict=EvalVerdict.PASS if technical_ok else EvalVerdict.FAIL,
            rationale=f"final_state={manifest.get('final_state')}",
        )
    )
    has_completeness = bool(completeness)
    if has_completeness:
        categories.append(
            CategoryScore(
                category="engineering_success",
                verdict=EvalVerdict.PASS if eng_outcome == "PASS" else EvalVerdict.FAIL,
                rationale=f"engineering_outcome={eng_outcome}",
            )
        )
        ver_complete = bool(completeness.get("verification_complete"))
        categories.append(
            CategoryScore(
                category="verification_success",
                verdict=EvalVerdict.PASS
                if ver_complete and completeness.get("required_checks_pass")
                else EvalVerdict.FAIL,
                rationale=(
                    f"verification_complete={completeness.get('verification_complete')} "
                    f"checks_pass={completeness.get('required_checks_pass')}"
                ),
            )
        )
        categories.append(
            CategoryScore(
                category="computation_relevance",
                verdict=EvalVerdict.PASS
                if completeness.get("computation_relevant")
                else EvalVerdict.FAIL,
                rationale=f"computation_relevant={completeness.get('computation_relevant')}",
            )
        )
        categories.append(
            CategoryScore(
                category="verification_completeness",
                verdict=EvalVerdict.PASS if ver_complete else EvalVerdict.FAIL,
                rationale=f"executed_checks={completeness.get('executed_checks')}",
            )
        )
        categories.append(
            CategoryScore(
                category="provenance_completeness",
                verdict=EvalVerdict.PASS
                if completeness.get("provenance_complete")
                else EvalVerdict.FAIL,
                rationale=f"provenance_complete={completeness.get('provenance_complete')}",
            )
        )
        if eng_outcome == "PASS":
            grounded_ok = bool(synthesis.get("verified_results"))
        else:
            grounded_ok = not bool(synthesis.get("accepted_claims"))
        categories.append(
            CategoryScore(
                category="synthesis_grounding",
                verdict=EvalVerdict.PASS if grounded_ok else EvalVerdict.FAIL,
                rationale=(
                    f"verified_results={len(synthesis.get('verified_results') or [])} "
                    f"accepted={len(synthesis.get('accepted_claims') or [])}"
                ),
            )
        )
        acceptance_path = run_dir / "reviews" / "benchmark_acceptance.json"
        if acceptance_path.is_file():
            acceptance = _load_json(acceptance_path)
            categories.append(
                CategoryScore(
                    category="benchmark_acceptance",
                    verdict=EvalVerdict.PASS
                    if acceptance.get("passed")
                    else EvalVerdict.FAIL,
                    rationale="; ".join(acceptance.get("reasons") or [])
                    or f"passed={acceptance.get('passed')}",
                )
            )
        elif completeness.get("acceptance_passed") is False:
            categories.append(
                CategoryScore(
                    category="benchmark_acceptance",
                    verdict=EvalVerdict.FAIL,
                    rationale="acceptance_passed=false in evidence_completeness",
                )
            )
        categories.append(
            CategoryScore(
                category="required_output_coverage",
                verdict=EvalVerdict.PASS
                if completeness.get("required_output_coverage", True)
                else EvalVerdict.FAIL,
                rationale=f"required_output_coverage={completeness.get('required_output_coverage')}",
            )
        )
        categories.append(
            CategoryScore(
                category="numerical_correctness",
                verdict=EvalVerdict.PASS
                if eng_outcome == "PASS" and ver_complete
                else EvalVerdict.FAIL
                if eng_outcome == "FAIL"
                else EvalVerdict.PARTIAL,
                rationale=f"Derived from deterministic checks / engineering_outcome={eng_outcome}",
            )
        )
    else:
        categories.append(
            CategoryScore(
                category="engineering_success",
                verdict=EvalVerdict.NOT_APPLICABLE,
                rationale="No evidence_completeness.json (pre-V2.6 run)",
            )
        )

    categories.append(
        CategoryScore(
            category="task_understanding",
            verdict=EvalVerdict.PARTIAL if observed is not None else EvalVerdict.FAIL,
            rationale="Orchestration/routing signal only; numeric understanding is non-authoritative",
        )
    )

    # Legacy structural correctness category kept for compatibility.
    categories.append(
        CategoryScore(
            category="correctness",
            verdict=EvalVerdict.PASS
            if eng_outcome == "PASS"
            else EvalVerdict.PARTIAL
            if observed is not None
            else EvalVerdict.FAIL,
            rationale=(
                "Engineering PASS requires verified computation chain; "
                "LLM prose alone never yields PASS"
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
            "engineering_outcome": eng_outcome,
            "technical_success": technical_ok,
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
