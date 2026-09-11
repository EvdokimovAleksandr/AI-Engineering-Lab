"""CLI entrypoint: python -m ai_lab run <project> --provider mock."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.runtime import plan_project, run_project

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-lab", description="AI Engineering Lab")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a lab project workflow")
    run_p.add_argument(
        "project",
        help="Project name under projects/ or path to project directory",
    )
    run_p.add_argument(
        "--provider",
        choices=["mock", "cursor_sdk", "replay"],
        default=None,
        help="Override config provider",
    )
    run_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config",
    )
    run_p.add_argument(
        "--auto-approve-hitl",
        action="store_true",
        help="Auto-approve human gates (tests/demo only)",
    )
    run_p.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted run using project_state.json run_id",
    )

    plan_p = sub.add_parser(
        "plan",
        help="Build and validate a TaskGraph without executing the lab pipeline",
    )
    plan_p.add_argument(
        "project",
        help="Project name under projects/ or path to project directory",
    )
    plan_p.add_argument(
        "--provider",
        choices=["mock", "cursor_sdk", "replay"],
        default=None,
        help="Override config provider",
    )
    plan_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config",
    )
    plan_p.add_argument(
        "--auto-approve-hitl",
        action="store_true",
        help="Auto-approve plan HITL if runtime.hitl_on_plan is set",
    )

    research_p = sub.add_parser(
        "research",
        help="Run a single research query and print structured sources/evidence/provenance",
    )
    research_p.add_argument("query", help="Search query")
    research_p.add_argument(
        "--provider",
        choices=["mock", "cursor_sdk", "replay"],
        default=None,
        help="Override LLM provider in config (does not change research.backend)",
    )
    research_p.add_argument(
        "--research-backend",
        choices=["mock", "replay", "web"],
        default=None,
        help="Override config research.backend",
    )
    research_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config",
    )
    research_p.add_argument(
        "--project",
        default=None,
        help="Optional project name or path — ingest results into Knowledge V2.1",
    )
    research_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max sources to retrieve",
    )

    routing_p = sub.add_parser(
        "routing",
        help="Show validated model routing / independence policy (no LLM calls)",
    )
    routing_p.add_argument(
        "--provider",
        choices=["mock", "cursor_sdk", "replay"],
        default=None,
        help="Override config provider for all roles",
    )
    routing_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config",
    )

    sandbox_p = sub.add_parser(
        "sandbox",
        help="Show compute sandbox backend capabilities for this platform (no execution)",
    )
    sandbox_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config",
    )

    ui_p = sub.add_parser("ui", help="Local web UI for posing an engineering problem")
    ui_p.add_argument("--host", default="127.0.0.1", help="Bind address")
    ui_p.add_argument("--port", type=int, default=8765, help="Bind port")
    ui_p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to YAML config (trusted; UI cannot override sandbox/routing)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        project_arg = args.project
        projects_dir = None
        project_name = project_arg
        path = Path(project_arg)
        if path.exists() and path.is_dir():
            projects_dir = path.parent
            project_name = path.name
        elif project_arg.startswith("projects/"):
            project_name = Path(project_arg).name

        try:
            snapshot = asyncio.run(
                run_project(
                    project_name,
                    provider=args.provider,
                    projects_dir=projects_dir,
                    config_path=args.config,
                    auto_approve_hitl=args.auto_approve_hitl,
                    resume=bool(getattr(args, "resume", False)),
                )
            )
        except Exception as exc:
            logger.error("Lab run failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

        print(f"run_id={snapshot.run_id}")
        print(f"state={snapshot.state.value}")
        print(f"iteration={snapshot.iteration}")
        return 0

    if args.command == "plan":
        project_arg = args.project
        projects_dir = None
        project_name = project_arg
        path = Path(project_arg)
        if path.exists() and path.is_dir():
            projects_dir = path.parent
            project_name = path.name
        elif project_arg.startswith("projects/"):
            project_name = Path(project_arg).name
        try:
            graph, validation = asyncio.run(
                plan_project(
                    project_name,
                    provider=args.provider,
                    projects_dir=projects_dir,
                    config_path=args.config,
                    auto_approve_hitl=bool(getattr(args, "auto_approve_hitl", False)),
                )
            )
        except Exception as exc:
            logger.error("Lab plan failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"graph_id={graph.graph_id}")
        print(f"version={graph.version}")
        print(f"ok={validation.get('ok') if isinstance(validation, dict) else validation}")
        if isinstance(validation, dict):
            print(f"reason={validation.get('reason')}")
            print(f"hash={validation.get('graph_hash')}")
            topo = validation.get("topo_order") or []
            print(f"topo_order={' '.join(topo)}")
        for task in graph.tasks:
            role = task.role.value if task.role else task.task_kind.value
            deps = ",".join(task.depends_on) if task.depends_on else "-"
            print(f"  {task.task_id}\t{role}\tdeps={deps}\tschema={task.output_schema}")
        return 0

    if args.command == "research":
        from ai_lab.cli_research import run_research_cli

        try:
            return asyncio.run(
                run_research_cli(
                    query=args.query,
                    config_path=args.config,
                    provider=args.provider,
                    research_backend=args.research_backend,
                    project=args.project,
                    limit=args.limit,
                )
            )
        except Exception as exc:
            logger.error("Research query failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.command == "routing":
        from ai_lab.cli_routing import run_routing_cli

        try:
            return run_routing_cli(config_path=args.config, provider=args.provider)
        except Exception as exc:
            logger.error("Routing inspect failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.command == "sandbox":
        from ai_lab.cli_sandbox import run_sandbox_cli

        try:
            return run_sandbox_cli(config_path=args.config)
        except Exception as exc:
            logger.error("Sandbox inspect failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if args.command == "ui":
        from ai_lab.cli_ui import run_ui_cli

        try:
            return run_ui_cli(host=args.host, port=args.port, config_path=args.config)
        except Exception as exc:
            logger.error("UI server failed: %s", exc)
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    parser.error(f"Unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
