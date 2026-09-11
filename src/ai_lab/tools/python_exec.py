"""Sandboxed Python execution via subprocess + AST import whitelist."""

from __future__ import annotations

import ast
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ai_lab.observability.logger import get_logger
from ai_lab.tools.base import ToolSpec

logger = get_logger(__name__)

# Builtins / stdlib-ish names that do not require import statements still OK in code body.
DEFAULT_ALLOWED = frozenset(
    {
        "math",
        "cmath",
        "statistics",
        "decimal",
        "fractions",
        "json",
        "re",
        "typing",
        "dataclasses",
        "collections",
        "itertools",
        "functools",
        "operator",
    }
)


class SandboxViolation(ValueError):
    pass


def validate_imports(source: str, allowed_modules: set[str]) -> None:
    """Reject scripts that import modules outside the whitelist."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SandboxViolation(f"Syntax error in sandbox script: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed_modules:
                    raise SandboxViolation(f"Import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                raise SandboxViolation("Relative imports are not allowed")
            root = node.module.split(".")[0]
            if root not in allowed_modules:
                raise SandboxViolation(f"Import not allowed: {node.module}")
        elif isinstance(node, ast.Attribute):
            # Block __import__ style patterns on bare names is hard; ban dunder calls lightly.
            pass

    # Ban exec/eval/open/__import__ by name
    banned = {"exec", "eval", "open", "__import__", "compile", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in banned:
            raise SandboxViolation(f"Forbidden name in sandbox script: {node.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned:
            raise SandboxViolation(f"Forbidden call in sandbox script: {node.func.id}")


class PythonExecTool:
    name = "python.execute"
    description = "Execute a short Python snippet in a restricted subprocess sandbox"

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 200_000,
        allowed_modules: set[str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.allowed_modules = set(allowed_modules or DEFAULT_ALLOWED)

    def as_spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, handler=self.run)

    async def run(self, code: str = "", **_: Any) -> dict[str, Any]:
        if not code or not code.strip():
            raise ValueError("python.execute requires non-empty 'code'")
        validate_imports(code, self.allowed_modules)

        with tempfile.TemporaryDirectory(prefix="ai_lab_sbx_") as tmp:
            script_path = Path(tmp) / "snippet.py"
            script_path.write_text(code, encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": "",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
            # Intentionally no network-oriented env vars / secrets
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",  # isolated
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp,
                env=env,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error("Sandbox timeout after %ss", self.timeout_seconds)
                raise TimeoutError(f"Sandbox exceeded {self.timeout_seconds}s")

            stdout = stdout_b[: self.max_output_bytes].decode("utf-8", errors="replace")
            stderr = stderr_b[: self.max_output_bytes].decode("utf-8", errors="replace")
            return {
                "returncode": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
            }
