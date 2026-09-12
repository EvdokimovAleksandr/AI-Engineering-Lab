"""CLI: python -m ai_lab ui [--demo] [--config path]"""

from __future__ import annotations

import shutil
from pathlib import Path

from ai_lab.config_loader import load_config
from ai_lab.core.models import LabConfig
from ai_lab.observability.logger import get_logger
from ai_lab.orchestrator.runtime import repo_root_from_here
from ai_lab.ui.http import serve

logger = get_logger(__name__)


def _resolve_config_path(repo_root: Path, config_path: Path | None) -> Path:
    """Trusted operator YAML only — never taken from the browser payload."""
    if config_path is None:
        return repo_root / "config" / "default.yaml"
    path = config_path.expanduser()
    if not path.is_absolute():
        path = (repo_root / path).resolve()
    else:
        path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path}")
    return path


def _require_provider_ready(cfg: LabConfig) -> None:
    """Fail at UI startup if the trusted provider cannot actually run."""
    if cfg.provider != "cursor_sdk":
        return
    from ai_lab.llm.cursor_sdk import CursorSDKProvider

    model = cfg.models.get("chief_engineer") or next(iter(cfg.models.values()), "composer-2.5")
    CursorSDKProvider(default_model=model, reasoning_only=True)


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
    root = repo_root_from_here()
    cfg = load_config(_resolve_config_path(root, config_path))
    _require_provider_ready(cfg)
    if demo:
        _seed_demo_project(root)
    serve(host=host, port=port, repo_root=root, demo_mode=demo, lab_config=cfg)
    return 0
