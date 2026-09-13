"""PR-07: behavioral benchmarks, scenarios, MODE / FailureClass in evaluate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_lab.benchmark.evaluate import evaluate_run
from ai_lab.benchmark.mode import execution_mode_from_provider, format_mode_line
from ai_lab.benchmark.models import ExpectedBehavior
from ai_lab.benchmark.registry import get_benchmark, list_benchmarks
from ai_lab.benchmark.runner import run_benchmark
from ai_lab.benchmark.scenarios import list_scenarios, run_all_scenarios, run_scenario
from ai_lab.core.enums import FailureClass, ProjectState
from ai_lab.task_routing.enums import EvalVerdict, WorkflowProfile

REPO = Path(__file__).resolve().parents[1]


def test_execution_mode_mapping() -> None:
    assert execution_mode_from_provider("mock") == "MOCK"
    assert execution_mode_from_provider("cursor_sdk") == "LIVE_CURSOR"
    assert execution_mode_from_provider("replay") == "REPLAY"
    assert "MODE: MOCK" in format_mode_line("mock")


def test_list_includes_ambiguous_and_scenarios() -> None:
    ids = {s.benchmark_id for s in list_benchmarks(REPO)}
    assert "ambiguous_rod_strength" in ids
    assert "simple_heater" in ids
    assert "spider_silk_review" in ids
    scen = {s.scenario_id for s in list_scenarios()}
    assert scen >= {
        "context_isolation",
        "units",
        "scope_resolution",
        "ambiguous_engineering",
        "budget_control",
        "research_review",
    }
    silk = get_benchmark(REPO, "spider_silk_review")
    assert silk.expectation.expected_behavior == ExpectedBehavior.ORCHESTRATION_ONLY
    amb = get_benchmark(REPO, "ambiguous_rod_strength")
    assert amb.expectation.expected_behavior == ExpectedBehavior.NEEDS_CLARIFICATION
    assert "load_type" in amb.expectation.required_clarification_fields


def test_all_focused_scenarios_pass() -> None:
    results = run_all_scenarios()
    failed = [r for r in results if not r.passed]
    assert not failed, [r.public_dump() for r in failed]
    ctx = run_scenario("context_isolation")
    assert ctx.failure_class == FailureClass.CONTEXT.value
    budget = run_scenario("budget_control")
    assert budget.expected_behavior == ExpectedBehavior.INSUFFICIENT_EVIDENCE
    assert budget.failure_class is not None


def test_spider_silk_phase00_scaffold_exists() -> None:
    phase = REPO / "benchmarks" / "spider_silk_review" / "phases" / "00_problem_definition"
    assert (phase / "problem.md").is_file()
    assert (phase / "evaluation.md").is_file()
    text = (phase / "problem.md").read_text(encoding="utf-8").lower()
    assert "bottleneck" in text or "cost" in text
    assert "stub" in text


@pytest.mark.asyncio
async def test_ambiguous_rod_benchmark_awaits_clarification(tmp_path: Path) -> None:
    work = tmp_path / "bench_ws"
    snapshot = await run_benchmark(
        "ambiguous_rod_strength",
        repo_root=REPO,
        provider="mock",
        work_root=work,
    )
    assert snapshot.state == ProjectState.AWAITING_HUMAN
    run_dir = work / "ambiguous_rod_strength" / ".runs" / snapshot.run_id
    scope = json.loads((run_dir / "planner" / "scope.json").read_text(encoding="utf-8"))
    assert scope["status"] == "SCOPE_NEEDS_CLARIFICATION"
    assert "load_type" in (scope.get("required_fields") or [])
    assert scope.get("problem_kind") == "OPEN_ENDED"

    report = evaluate_run(
        "ambiguous_rod_strength",
        snapshot.run_id,
        repo_root=REPO,
        work_root=work,
    )
    assert report.details.get("execution_mode") == "MOCK"
    beh = next(c for c in report.categories if c.category == "expected_behavior")
    assert beh.verdict == EvalVerdict.PASS
    assert report.overall in {EvalVerdict.PASS, EvalVerdict.PARTIAL}


@pytest.mark.asyncio
async def test_simple_heater_mock_pass_path(tmp_path: Path) -> None:
    """Closed calculation under mock + heater_correct remains a PASS path."""
    work = tmp_path / "bench_ws"
    snapshot = await run_benchmark(
        "simple_heater",
        repo_root=REPO,
        provider="mock",
        work_root=work,
        auto_approve_hitl=True,
    )
    assert snapshot.state == ProjectState.COMPLETED
    run_dir = work / "simple_heater" / ".runs" / snapshot.run_id
    routing = json.loads((run_dir / "planner" / "task_routing.json").read_text(encoding="utf-8"))
    assert routing["final_workflow"] == WorkflowProfile.SIMPLE.value
    report = evaluate_run(
        "simple_heater", snapshot.run_id, repo_root=REPO, work_root=work
    )
    assert report.details.get("execution_mode") == "MOCK"
    assert report.observed_workflow == "SIMPLE"
    # With heater_correct fixture, engineering PASS is expected when gates hold.
    eng = next(c for c in report.categories if c.category == "engineering_success")
    assert eng.verdict in {EvalVerdict.PASS, EvalVerdict.NOT_APPLICABLE}
    tech = next(c for c in report.categories if c.category == "technical_success")
    assert tech.verdict == EvalVerdict.PASS


def test_evaluate_orchestration_only_rejects_empty_research_pass(tmp_path: Path) -> None:
    """Honesty gate: RESEARCH_EMPTY + eng PASS → expected_behavior FAIL."""
    work = tmp_path / "bench_ws"
    bid = "spider_silk_review"
    run_id = "run_fake_empty_pass"
    run_dir = work / bid / ".runs" / run_id
    (run_dir / "planner").mkdir(parents=True)
    (run_dir / "reviews").mkdir(parents=True)
    (work / bid / "assumptions.md").write_text("# a\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "project_id": bid,
                "workflow_profile": "RESEARCH",
                "final_state": "COMPLETED",
                "engineering_outcome": "PASS",
                "model_provider": "mock",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "planner" / "task_routing.json").write_text(
        json.dumps({"final_workflow": "RESEARCH"}), encoding="utf-8"
    )
    (run_dir / "planner" / "task_graph.json").write_text(
        json.dumps(
            {
                "graph_id": "research_pipeline",
                "tasks": [
                    {"task_id": "research"},
                    {"task_id": "hypothesis"},
                    {"task_id": "verification"},
                    {"task_id": "red_team"},
                    {"task_id": "deterministic_verify"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "reviews" / "research_sufficiency.json").write_text(
        json.dumps({"outcome": "RESEARCH_EMPTY"}), encoding="utf-8"
    )
    report = evaluate_run(bid, run_id, repo_root=REPO, work_root=work)
    assert report.details.get("execution_mode") == "MOCK"
    beh = next(c for c in report.categories if c.category == "expected_behavior")
    assert beh.verdict == EvalVerdict.FAIL
    assert report.overall == EvalVerdict.FAIL
