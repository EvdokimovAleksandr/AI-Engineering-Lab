"""Typed artifact save/load helpers exposed as tools."""

from __future__ import annotations

from typing import Any

from ai_lab.memory.project_store import ProjectStore
from ai_lab.tools.base import ToolSpec


class ArtifactsTool:
    """Persist arbitrary JSON/markdown artifacts under project subfolders."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def save_spec(self) -> ToolSpec:
        return ToolSpec(
            name="artifacts.save",
            description="Save a JSON or text artifact under the project",
            handler=self.save,
        )

    def load_spec(self) -> ToolSpec:
        return ToolSpec(
            name="artifacts.load",
            description="Load an artifact by relative path",
            handler=self.load,
        )

    async def save(
        self,
        path: str = "",
        data: Any = None,
        text: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not path:
            raise ValueError("artifacts.save requires 'path'")
        if text is not None:
            rel = self.store.write_text(path, text)
        elif data is not None:
            rel = self.store.write_json(path, data)
        else:
            raise ValueError("artifacts.save requires 'data' or 'text'")
        return {"path": rel}

    async def load(self, path: str = "", **_: Any) -> dict[str, Any]:
        if not path:
            raise ValueError("artifacts.load requires 'path'")
        if path.endswith(".json"):
            return {"path": path, "data": self.store.read_json(path)}
        return {"path": path, "text": self.store.read_text(path)}
