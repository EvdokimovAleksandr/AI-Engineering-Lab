"""DockerSandbox — OS/container isolation backend for ComputeSandbox.

Optional: Docker Engine is not a project dependency. Missing engine →
SandboxUnsupportedError. No silent fallback to LocalSubprocessSandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ai_lab.core.enums import SandboxStatus
from ai_lab.core.models import ComputationArtifact
from ai_lab.observability.logger import get_logger
from ai_lab.sandbox.capabilities import docker_capabilities
from ai_lab.sandbox.docker_cmd import (
    build_docker_command,
    make_container_name,
    resolve_image_ref,
    run_id_label,
)
from ai_lab.sandbox.docker_discover import (
    DockerCommandRunner,
    DockerDiscovery,
    detect_docker_capabilities,
    inspect_local_image,
    local_docker_host_allowed,
)
from ai_lab.sandbox.env import build_docker_cli_env, build_docker_container_extra_env
from ai_lab.sandbox.errors import (
    DockerImageUnavailable,
    SandboxSpawnError,
    SandboxUnsupportedError,
    SandboxValidationError,
)
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
from ai_lab.sandbox.supervise import close_subprocess_streams, supervise_process, wait_killed
from ai_lab.sandbox.windows_job import pid_is_alive
from ai_lab.sandbox.workspace import prepare_workspace, write_workspace_files

logger = get_logger(__name__)

TRUSTED_RUNNER = Path(__file__).resolve().parent / "runner.py"
RUNNER_HASH = hashlib.sha256(TRUSTED_RUNNER.read_bytes()).hexdigest() if TRUSTED_RUNNER.is_file() else "unknown"


class DockerSandbox:
    """Container-isolated compute. Docker daemon/kernel remain TCB — not '100% secure'."""

    name = "docker"

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        repo_root: Path | None = None,
        runner: DockerCommandRunner | None = None,
        discovery: DockerDiscovery | None = None,
    ) -> None:
        self.policy = policy
        self.repo_root = repo_root
        self._cli = runner or DockerCommandRunner()
        self._discovery_override = discovery

    def capabilities(self) -> SandboxCapabilities:
        return docker_capabilities(self.policy, self._probe(check_image=self.policy.docker is not None))

    def require_available(self) -> DockerDiscovery:
        """FAIL LOUD if Docker was requested but cannot actually isolate."""
        host = os.environ.get("DOCKER_HOST")
        if host and not local_docker_host_allowed(host):
            raise SandboxUnsupportedError(f"remote Docker daemon is not supported: DOCKER_HOST={host!r}")
        disc = self._probe(check_image=True)
        if not disc.installed:
            raise SandboxUnsupportedError(f"DockerSandbox unavailable: {disc.reason}")
        if not disc.daemon_available:
            raise SandboxUnsupportedError(f"Docker daemon unavailable: {disc.reason}")
        if not disc.platform_supported:
            raise SandboxUnsupportedError(f"Docker platform unsupported: {disc.reason}")
        if self.policy.docker is None:
            raise SandboxValidationError("sandbox.docker.image is required when backend=docker")
        if not disc.image_available:
            raise DockerImageUnavailable(f"Docker image unavailable (no automatic pull): {disc.reason}")
        if not disc.backend_available:
            raise SandboxUnsupportedError(f"DockerSandbox unavailable: {disc.reason}")
        return disc

    def reap_stale_containers(self, run_id: str) -> None:
        """Удалить контейнеры этого run_id и exited ai-lab контейнеры. Чужие run не трогаем."""
        if not self._cli.binary:
            return
        try:
            cli_env = build_docker_cli_env()
        except ValueError as exc:
            logger.error("Docker CLI env rejected during reap: %s", exc)
            return
        label = run_id_label(run_id)
        try:
            mine = self._cli.invoke(
                ["ps", "-aq", "--filter", f"label=ai-lab.run_id={label}"],
                timeout=10.0,
                env=cli_env,
            )
            ids = [line.strip() for line in (mine.stdout or "").splitlines() if line.strip()]
            exited = self._cli.invoke(
                ["ps", "-aq", "--filter", "label=ai-lab.sandbox=1", "--filter", "status=exited"],
                timeout=10.0,
                env=cli_env,
            )
            ids.extend(line.strip() for line in (exited.stdout or "").splitlines() if line.strip())
            unique = list(dict.fromkeys(ids))
            if unique:
                rm = self._cli.invoke(["rm", "-f", *unique], timeout=20.0, env=cli_env)
                if rm.returncode != 0:
                    logger.error("stale container rm failed: %s", (rm.stderr or "").strip())
        except Exception as exc:
            logger.error("reap_stale_containers failed: %s", exc)

    async def execute(self, spec: ComputeSpec, context: SandboxContext) -> SandboxResult:
        if not context.run_id:
            raise SandboxValidationError("SandboxContext.run_id is required")
        disc = self.require_available()
        cfg = self.policy.docker
        if cfg is None:
            raise SandboxValidationError("sandbox.docker config is required")
        bound = bind_spec_to_policy(spec, self.policy)
        workspace = prepare_workspace(context)
        write_workspace_files(workspace, bound)
        _chmod_workspace_for_nonroot(workspace)

        self.reap_stale_containers(context.run_id)

        image_ref = resolve_image_ref(cfg.image, cfg.image_digest)
        inspected = inspect_local_image(image_ref, runner=self._cli)
        if not inspected:
            raise DockerImageUnavailable(
                f"Docker image not available locally (no automatic pull): {image_ref}"
            )
        local_id = inspected.get("id") or image_ref
        digest = inspected.get("digest") or cfg.image_digest
        py_ver = inspected.get("python_version") or disc.python_version or "unknown"
        env_repro = "full" if digest and not str(digest).endswith("unknown") else "partial"
        if not digest:
            env_repro = "partial"
            digest = None

        nonce = uuid.uuid4().hex[:12]
        container_name = make_container_name(context.run_id, context.task_id, nonce)
        flags = disc.flags_supported or {}
        plan = build_docker_command(
            policy=self.policy,
            spec=bound,
            image=local_id,
            container_name=container_name,
            workspace_host=workspace,
            workspace_root=Path(context.workspace_parent),  # type: ignore[arg-type]
            runner_host=TRUSTED_RUNNER,
            runner_expected=TRUSTED_RUNNER,
            run_id=context.run_id,
            docker_cfg=cfg,
            include_init=bool(flags.get("--init", True)),
            include_pull_never=bool(flags.get("--pull", True)),
        )
        extra_e = build_docker_container_extra_env(context.extra_env or None)
        argv = list(plan.argv)
        if extra_e:
            argv = _inject_env_flags(argv, extra_e)

        try:
            cli_env = build_docker_cli_env()
        except ValueError as exc:
            raise SandboxUnsupportedError(str(exc)) from exc

        dep_hash = dependency_fingerprint(
            package_version=package_version(),
            pyproject_hash=hash_pyproject_dependencies(self.repo_root),
        )
        id_hashes = hashes_for_spec(
            bound,
            self.policy,
            python_version=py_ver,
            platform_name=f"docker:{disc.os_type or 'linux'}",
            dependency_hash=dep_hash,
            runner_hash=RUNNER_HASH,
            image_digest=digest or "unknown",
        )

        caps = docker_capabilities(self.policy, disc)
        enforcement = caps.as_enforcement_map()

        started = datetime.now(timezone.utc)
        t0 = time.monotonic()
        proc = None
        pid: int | None = None
        timed_out = False
        output_exceeded = False
        stdout_b = b""
        stderr_b = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=cli_env,
            )
            pid = proc.pid
        except OSError as exc:
            logger.error("Docker CLI spawn failed: %s", exc)
            raise SandboxSpawnError(f"Failed to spawn docker CLI: {exc}") from exc

        try:
            stdout_b, stderr_b, timed_out, output_exceeded = await supervise_process(
                proc,
                timeout_s=bound.timeout_s,
                max_stdout=bound.max_stdout_bytes,
                max_stderr=bound.max_stderr_bytes,
            )
        finally:
            await _ensure_container_dead(
                proc,
                cli=self._cli,
                container_name=container_name,
                cli_env=cli_env,
                kill=timed_out or output_exceeded or (proc is not None and proc.returncode is None),
            )
            close_subprocess_streams(proc)
            _force_rm(self._cli, container_name, cli_env)

        duration_ms = (time.monotonic() - t0) * 1000.0
        finished = datetime.now(timezone.utc)
        exit_code = proc.returncode if proc is not None else None
        leftover = _container_running(self._cli, container_name, cli_env)
        alive = pid_is_alive(pid) or leftover
        if leftover:
            logger.error("Docker container still running after return: %s", container_name)
            _force_rm(self._cli, container_name, cli_env)
            leftover = _container_running(self._cli, container_name, cli_env)
            alive = pid_is_alive(pid) or leftover

        stderr = stderr_b.decode("utf-8", errors="replace")
        stdout = stdout_b.decode("utf-8", errors="replace")
        status = _status_from(
            timed_out=timed_out,
            output_exceeded=output_exceeded,
            exit_code=exit_code,
            stderr_text=stderr,
        )

        artifact = ComputationArtifact(
            run_id=context.run_id,
            project_id=context.project_id,
            investigation_id=context.investigation_id,
            task_id=context.task_id,
            contract_version=context.contract_version,
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
                "container_name": container_name,
                "sanitized_command": plan.sanitized_argv,
            },
            environment_hash=id_hashes["environment_hash"],
            python_version=py_ver,
            dependency_hash=id_hashes["dependency_hash"],
            sandbox_backend=self.name,
            sandbox_policy_version=self.policy.version,
            resource_limits={
                "timeout_s": bound.timeout_s,
                "memory_mb": bound.memory_mb,
                "cpu_time_s": bound.cpu_time_s,
                "cpus": cfg.cpus,
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
            image_ref=cfg.image,
            image_digest=digest,
            environment_reproducibility=env_repro,
            determinism="unknown",
        )

        return SandboxResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            resource_usage=ResourceUsage(duration_ms=duration_ms, pid=pid),
            artifact=artifact,
            security_metadata={
                "backend": self.name,
                "policy_version": self.policy.version,
                "network_policy": bound.network.value,
                "network_enforcement": enforcement["network_deny"],
                "filesystem_policy": bound.filesystem.value,
                "filesystem_enforcement": enforcement["filesystem_jail"],
                "workspace": str(workspace),
                "code_hash": id_hashes["code_hash"],
                "input_hash": id_hashes["input_hash"],
                "computation_hash": id_hashes["computation_hash"],
                "environment_hash": id_hashes["environment_hash"],
                "image_ref": cfg.image,
                "image_digest": digest,
                "environment_reproducibility": env_repro,
                "sanitized_command": plan.sanitized_argv,
                "container_reaped": not leftover,
            },
            enforcement=enforcement,
            process_alive_after_return=alive,
        )

    def _probe(self, *, check_image: bool) -> DockerDiscovery:
        if self._discovery_override is not None:
            return self._discovery_override
        return detect_docker_capabilities(
            docker_cfg=self.policy.docker,
            runner=self._cli,
            check_image=check_image,
        )


def _inject_env_flags(argv: list[str], extra: dict[str, str]) -> list[str]:
    """Вставить дополнительные -e перед image. extra уже allowlisted."""
    # image — последний токен до python -I ...
    try:
        py_idx = argv.index("python")
    except ValueError:
        raise SandboxValidationError("docker command missing python interpreter token") from None
    injected: list[str] = []
    for key, val in extra.items():
        if "\n" in key or "\n" in val or "=" in key:
            raise SandboxValidationError("invalid extra_env for docker")
        injected.extend(["-e", f"{key}={val}"])
    return argv[:py_idx] + injected + argv[py_idx:]


def _chmod_workspace_for_nonroot(workspace: Path) -> None:
    """Numeric uid 65534 должен писать в bind-mount (Linux VM / native)."""
    try:
        workspace.chmod(workspace.stat().st_mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IXOTH | stat.S_IROTH)
        for child in workspace.rglob("*"):
            mode = child.stat().st_mode
            if child.is_dir():
                child.chmod(mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IXOTH | stat.S_IROTH)
            else:
                child.chmod(mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH)
    except OSError as exc:
        logger.error("chmod workspace for non-root failed: %s", exc)


async def _ensure_container_dead(
    proc: asyncio.subprocess.Process | None,
    *,
    cli: DockerCommandRunner,
    container_name: str,
    cli_env: dict[str, str],
    kill: bool,
) -> None:
    if proc is None:
        return
    if not kill:
        if proc.returncode is None:
            await proc.wait()
        return
    try:
        stop = cli.invoke(["kill", container_name], timeout=10.0, env=cli_env)
        if stop.returncode != 0:
            logger.error("docker kill %s: %s", container_name, (stop.stderr or "").strip())
    except Exception as exc:
        logger.error("docker kill failed: %s", exc)
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except OSError as exc:
            logger.error("docker CLI proc.kill failed: %s", exc)
    await wait_killed(proc)


def _force_rm(cli: DockerCommandRunner, container_name: str, cli_env: dict[str, str]) -> None:
    try:
        rm = cli.invoke(["rm", "-f", container_name], timeout=10.0, env=cli_env)
        if rm.returncode != 0 and "No such container" not in (rm.stderr or "") and "No such container" not in (rm.stdout or ""):
            logger.error("docker rm -f %s: %s", container_name, (rm.stderr or "").strip())
    except Exception as exc:
        logger.error("docker rm failed: %s", exc)


def _container_running(cli: DockerCommandRunner, container_name: str, cli_env: dict[str, str]) -> bool:
    try:
        ps = cli.invoke(
            ["ps", "-q", "--filter", f"name={container_name}"],
            timeout=8.0,
            env=cli_env,
        )
        return bool((ps.stdout or "").strip())
    except Exception as exc:
        logger.error("docker ps after return failed: %s", exc)
        return False


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
    if exit_code in (137, 139):
        if "memoryerror" in blob or exit_code == 137:
            return SandboxStatus.MEMORY_LIMIT
    if exit_code not in (0, None):
        if "filesystem_denied" in blob:
            return SandboxStatus.FILESYSTEM_DENIED
        if "memoryerror" in blob or "cannot allocate memory" in blob:
            return SandboxStatus.MEMORY_LIMIT
        if exit_code in (125, 126, 127):
            return SandboxStatus.PROCESS_ERROR
        return SandboxStatus.NONZERO_EXIT
    return SandboxStatus.SUCCESS
