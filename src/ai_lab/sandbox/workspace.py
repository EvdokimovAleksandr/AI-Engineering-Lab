"""Run-scoped sandbox workspace. Shared by local and Docker backends."""

from __future__ import annotations

import json
from pathlib import Path

from ai_lab.sandbox.errors import SandboxValidationError
from ai_lab.sandbox.models import ComputeSpec, SandboxContext


def prepare_workspace(context: SandboxContext) -> Path:
    """Создать `.runs/<run_id>/sandbox/<task_id>/`. Не project root и не user home."""
    parent = context.workspace_parent
    if parent is None:
        raise SandboxValidationError("SandboxContext.workspace_parent is required (run-scoped)")
    parent = Path(parent)
    task_part = context.task_id or "untasked"
    # Не даём task_id выйти из sandbox dir (path traversal).
    safe_task = Path(task_part).name.replace("..", "_")
    workspace = parent / safe_task
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".tmp").mkdir(exist_ok=True)
    return workspace.resolve()


def write_workspace_files(workspace: Path, spec: ComputeSpec) -> None:
    """Launcher пишет code/inputs/limits — не интерполяция в командную строку."""
    (workspace / "user_code.py").write_text(spec.code, encoding="utf-8")
    (workspace / "inputs.json").write_text(
        json.dumps(spec.inputs, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (workspace / "limits.json").write_text(
        json.dumps(
            {
                "memory_mb": spec.memory_mb,
                "cpu_time_s": spec.cpu_time_s,
                "max_processes": spec.max_processes,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
