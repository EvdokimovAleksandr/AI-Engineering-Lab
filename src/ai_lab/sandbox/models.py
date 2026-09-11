"""Contracts for general-purpose compute. Not VerificationSpec / DeterministicVerifier."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_lab.core.enums import EnforcementLevel, FilesystemPolicy, NetworkPolicy, SandboxStatus
from ai_lab.core.models import ComputationArtifact

# Запрещённые ключи: LLM/tool kwargs не могут протаскивать host control.
FORBIDDEN_COMPUTE_KEYS = frozenset(
    {
        "cwd",
        "host_path",
        "shell",
        "command",
        "docker_args",
        "environment_mutation",
        "network_proxy",
        "env",
        "environment",
        "image",
        "docker_image",
        "runtime",
        "entrypoint",
        "mounts",
        "devices",
        "capabilities",
        "privileged",
        "cap_add",
        "pid_mode",
        "ipc_mode",
        "network_mode",
        "security_opt",
        "container_name",
        "docker_host",
    }
)

SANDBOX_POLICY_VERSION = "v2.4c-1"


class ComputeSpec(BaseModel):
    """What to compute and which limits apply — not a host command.

    Extra keys are forbidden so callers cannot smuggle cwd / shell / docker_args.
    Policy validator still rejects escalation above trusted config.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = Field(default=10.0, gt=0.0, le=3600.0)
    memory_mb: int = Field(default=256, gt=0, le=65536)
    cpu_time_s: float | None = Field(default=None, gt=0.0, le=3600.0)
    network: NetworkPolicy = NetworkPolicy.DENY
    filesystem: FilesystemPolicy = FilesystemPolicy.RUN_SCOPED
    python_version: str | None = None
    working_dir_policy: Literal["sandbox_workspace"] = "sandbox_workspace"
    max_stdout_bytes: int = Field(default=200_000, gt=0, le=50_000_000)
    max_stderr_bytes: int = Field(default=200_000, gt=0, le=50_000_000)
    max_processes: int = Field(default=8, gt=0, le=256)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _code_non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("ComputeSpec.code must be non-empty")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_no_host_control(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = FORBIDDEN_COMPUTE_KEYS.intersection(v)
        if bad:
            raise ValueError(f"ComputeSpec.metadata must not contain host-control keys: {sorted(bad)}")
        return v


class ResourceUsage(BaseModel):
    duration_ms: float | None = None
    peak_memory_bytes: int | None = None
    cpu_time_s: float | None = None
    pid: int | None = None
    child_pids: list[int] = Field(default_factory=list)


class SandboxCapabilities(BaseModel):
    """Honest platform/backend capability report. Never optimistic."""

    backend: str
    platform: str
    timeout: EnforcementLevel
    process_kill: EnforcementLevel
    process_tree_kill: EnforcementLevel
    network_deny: EnforcementLevel
    filesystem_jail: EnforcementLevel
    memory_limit: EnforcementLevel
    cpu_limit: EnforcementLevel
    output_limit: EnforcementLevel
    environment_isolation: EnforcementLevel
    max_processes: EnforcementLevel
    job_objects: EnforcementLevel = EnforcementLevel.UNSUPPORTED

    def as_enforcement_map(self) -> dict[str, str]:
        return {
            "timeout": self.timeout.value,
            "process_kill": self.process_kill.value,
            "process_tree_kill": self.process_tree_kill.value,
            "network_deny": self.network_deny.value,
            "filesystem_jail": self.filesystem_jail.value,
            "memory_limit": self.memory_limit.value,
            "cpu_limit": self.cpu_limit.value,
            "output_limit": self.output_limit.value,
            "environment_isolation": self.environment_isolation.value,
            "max_processes": self.max_processes.value,
            "job_objects": self.job_objects.value,
        }


class DockerRuntimeConfig(BaseModel):
    """Trusted Docker engine settings. Not accepted from ComputeSpec / LLM proposal.

    memory / pids / timeout живут в SandboxPolicy / ComputeSpec — не дублируем их здесь.
    """

    model_config = ConfigDict(extra="forbid")

    image: str = "python:3.12-slim"
    image_digest: str | None = None
    network_mode: Literal["none"] = "none"
    read_only_root: bool = True
    cap_drop_all: bool = True
    no_new_privileges: bool = True
    cpus: float = Field(default=1.0, gt=0.0, le=64.0)
    non_root: bool = True
    user: str = "65534:65534"


class SandboxPolicy(BaseModel):
    """Trusted configuration ceiling. ComputeSpec may only request values at or below this."""

    model_config = ConfigDict(extra="forbid")

    backend: str = "local_subprocess"
    fallback_backend: Literal["none"] = "none"
    version: str = SANDBOX_POLICY_VERSION
    timeout_s: float = Field(default=10.0, gt=0.0)
    memory_mb: int = Field(default=256, gt=0)
    cpu_time_s: float | None = Field(default=None, gt=0.0)
    network: NetworkPolicy = NetworkPolicy.DENY
    filesystem: FilesystemPolicy = FilesystemPolicy.RUN_SCOPED
    max_stdout_bytes: int = Field(default=200_000, gt=0)
    max_stderr_bytes: int = Field(default=200_000, gt=0)
    max_processes: int = Field(default=8, gt=0)
    kill_on_timeout: bool = True
    allowed_modules: list[str] = Field(default_factory=list)
    docker: DockerRuntimeConfig | None = None


class SandboxResult(BaseModel):
    """Structured sandbox outcome. stdout/stderr are already capped."""

    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: float = 0.0
    resource_usage: ResourceUsage = Field(default_factory=ResourceUsage)
    artifact: ComputationArtifact | None = None
    security_metadata: dict[str, Any] = Field(default_factory=dict)
    enforcement: dict[str, str] = Field(default_factory=dict)
    process_alive_after_return: bool = False


@dataclass
class SandboxContext:
    """Runtime identity for one invocation. Does not grant host cwd / env mutation."""

    run_id: str
    task_id: str | None = None
    # Родитель каталога workspace: .runs/<run_id>/sandbox/ — не project root.
    workspace_parent: Path | None = None
    tool_name: str = "python.execute"
    extra_env: dict[str, str] = field(default_factory=dict)
