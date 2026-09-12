"""python.execute — ToolRegistry entry that delegates to ComputeSandbox.

AST import whitelist is trusted tool policy (defense in depth), not the OS
security boundary. Isolation is the sandbox process: timeout kill, env
allowlist, run-scoped workspace, output caps.
"""

from __future__ import annotations

import ast
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from ai_lab.core.enums import SandboxStatus
from ai_lab.core.models import ComputationArtifact, RunEvent
from ai_lab.observability.logger import get_logger
from ai_lab.sandbox.errors import SandboxValidationError
from ai_lab.sandbox.factory import build_compute_sandbox
from ai_lab.sandbox.models import SandboxContext, SandboxPolicy
from ai_lab.sandbox.policy import reject_host_control_kwargs, spec_from_tool_args
from ai_lab.tools.base import ToolSpec

logger = get_logger(__name__)

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
    """Tool-policy violation (imports / forbidden names). Not SandboxStatus.FAIL."""


class SandboxSyntaxError(SandboxViolation):
    """LLM snippet is not valid Python. Not a security violation — no computation evidence."""

    def __init__(self, message: str, *, source: str = "") -> None:
        super().__init__(message)
        self.source = source


def coerce_sandbox_code(value: Any) -> str:
    """Normalize LLM sandbox snippets to module-level Python source.

    Live providers return a string, a list of lines, or ``{code|source|text}``.
    Common leading indent is stripped so the snippet parses at module scope.
    Mixed/unexpected indents are left unchanged and fail in ``ast.parse``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value
    elif isinstance(value, list):
        raw = "\n".join("" if x is None else str(x) for x in value)
    elif isinstance(value, dict):
        raw = str(value.get("code") or value.get("source") or value.get("text") or "")
    else:
        raw = str(value)
    return textwrap.dedent(raw.expandtabs(4)).strip()


def _syntax_preview(source: str, *, limit: int = 16) -> str:
    lines = source.splitlines() or [source]
    numbered = "\n".join(f"{i:3d}| {line}" for i, line in enumerate(lines[:limit], 1))
    extra = len(lines) - limit
    if extra > 0:
        numbered += f"\n... ({extra} more lines)"
    return numbered


def validate_imports(source: str, allowed_modules: set[str]) -> None:
    """Reject scripts that import modules outside the trusted whitelist."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        preview = _syntax_preview(source)
        raise SandboxSyntaxError(
            f"Syntax error in sandbox script: {exc}\n{preview}",
            source=source,
        ) from exc

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

    # open() разрешён: запись внутри workspace — политика filesystem, не AST.
    banned = {"exec", "eval", "__import__", "compile", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in banned:
            raise SandboxViolation(f"Forbidden name in sandbox script: {node.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned:
            raise SandboxViolation(f"Forbidden call in sandbox script: {node.func.id}")


class PythonExecTool:
    name = "python.execute"
    description = "Execute a short Python snippet in a process-isolated compute sandbox"

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 200_000,
        allowed_modules: set[str] | None = None,
        sandbox: Any = None,
        policy: SandboxPolicy | None = None,
        run_store: Any = None,
        sink: Any = None,
        run_id: str | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.allowed_modules = set(allowed_modules if allowed_modules is not None else DEFAULT_ALLOWED)
        self.policy = policy or SandboxPolicy(
            timeout_s=timeout_seconds,
            max_stdout_bytes=max_output_bytes,
            max_stderr_bytes=max_output_bytes,
            allowed_modules=sorted(self.allowed_modules),
        )
        self.sandbox = sandbox or build_compute_sandbox(self.policy, repo_root=repo_root)
        self.run_store = run_store
        self.sink = sink
        self.run_id = run_id
        self.repo_root = repo_root
        self._orphan_workspace: tempfile.TemporaryDirectory[str] | None = None

    def as_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            handler=self.run,
            sandbox_required=True,
            network="deny",
            filesystem="run_scoped",
            metadata={
                "sandbox_backend": getattr(self.sandbox, "name", "local_subprocess"),
                "sandbox_policy_version": self.policy.version,
            },
        )

    async def run(self, code: str = "", **kwargs: Any) -> dict[str, Any]:
        code = coerce_sandbox_code(code)
        if not code:
            raise ValueError("python.execute requires non-empty 'code'")
        reject_host_control_kwargs(kwargs)
        try:
            validate_imports(code, self.allowed_modules)
        except SandboxSyntaxError:
            logger.error("Sandbox script failed to parse:\n%s", code[:4000])
            raise

        inputs = kwargs.pop("inputs", None) or {}
        if not isinstance(inputs, dict):
            raise SandboxValidationError("python.execute inputs must be a dict")
        task_id = kwargs.pop("task_id", None)
        if "timeout_seconds" in kwargs and "timeout_s" not in kwargs:
            kwargs["timeout_s"] = kwargs.pop("timeout_seconds")
        else:
            kwargs.pop("timeout_seconds", None)

        spec = spec_from_tool_args(code=code, policy=self.policy, inputs=inputs, extra=kwargs)
        context = SandboxContext(
            run_id=self.run_id or (self.run_store.run_id if self.run_store is not None else "orphan"),
            task_id=str(task_id) if task_id else None,
            workspace_parent=self._workspace_parent(),
            tool_name=self.name,
        )
        reap = getattr(self.sandbox, "reap_stale_containers", None)
        if callable(reap):
            # Resume/crash: старый container не trusted state; новый invocation.
            reap(context.run_id)
        result = await self.sandbox.execute(spec, context)
        artifact = result.artifact
        saved = False
        if artifact is not None and self.run_store is not None:
            try:
                self.run_store.save_computation(artifact)
                saved = True
            except FileExistsError:
                logger.error("Computation artifact id collision: %s", artifact.artifact_id)
                raise

        if self.sink is not None:
            self._emit(context.run_id, task_id, result, artifact)

        if result.process_alive_after_return:
            logger.error(
                "Sandbox returned while worker pid still alive: pid=%s status=%s",
                result.resource_usage.pid,
                result.status.value,
            )

        payload: dict[str, Any] = {
            "returncode": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "sandbox_status": result.status.value,
            "duration_ms": result.duration_ms,
            "enforcement": result.enforcement,
            "security_metadata": result.security_metadata,
            "process_alive_after_return": result.process_alive_after_return,
            "artifact_saved": saved,
        }
        if artifact is not None:
            payload["artifact"] = artifact.model_dump(mode="json")
            payload["artifact_id"] = artifact.artifact_id
            payload["code_hash"] = artifact.code_hash
            payload["input_hash"] = artifact.input_hash
            payload["computation_hash"] = artifact.computation_hash
            payload["environment_hash"] = artifact.environment_hash
        return payload

    def _workspace_parent(self) -> Path:
        if self.run_store is not None:
            root = Path(self.run_store.project.root) / self.run_store.rel_root / "sandbox"
            root.mkdir(parents=True, exist_ok=True)
            return root
        if self._orphan_workspace is None:
            self._orphan_workspace = tempfile.TemporaryDirectory(prefix="ai_lab_sbx_")
        return Path(self._orphan_workspace.name)

    def _emit(self, run_id: str, task_id: Any, result: Any, artifact: ComputationArtifact | None) -> None:
        data: dict[str, Any] = {
            "sandbox_backend": getattr(self.sandbox, "name", None),
            "sandbox_policy_version": self.policy.version,
            "status": result.status.value,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "resource_limits": artifact.resource_limits if artifact else {},
            "code_hash": artifact.code_hash if artifact else None,
            "input_hash": artifact.input_hash if artifact else None,
            "computation_hash": artifact.computation_hash if artifact else None,
            "environment_hash": artifact.environment_hash if artifact else None,
            "image": artifact.image_ref if artifact else None,
            "image_digest": artifact.image_digest if artifact else None,
            "environment_reproducibility": artifact.environment_reproducibility if artifact else None,
            "sanitized_command": (artifact.metadata or {}).get("sanitized_command") if artifact else None,
        }
        event_status = "ok" if result.status == SandboxStatus.SUCCESS else result.status.value.lower()
        self.sink.emit(
            RunEvent(
                run_id=run_id,
                task_id=str(task_id) if task_id else None,
                tool_name=self.name,
                duration_ms=result.duration_ms,
                status=event_status,
                message="compute sandbox invocation",
                data=data,
            )
        )
