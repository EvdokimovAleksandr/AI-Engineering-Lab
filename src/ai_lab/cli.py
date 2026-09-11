"""CLI entrypoint: python -m ai_lab run <project> --provider mock."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.runtime import run_project

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
        choices=["mock", "cursor_sdk"],
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

    parser.error(f"Unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
