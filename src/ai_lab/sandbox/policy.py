"""Trusted sandbox policy: config is the ceiling; ComputeSpec cannot escalate."""

from __future__ import annotations

import sys
from typing import Any

from ai_lab.core.enums import FilesystemPolicy, NetworkPolicy
from ai_lab.core.models import LabConfig
from ai_lab.sandbox.errors import SandboxSecurityPolicyError, SandboxValidationError
from ai_lab.sandbox.models import (
    FORBIDDEN_COMPUTE_KEYS,
    SANDBOX_POLICY_VERSION,
    ComputeSpec,
    DockerRuntimeConfig,
    SandboxPolicy,
)


def sandbox_policy_from_config(config: LabConfig) -> SandboxPolicy:
    """Load trusted ceilings from YAML. Unknown backends fail loud (no silent fallback)."""
    raw = dict(config.sandbox or {})
    timeout = raw.get("timeout_s", raw.get("timeout_seconds", 10))
    max_out = raw.get("max_output_bytes", 200_000)
    max_stdout = raw.get("max_stdout_bytes", max_out)
    max_stderr = raw.get("max_stderr_bytes", max_out)
    network_raw = str(raw.get("network", "deny")).lower()
    fs_raw = str(raw.get("filesystem", "run_scoped")).lower()
    backend = str(raw.get("backend", "local_subprocess"))
    if backend not in {"local_subprocess", "docker"}:
        raise SandboxValidationError(f"Unknown sandbox backend {backend!r}")
    fallback = str(raw.get("fallback_backend", "none") or "none").lower()
    if fallback != "none":
        raise SandboxValidationError(
            f"sandbox.fallback_backend={fallback!r} is not supported; automatic fallback is forbidden"
        )
    allowed = raw.get("allowed_modules") or []
    cpu = raw.get("cpu_time_s")
    docker_cfg = _docker_config_from_raw(raw.get("docker"))
    if backend == "docker" and docker_cfg is None:
        raise SandboxValidationError("sandbox.docker.image is required when backend=docker")
    return SandboxPolicy(
        backend=backend,
        fallback_backend="none",
        version=str(raw.get("policy_version") or SANDBOX_POLICY_VERSION),
        timeout_s=float(timeout),
        memory_mb=int(raw.get("memory_mb", 256)),
        cpu_time_s=float(cpu) if cpu is not None else None,
        network=NetworkPolicy(network_raw),
        filesystem=FilesystemPolicy(fs_raw),
        max_stdout_bytes=int(max_stdout),
        max_stderr_bytes=int(max_stderr),
        max_processes=int(raw.get("max_processes", 8)),
        kill_on_timeout=bool(raw.get("kill_on_timeout", True)),
        allowed_modules=[str(m) for m in allowed],
        docker=docker_cfg,
    )


def _docker_config_from_raw(raw: Any) -> DockerRuntimeConfig | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise SandboxValidationError("sandbox.docker must be a mapping")
    forbidden = {"privileged", "cap_add", "devices", "docker_args", "mounts", "entrypoint", "runtime"}
    bad = forbidden.intersection(raw)
    if bad:
        raise SandboxValidationError(f"sandbox.docker forbids keys: {sorted(bad)}")
    digest = raw.get("image_digest") or raw.get("digest")
    if digest is not None:
        digest = str(digest).strip() or None
    network_mode = str(raw.get("network", raw.get("network_mode", "none"))).lower()
    if network_mode in {"none", "deny"}:
        network_mode = "none"
    user = str(raw.get("user", "65534:65534"))
    return DockerRuntimeConfig(
        image=str(raw.get("image", "python:3.12-slim")),
        image_digest=digest,
        network_mode=network_mode,  # type: ignore[arg-type]
        read_only_root=bool(raw.get("read_only_root", True)),
        cap_drop_all=bool(raw.get("cap_drop_all", True)),
        no_new_privileges=bool(raw.get("no_new_privileges", True)),
        cpus=float(raw.get("cpus", 1.0)),
        non_root=bool(raw.get("non_root", True)),
        user=user,
    )


def reject_host_control_kwargs(kwargs: dict[str, Any]) -> None:
    """Tool callers must not pass cwd/shell/env mutation through python.execute."""
    bad = FORBIDDEN_COMPUTE_KEYS.intersection(kwargs)
    if bad:
        raise SandboxSecurityPolicyError(
            f"python.execute rejects host-control arguments: {sorted(bad)}"
        )


def bind_spec_to_policy(
    spec: ComputeSpec,
    policy: SandboxPolicy,
    *,
    requested_overrides: dict[str, Any] | None = None,
) -> ComputeSpec:
    """Apply trusted ceilings. Escalation is rejected, not silently clamped.

    `requested_overrides` are untrusted tool kwargs (timeout_s, network, …).
    """
    overrides = requested_overrides or {}
    reject_host_control_kwargs(overrides)

    timeout = spec.timeout_s
    memory = spec.memory_mb
    cpu = spec.cpu_time_s
    network = spec.network
    filesystem = spec.filesystem
    max_out = spec.max_stdout_bytes
    max_err = spec.max_stderr_bytes
    max_proc = spec.max_processes

    if "timeout_s" in overrides:
        timeout = float(overrides["timeout_s"])
    if "memory_mb" in overrides:
        memory = int(overrides["memory_mb"])
    if "cpu_time_s" in overrides and overrides["cpu_time_s"] is not None:
        cpu = float(overrides["cpu_time_s"])
    if "network" in overrides:
        network = NetworkPolicy(str(overrides["network"]).lower())
    if "filesystem" in overrides:
        filesystem = FilesystemPolicy(str(overrides["filesystem"]).lower())
    if "max_stdout_bytes" in overrides:
        max_out = int(overrides["max_stdout_bytes"])
    if "max_stderr_bytes" in overrides:
        max_err = int(overrides["max_stderr_bytes"])
    if "max_processes" in overrides:
        max_proc = int(overrides["max_processes"])

    _reject_escalation("timeout_s", timeout, policy.timeout_s)
    _reject_escalation("memory_mb", memory, policy.memory_mb)
    _reject_escalation("max_stdout_bytes", max_out, policy.max_stdout_bytes)
    _reject_escalation("max_stderr_bytes", max_err, policy.max_stderr_bytes)
    _reject_escalation("max_processes", max_proc, policy.max_processes)
    if cpu is not None and policy.cpu_time_s is not None:
        _reject_escalation("cpu_time_s", cpu, policy.cpu_time_s)
    elif cpu is not None and policy.cpu_time_s is None:
        # Политика не задала CPU ceiling — не даём LLM сам назначить «бесконечный» CPU
        # сверх timeout; cpu_time_s всё ещё advisory/job limit внутри timeout.
        pass

    if policy.network == NetworkPolicy.DENY and network != NetworkPolicy.DENY:
        raise SandboxSecurityPolicyError("network=allow rejected: trusted policy is deny")
    if filesystem != policy.filesystem:
        raise SandboxSecurityPolicyError(
            f"filesystem={filesystem.value} rejected: trusted policy is {policy.filesystem.value}"
        )
    if spec.working_dir_policy != "sandbox_workspace":
        raise SandboxSecurityPolicyError("working_dir_policy must be sandbox_workspace")

    if spec.python_version and policy.backend != "docker":
        current = f"{sys.version_info.major}.{sys.version_info.minor}"
        requested = spec.python_version.strip()
        if not (
            sys.version.startswith(requested)
            or current.startswith(requested)
            or requested.startswith(current)
        ):
            raise SandboxValidationError(
                f"python_version={requested!r} is not this interpreter ({sys.version.split()[0]}); "
                "LocalSubprocessSandbox does not switch runtimes"
            )

    return spec.model_copy(
        update={
            "timeout_s": timeout,
            "memory_mb": memory,
            "cpu_time_s": cpu if cpu is not None else policy.cpu_time_s,
            "network": network,
            "filesystem": filesystem,
            "max_stdout_bytes": max_out,
            "max_stderr_bytes": max_err,
            "max_processes": max_proc,
        }
    )


def spec_from_tool_args(
    *,
    code: str,
    policy: SandboxPolicy,
    inputs: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> ComputeSpec:
    """Build a ComputeSpec from python.execute kwargs, then bind to trusted policy."""
    extra = extra or {}
    reject_host_control_kwargs(extra)
    base = ComputeSpec(
        code=code,
        inputs=dict(inputs or {}),
        timeout_s=policy.timeout_s,
        memory_mb=policy.memory_mb,
        cpu_time_s=policy.cpu_time_s,
        network=policy.network,
        filesystem=policy.filesystem,
        max_stdout_bytes=policy.max_stdout_bytes,
        max_stderr_bytes=policy.max_stderr_bytes,
        max_processes=policy.max_processes,
        metadata={k: extra[k] for k in extra if k not in _TOOL_CONSUMED},
    )
    return bind_spec_to_policy(base, policy, requested_overrides=extra)


_TOOL_CONSUMED = frozenset(
    {
        "code",
        "inputs",
        "timeout_s",
        "timeout_seconds",
        "memory_mb",
        "cpu_time_s",
        "network",
        "filesystem",
        "max_stdout_bytes",
        "max_stderr_bytes",
        "max_processes",
        "task_id",
        "python_version",
        "metadata",
    }
)


def _reject_escalation(name: str, requested: float, ceiling: float) -> None:
    if requested > ceiling + 1e-12:
        raise SandboxSecurityPolicyError(
            f"{name}={requested} exceeds trusted policy ceiling {ceiling}"
        )
