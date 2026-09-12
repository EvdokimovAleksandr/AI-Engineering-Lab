"""Benchmark registry, evaluation, and lightweight routing checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ai_lab.benchmark.evaluate import evaluate_run
from ai_lab.benchmark.registry import get_benchmark, list_benchmarks
from ai_lab.benchmark.runner import run_benchmark
from ai_lab.core.enums import ProjectState
from ai_lab.core.models import LabConfig
from ai_lab.task_routing.enums import EvalVerdict, WorkflowProfile

REPO = Path(__file__).resolve().parents[1]


def test_list_three_benchmarks() -> None:
    specs = list_benchmarks(REPO)
    ids = {s.benchmark_id for s in specs}
    assert ids == {"simple_heater", "shaft_design", "spider_silk_review"}
    assert get_benchmark(REPO, "simple_heater").expectation.expected_workflow == WorkflowProfile.SIMPLE


def test_list_ignores_dot_workspace(tmp_path: Path) -> None:
    """Isolated run dir benchmarks/.workspace must not be treated as a benchmark."""
    root = tmp_path / "repo"
    benches = root / "benchmarks"
    benches.mkdir(parents=True)
    for bid in ("simple_heater", "shaft_design", "spider_silk_review"):
        d = benches / bid
        d.mkdir()
        for name in ("problem.md", "expected_capabilities.md", "evaluation.md"):
            (d / name).write_text(f"# {bid}\n", encoding="utf-8")
    (benches / ".workspace").mkdir()
    (benches / ".workspace" / "simple_heater").mkdir()
    ids = {s.benchmark_id for s in list_benchmarks(root)}
    assert ids == {"simple_heater", "shaft_design", "spider_silk_review"}
    assert ".workspace" not in ids


@pytest.mark.asyncio
async def test_benchmark_run_simple_heater_is_simple_and_isolated(tmp_path: Path) -> None:
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
    assert (run_dir / "manifest.json").is_file()
    routing = json.loads((run_dir / "planner" / "task_routing.json").read_text(encoding="utf-8"))
    assert routing["final_workflow"] == "SIMPLE"
    graph = json.loads((run_dir / "planner" / "task_graph.json").read_text(encoding="utf-8"))
    task_ids = {t["task_id"] for t in graph["tasks"]}
    assert "research" not in task_ids
    assert "red_team" not in task_ids
    # Namespace isolation: no bleed into repo projects/
    assert not (REPO / "projects" / "simple_heater").exists()

    report = evaluate_run(
        "simple_heater", snapshot.run_id, repo_root=REPO, work_root=work
    )
    assert report.observed_workflow == "SIMPLE"
    wf = next(c for c in report.categories if c.category == "workflow_selection")
    assert wf.verdict == EvalVerdict.PASS
    assert report.overall in {EvalVerdict.PASS, EvalVerdict.PARTIAL}


@pytest.mark.asyncio
async def test_benchmark_run_shaft_and_spider_routing(tmp_path: Path) -> None:
    work = tmp_path / "bench_ws"
    shaft = await run_benchmark(
        "shaft_design", repo_root=REPO, provider="mock", work_root=work
    )
    silk = await run_benchmark(
        "spider_silk_review", repo_root=REPO, provider="mock", work_root=work
    )
    shaft_r = json.loads(
        (work / "shaft_design" / ".runs" / shaft.run_id / "planner" / "task_routing.json").read_text(
            encoding="utf-8"
        )
    )
    silk_r = json.loads(
        (
            work / "spider_silk_review" / ".runs" / silk.run_id / "planner" / "task_routing.json"
        ).read_text(encoding="utf-8")
    )
    assert shaft_r["final_workflow"] == "STANDARD"
    assert silk_r["final_workflow"] == "RESEARCH"
    # Separate run namespaces
    assert shaft.run_id != silk.run_id
    assert (work / "shaft_design" / ".runs" / shaft.run_id).is_dir()
    assert (work / "spider_silk_review" / ".runs" / silk.run_id).is_dir()


def test_evaluation_underroute_fails(tmp_path: Path) -> None:
    """Synthetic manifest: shaft expected STANDARD but observed SIMPLE → FAIL."""
    work = tmp_path / "bench_ws"
    bid = "shaft_design"
    run_id = "run_fakeunder"
    run_dir = work / bid / ".runs" / run_id
    (run_dir / "planner").mkdir(parents=True)
    (run_dir / "reviews").mkdir(parents=True)
    (work / bid / "assumptions.md").write_text("# a\n", encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "project_id": bid,
        "workflow_profile": "SIMPLE",
        "final_state": "COMPLETED",
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "planner" / "task_routing.json").write_text(
        json.dumps({"final_workflow": "SIMPLE"}), encoding="utf-8"
    )
    (run_dir / "planner" / "task_graph.json").write_text(
        json.dumps({"graph_id": "simple_pipeline", "tasks": [{"task_id": "calculation"}]}),
        encoding="utf-8",
    )
    report = evaluate_run(bid, run_id, repo_root=REPO, work_root=work)
    wf = next(c for c in report.categories if c.category == "workflow_selection")
    assert wf.verdict == EvalVerdict.FAIL
    assert report.overall == EvalVerdict.FAIL
