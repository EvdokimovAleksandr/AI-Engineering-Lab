"""CLI: python -m ai_lab benchmark list|run|evaluate|scenario."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ai_lab.benchmark.evaluate import evaluate_run
from ai_lab.benchmark.mode import format_mode_line
from ai_lab.benchmark.registry import list_benchmarks
from ai_lab.benchmark.runner import run_benchmark
from ai_lab.benchmark.scenarios import list_scenarios, run_all_scenarios, run_scenario
from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.runtime import repo_root_from_here

logger = get_logger(__name__)


def build_benchmark_parser(sub: argparse._SubParsersAction) -> None:
    bench = sub.add_parser("benchmark", help="List / run / evaluate lab benchmarks")
    bench_sub = bench.add_subparsers(dest="benchmark_command", required=True)

    list_p = bench_sub.add_parser("list", help="List registered benchmarks and scenarios")
    list_p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: auto-detect)",
    )

    run_p = bench_sub.add_parser("run", help="Run a benchmark as an ordinary lab Run")
    run_p.add_argument("benchmark_id", help="e.g. simple_heater")
    run_p.add_argument(
        "--provider",
        choices=["mock", "cursor_sdk", "replay"],
        default="mock",
        help="LLM provider override (default mock)",
    )
    run_p.add_argument("--config", type=Path, default=None, help="YAML config path")
    run_p.add_argument(
        "--auto-approve-hitl",
        action="store_true",
        default=None,
        help="Force auto-approve HITL (default: on, except NEEDS_CLARIFICATION cases)",
    )
    run_p.add_argument(
        "--no-auto-approve-hitl",
        action="store_true",
        default=False,
        help="Disable HITL auto-approve",
    )
    run_p.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Isolated workspace for benchmark projects (default benchmarks/.workspace)",
    )
    run_p.add_argument(
        "--simulation-fixture",
        default=None,
        help="Optional mock simulation fixture (e.g. heater_correct)",
    )

    eval_p = bench_sub.add_parser("evaluate", help="Evaluate a completed benchmark run")
    eval_p.add_argument("benchmark_id", help="e.g. simple_heater")
    eval_p.add_argument("run_id", help="Run id from benchmark run")
    eval_p.add_argument("--work-root", type=Path, default=None)
    eval_p.add_argument("--repo-root", type=Path, default=None)

    scen_p = bench_sub.add_parser(
        "scenario",
        help="Run focused PR-07 scenarios (no full project tree required)",
    )
    scen_p.add_argument(
        "scenario_id",
        nargs="?",
        default=None,
        help="Scenario id (omit with --all)",
    )
    scen_p.add_argument(
        "--all",
        action="store_true",
        help="Run all registered scenarios",
    )


def run_benchmark_cli(args: argparse.Namespace) -> int:
    root = Path(args.repo_root) if getattr(args, "repo_root", None) else repo_root_from_here()
    cmd = args.benchmark_command

    if cmd == "list":
        specs = list_benchmarks(root)
        if not specs:
            print("No benchmarks found under benchmarks/")
        else:
            print("# project benchmarks")
            for spec in specs:
                beh = spec.expectation.expected_behavior.value
                print(
                    f"{spec.benchmark_id}\t{spec.expectation.expected_workflow.value}\t"
                    f"{beh}\t{spec.title}"
                )
        print("# focused scenarios (benchmark scenario <id>)")
        for scen in list_scenarios():
            print(
                f"{scen.scenario_id}\tscenario\t{scen.expected_behavior.value}\t{scen.title}"
            )
        return 0

    if cmd == "scenario":
        try:
            if args.all:
                results = run_all_scenarios()
            elif args.scenario_id:
                results = [run_scenario(args.scenario_id)]
            else:
                print("Specify scenario_id or --all", file=sys.stderr)
                return 2
        except KeyError as exc:
            logger.error("Scenario failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        failed = 0
        for r in results:
            print(json.dumps(r.public_dump(), indent=2, sort_keys=True))
            if not r.passed:
                failed += 1
        print(f"scenarios_passed={len(results) - failed}/{len(results)}")
        return 0 if failed == 0 else 1

    if cmd == "run":
        auto = getattr(args, "auto_approve_hitl", None)
        if getattr(args, "no_auto_approve_hitl", False):
            auto = False
        try:
            snapshot = asyncio.run(
                run_benchmark(
                    args.benchmark_id,
                    repo_root=root,
                    provider=args.provider,
                    config_path=args.config,
                    auto_approve_hitl=auto,
                    work_root=getattr(args, "work_root", None),
                    simulation_fixture=getattr(args, "simulation_fixture", None),
                )
            )
        except Exception as exc:
            logger.error("Benchmark run failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"benchmark={args.benchmark_id}")
        print(f"run_id={snapshot.run_id}")
        print(f"state={snapshot.state.value}")
        print(format_mode_line(args.provider))
        return 0

    if cmd == "evaluate":
        try:
            report = evaluate_run(
                args.benchmark_id,
                args.run_id,
                repo_root=root,
                work_root=getattr(args, "work_root", None),
            )
        except Exception as exc:
            logger.error("Benchmark evaluate failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        mode = (report.details or {}).get("execution_mode")
        provider = (report.details or {}).get("provider")
        if mode:
            print(f"MODE: {mode} (provider={provider})")
        print(json.dumps(report.public_dump(), indent=2, sort_keys=True))
        return 0 if report.overall.value != "FAIL" else 1

    print(f"Unknown benchmark command {cmd}", file=sys.stderr)
    return 2
