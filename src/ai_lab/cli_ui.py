"""CLI: python -m ai_lab ui [--demo]"""

from __future__ import annotations

import shutil
from pathlib import Path

from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.runtime import repo_root_from_here
from ai_lab.ui.http import serve

logger = get_logger(__name__)


def _seed_demo_project(repo_root: Path) -> None:
    """Ensure a local demo project exists for UI demos (mock-friendly heater).

    Does not overwrite an existing problem.md — only creates if missing.
    """
    src = repo_root / "benchmarks" / "simple_heater"
    dst = repo_root / "projects" / "demo_simple_heater"
    if not src.is_dir():
        logger.error("Demo benchmark simple_heater not found at %s", src)
        raise FileNotFoundError(f"Missing demo source: {src}")
    if dst.exists() and (dst / "problem.md").is_file():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("problem.md", "requirements.md", "assumptions.md", "final_report.md"):
        s = src / name
        if s.is_file():
            shutil.copy2(s, dst / name)
        else:
            (dst / name).write_text(f"# {name}\n\n(demo stub)\n", encoding="utf-8")
    meta = dst / "project_meta.json"
    if not meta.is_file():
        meta.write_text(
            '{\n  "project_id": "demo_simple_heater",\n  "title": "Demo: Simple Heater",\n'
            '  "domain": "thermal",\n  "created_at": null\n}\n',
            encoding="utf-8",
        )
    logger.info("Demo project ready at %s", dst)


def run_ui_cli(
    *,
    host: str,
    port: int,
    config_path: Path | None = None,
    demo: bool = False,
) -> int:
    # config_path reserved so the CLI shape matches other subcommands; UI cannot override sandbox.
    del config_path
    root = repo_root_from_here()
    if demo:
        _seed_demo_project(root)
    serve(host=host, port=port, repo_root=root, demo_mode=demo)
    return 0
