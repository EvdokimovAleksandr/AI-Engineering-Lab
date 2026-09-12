"""LocalSubprocessSandbox — killable Python worker process.

Not a fully secure container. Guarantees: process isolation, wall-clock timeout
with real process kill, allowlisted env, run-scoped workspace, parent-side
stdout/stderr caps. Network/memory/CPU/filesystem jail are platform-dependent
and reported honestly via EnforcementLevel.
"""

from __future__ import annotations

import asyncio
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_lab.core.enums import SandboxStatus
from ai_lab.core.models import ComputationArtifact
from ai_lab.observability.logger import get_logger
from ai_lab.sandbox.capabilities import local_subprocess_capabilities
from ai_lab.sandbox.env import build_sandbox_env
from ai_lab.sandbox.errors import SandboxSpawnError, SandboxValidationError
from ai_lab.sandbox.hashing import (
    dependency_fingerprint,
    hash_pyproject_dependencies,
    hashes_for_spec,
    package_version,
    stdout_hash,
)
from ai_lab.sandbox.models import (
    ComputeSpec,
    ResourceUsage,
    SandboxCapabilities,
    SandboxContext,
    SandboxPolicy,
    SandboxResult,
)
from ai_lab.sandbox.policy import bind_spec_to_policy
from ai_lab.sandbox.supervise import close_subprocess_streams, kill_local_process, supervise_process, wait_killed
from ai_lab.sandbox.windows_job import (
    WindowsJob,
    _windows_child_pids,
    kill_process_tree,
    pid_is_alive,
    terminate_pid,
)
from ai_lab.sandbox.workspace import prepare_workspace, write_workspace_files

logger = get_logger(__name__)

RUNNER_PATH = Path(__file__).resolve().parent / "runner.py"
RUNNER_HASH = __import__("hashlib").sha256(RUNNER_PATH.read_bytes()).hexdigest() if RUNNER_PATH.is_file() else "unknown"


class LocalSubprocessSandbox:
    name = "local_subprocess"

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        repo_root: Path | None = None,
    ) -> None:
        self.policy = policy
        self.repo_root = repo_root

    def capabilities(self) -> SandboxCapabilities:
        return local_subprocess_capabilities(self.policy)

    async def execute(self, spec: ComputeSpec, context: SandboxContext) -> SandboxResult:
        if not context.run_id:
            raise SandboxValidationError("SandboxContext.run_id is required")
        bound = bind_spec_to_policy(spec, self.policy)
        workspace = prepare_workspace(context)
        write_workspace_files(workspace, bound)

        env = build_sandbox_env(workspace, extra=context.extra_env or None)
        # Командная строка собрана runtime из констант — не из user code.
        cmd = [sys.executable, "-I", str(RUNNER_PATH), "--workspace", str(workspace)]

        caps = self.capabilities()
        python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        dep_hash = dependency_fingerprint(
            package_version=package_version(),
            pyproject_hash=hash_pyproject_dependencies(self.repo_root),
        )
        id_hashes = hashes_for_spec(
            bound,
            self.policy,
            python_version=python_ver,
            platform_name=platform.platform(),
            dependency_hash=dep_hash,
            runner_hash=RUNNER_HASH,
        )

        job: WindowsJob | None = None
        if sys.platform == "win32":
            job = WindowsJob.try_create(
                memory_bytes=int(bound.memory_mb) * 1024 * 1024,
                cpu_time_s=bound.cpu_time_s,
                max_processes=bound.max_processes,
            )

        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        proc = None
        pid: int | None = None
        job_attached = False
        spawn_kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            spawn_kwargs["start_new_session"] = True
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
                env=env,
                **spawn_kwargs,
            )
            pid = proc.pid
            if job is not None and pid:
                job_attached = job.assign(pid)
                if not job_attached:
                    logger.error("Job Object created but not attached; pid=%s", pid)
        except OSError as exc:
            logger.error("Sandbox spawn failed: %s", exc)
            raise SandboxSpawnError(f"Failed to spawn sandbox process: {exc}") from exc

        timed_out = False
        output_exceeded = False
        stdout_b = b""
        stderr_b = b""
        try:
            stdout_b, stderr_b, timed_out, output_exceeded = await supervise_process(
                proc,
                timeout_s=bound.timeout_s,
                max_stdout=bound.max_stdout_bytes,
                max_stderr=bound.max_stderr_bytes,
            )
        finally:
            await _ensure_dead(proc, job=job, kill=timed_out or output_exceeded)
            close_subprocess_streams(proc)

        duration_ms = (time.monotonic() - t0) * 1000.0
        finished = datetime.now(timezone.utc)
        peak = job.query_peak_memory() if job is not None and job_attached else None
        if job is not None:
            job.close()

        alive = pid_is_alive(pid)
        if alive:
            logger.error("Sandbox worker still alive after return: pid=%s", pid)

        caps_actual = local_subprocess_capabilities(self.policy, job_attached=job_attached if sys.platform == "win32" else None)
        enforcement = caps_actual.as_enforcement_map()

        exit_code = proc.returncode
        status = _status_from(
            timed_out=timed_out,
            output_exceeded=output_exceeded,
            exit_code=exit_code,
            stderr_text=stderr_b.decode("utf-8", errors="replace"),
        )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        artifact = ComputationArtifact(
            run_id=context.run_id,
            kind="calculation",
            input_hash=id_hashes["input_hash"],
            code_hash=id_hashes["code_hash"],
            tool_version="python.execute",
            started_at=started,
            finished_at=finished,
            status=status.value,
            code=bound.code,
            stdout=stdout,
            stderr=stderr,
            returncode=exit_code,
            result={"sandbox_status": status.value},
            metadata={
                "task_id": context.task_id,
                "tool_name": context.tool_name,
                "policy_hash": id_hashes["policy_hash"],
                "runner_hash": id_hashes["runner_hash"],
            },
            environment_hash=id_hashes["environment_hash"],
            python_version=python_ver,
            dependency_hash=id_hashes["dependency_hash"],
            sandbox_backend=self.name,
            sandbox_policy_version=self.policy.version,
            resource_limits={
                "timeout_s": bound.timeout_s,
                "memory_mb": bound.memory_mb,
                "cpu_time_s": bound.cpu_time_s,
                "max_stdout_bytes": bound.max_stdout_bytes,
                "max_stderr_bytes": bound.max_stderr_bytes,
                "max_processes": bound.max_processes,
                "network": bound.network.value,
                "filesystem": bound.filesystem.value,
            },
            stdout_hash=stdout_hash(stdout),
            stderr_hash=stdout_hash(stderr),
            computation_hash=id_hashes["computation_hash"],
            duration_ms=duration_ms,
            sandbox_status=status.value,
            enforcement=enforcement,
            workspace=str(workspace),
        )

        return SandboxResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            resource_usage=ResourceUsage(
                duration_ms=duration_ms,
                peak_memory_bytes=peak,
                pid=pid,
            ),
            artifact=artifact,
            security_metadata={
                "backend": self.name,
                "policy_version": self.policy.version,
                "network_policy": bound.network.value,
                "network_enforcement": enforcement["network_deny"],
                "filesystem_policy": bound.filesystem.value,
                "filesystem_enforcement": enforcement["filesystem_jail"],
                "job_object_attached": job_attached,
                "workspace": str(workspace),
                "code_hash": id_hashes["code_hash"],
                "input_hash": id_hashes["input_hash"],
                "computation_hash": id_hashes["computation_hash"],
                "environment_hash": id_hashes["environment_hash"],
            },
            enforcement=enforcement,
            process_alive_after_return=alive,
        )


async def _ensure_dead(
    proc: asyncio.subprocess.Process | None,
    *,
    job: WindowsJob | None,
    kill: bool,
) -> None:
    if proc is None:
        return
    # Успешное завершение: только дождаться exit, не TerminateJobObject.
    if not kill:
        if proc.returncode is None:
            await proc.wait()
        return
    # Snapshot descendants *before* TerminateJobObject: on Windows the grandchild
    # may survive the job kill (breakaway / nested host job), and after the parent
    # dies Toolhelp no longer links orphans to the dead root pid.
    known_children: list[int] = []
    if sys.platform == "win32" and proc.pid:
        known_children = _windows_child_pids(int(proc.pid))
    if job is not None and job.assigned:
        job.terminate(1)
    for child_pid in known_children:
        if pid_is_alive(child_pid):
            logger.error(
                "Sandbox descendant still alive after Job terminate; killing pid=%s",
                child_pid,
            )
            terminate_pid(child_pid)
    if proc.pid:
        kill_process_tree(proc.pid)
    kill_local_process(proc)
    await wait_killed(proc)
    # Settle: process-table lag after TerminateProcess.
    if proc.pid:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and (
            pid_is_alive(proc.pid) or any(pid_is_alive(c) for c in known_children)
        ):
            for child_pid in known_children:
                if pid_is_alive(child_pid):
                    terminate_pid(child_pid)
            if pid_is_alive(proc.pid):
                kill_local_process(proc)
            await asyncio.sleep(0.05)
        if pid_is_alive(proc.pid):
            logger.error(
                "Sandbox parent still alive after terminate+tree-kill+poll: pid=%s",
                proc.pid,
            )
        still = [c for c in known_children if pid_is_alive(c)]
        if still:
            logger.error(
                "Sandbox descendants still alive after kill attempts: %s",
                still,
            )


def _status_from(
    *,
    timed_out: bool,
    output_exceeded: bool,
    exit_code: int | None,
    stderr_text: str,
) -> SandboxStatus:
    if timed_out:
        return SandboxStatus.TIMEOUT
    if output_exceeded:
        return SandboxStatus.OUTPUT_LIMIT
    blob = (stderr_text or "").lower()
    if exit_code not in (0, None):
        if "filesystem_denied" in blob:
            return SandboxStatus.FILESYSTEM_DENIED
        # Job memory / CPU на Windows часто даёт STATUS_INTEGER / access / killed
        if "memoryerror" in blob:
            return SandboxStatus.MEMORY_LIMIT
        return SandboxStatus.NONZERO_EXIT
    return SandboxStatus.SUCCESS
