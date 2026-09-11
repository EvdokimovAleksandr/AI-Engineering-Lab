"""Project-scoped file read/write tools."""

from __future__ import annotations

from typing import Any

from ai_lab.memory.project_store import ProjectStore
from ai_lab.tools.base import ToolSpec


class FilesTool:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def read_spec(self) -> ToolSpec:
        return ToolSpec(
            name="files.read",
            description="Read a text file relative to the project root",
            handler=self.read,
        )

    def write_spec(self) -> ToolSpec:
        return ToolSpec(
            name="files.write",
            description="Write a text file relative to the project root",
            handler=self.write,
        )

    async def read(self, path: str = "", **_: Any) -> dict[str, Any]:
        if not path:
            raise ValueError("files.read requires 'path'")
        content = self.store.read_text(path)
        return {"path": path, "content": content}

    async def write(self, path: str = "", content: str = "", **_: Any) -> dict[str, Any]:
        if not path:
            raise ValueError("files.write requires 'path'")
        rel = self.store.write_text(path, content)
        return {"path": rel, "bytes": len(content.encode("utf-8"))}
