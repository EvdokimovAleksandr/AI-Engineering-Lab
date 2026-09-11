"""Project memory: on-disk layout under projects/<name>/."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import ProjectSnapshot

# Canonical subdirectories for every lab project
PROJECT_SUBDIRS = (
    "hypotheses",
    "research",
    "calculations",
    "simulations",
    "experiments",
    "designs",
    "reviews",
    "decisions",
)

STATE_FILENAME = "project_state.json"


class ProjectStore:
    """File-backed project root with path sandboxing."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Project root does not exist: {self.root}")
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @classmethod
    def open(cls, projects_dir: Path, name: str) -> ProjectStore:
        path = (projects_dir / name).resolve()
        return cls(path)

    def ensure_layout(self) -> None:
        """Create expected artifact directories if missing."""
        for sub in PROJECT_SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        for required in ("problem.md", "requirements.md", "assumptions.md", "final_report.md"):
            target = self.root / required
            if not target.exists():
                target.write_text(f"# {required}\n\n_TODO_\n", encoding="utf-8")

    @property
    def name(self) -> str:
        return self.root.name

    def _lock_for(self, rel: str) -> threading.Lock:
        with self._locks_guard:
            if rel not in self._locks:
                self._locks[rel] = threading.Lock()
            return self._locks[rel]

    def resolve(self, relative: str) -> Path:
        """Resolve a path relative to project root; reject escapes."""
        if relative.startswith("/") or relative.startswith("\\") or ".." in Path(relative).parts:
            raise ValueError(f"Illegal project-relative path: {relative!r}")
        full = (self.root / relative).resolve()
        if not str(full).startswith(str(self.root)):
            raise ValueError(f"Path escapes project root: {relative!r}")
        return full

    def read_text(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(f"Missing file: {relative}")
        return path.read_text(encoding="utf-8")

    def write_text(self, relative: str, content: str) -> str:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._lock_for(relative)
        with lock:
            path.write_text(content, encoding="utf-8")
        return relative

    def write_json(self, relative: str, data: Any) -> str:
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        return self.write_text(relative, payload + "\n")

    def read_json(self, relative: str) -> Any:
        return json.loads(self.read_text(relative))

    def load_snapshot(self) -> ProjectSnapshot:
        state_path = self.root / STATE_FILENAME
        if not state_path.exists():
            return ProjectSnapshot(project_name=self.name, state=ProjectState.CREATED)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        return ProjectSnapshot.model_validate(raw)

    def save_snapshot(self, snapshot: ProjectSnapshot) -> None:
        self.write_json(STATE_FILENAME, snapshot.model_dump(mode="json"))

    def list_artifacts(self, subdirectory: str) -> list[str]:
        folder = self.resolve(subdirectory)
        if not folder.is_dir():
            return []
        return sorted(
            str(p.relative_to(self.root)).replace("\\", "/")
            for p in folder.rglob("*")
            if p.is_file() and p.name != ".gitkeep"
        )
