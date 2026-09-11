"""CLI: python -m ai_lab ui"""

from __future__ import annotations

from pathlib import Path

from ai_lab.orchestrator.runtime import repo_root_from_here
from ai_lab.ui.http import serve


def run_ui_cli(*, host: str, port: int, config_path: Path | None = None) -> int:
    # config_path reserved so the CLI shape matches other subcommands; UI cannot override sandbox.
    del config_path
    serve(host=host, port=port, repo_root=repo_root_from_here())
    return 0
