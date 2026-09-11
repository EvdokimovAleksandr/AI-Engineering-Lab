"""Load YAML lab configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_lab.core.models import LabConfig


def load_config(path: Path | None = None) -> LabConfig:
    """Load config/default.yaml relative to repo root unless path given."""
    if path is None:
        # src/ai_lab/config_loader.py → parents: ai_lab, src, repo
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "config" / "default.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"Config root must be a mapping: {path}")
    return LabConfig.model_validate(raw)
