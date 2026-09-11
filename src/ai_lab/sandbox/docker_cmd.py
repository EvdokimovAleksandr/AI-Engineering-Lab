"""Deterministic Docker CLI argv builder. No shell, no LLM-controlled flags."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ai_lab.sandbox.errors import SandboxSecurityPolicyError, SandboxValidationError
from ai_lab.sandbox.models import ComputeSpec, DockerRuntimeConfig, SandboxPolicy
from ai_lab.sandbox.paths import validate_bind_source, validate_runner_source

CONTAINER_WORKSPACE = "/workspace"
CONTAINER_RUNNER = "/opt/ai-lab/runner.py"
CONTAINER_TMP = "/tmp"

# Имена контейнеров: только безопасный charset Docker.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

# Image ref: registry/name:tag@sha256:hex — без shell metacharacters.
_IMAGE_UNSAFE = frozenset(" \t\n\r;|&$`<>(){}!\\\"'")

# Эти флаги никогда не должны появиться в argv, даже как часть user string.
FORBIDDEN_DOCKER_FLAGS = (
    "--privileged",
    "--pid=host",
    "--ipc=host",
    "--uts=host",
    "--userns=host",
    "--cgroupns=host",
    "--network=host",
    "--net=host",
    "--cap-add",
    "--device",
    "--add-host",
    "--security-opt=seccomp=unconfined",
    "--security-opt=apparmor=unconfined",
    "--volume",
    "--mount-type=volume",
)

_FORBIDDEN_ARGV_EXACT = frozenset(
    {
        "--privileged",
        "--pid=host",
        "--ipc=host",
        "--uts=host",
        "--userns=host",
        "--cgroupns=host",
        "--network=host",
        "--net=host",
        "-v",
        "--volume",
        "--cap-add",
        "--device",
        "--add-host",
        "--publish",
        "-p",
        "--expose",
        "--network-alias",
    }
)


@dataclass(frozen=True)
class DockerCommand:
    """Готовая argv-команда. sanitized_argv скрывает host paths для provenance."""

    argv: list[str]
    container_name: str
    image: str
    workspace_host: str
    runner_host: str
    sanitized_argv: list[str]


def make_container_name(run_id: str, task_id: str | None, nonce: str) -> str:
    """Имя из хеша, не из сырого user input — concurrent-safe и без injection."""
    raw = f"{run_id}\0{task_id or '-'}\0{nonce}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    name = f"ai-lab-{digest}"
    if not _NAME_RE.match(name):
        raise SandboxValidationError("generated container name is invalid")
    return name


def run_id_label(run_id: str) -> str:
    """Docker label value: короткий хеш run_id, без произвольной строки."""
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


def validate_image_ref(image: str) -> str:
    """Image задаётся trusted config. Отклоняем shell/flag injection."""
    if not image or not str(image).strip():
        raise SandboxValidationError("docker image must be non-empty")
    ref = str(image).strip()
    if any(c in ref for c in _IMAGE_UNSAFE):
        raise SandboxValidationError("docker image contains forbidden characters")
    if "--" in ref:
        raise SandboxValidationError("docker image must not contain '--'")
    if ref.startswith("-"):
        raise SandboxValidationError("docker image must not look like a CLI flag")
    return ref


def resolve_image_ref(image: str, image_digest: str | None) -> str:
    """Склеить tag + digest. Digest из config, не из ComputeSpec."""
    ref = validate_image_ref(image)
    if not image_digest:
        return ref
    digest = image_digest.strip()
    if digest.startswith("sha256:"):
        hexpart = digest[7:]
    else:
        hexpart = digest
        digest = f"sha256:{hexpart}"
    if len(hexpart) != 64 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
        raise SandboxValidationError("docker image_digest must be sha256:<64 hex>")
    name = ref.split("@", 1)[0]
    if "@" in ref:
        existing = ref.split("@", 1)[1]
        if existing != digest and existing != hexpart:
            raise SandboxValidationError("docker image @digest does not match image_digest")
        return f"{name}@{digest}"
    return f"{name}@{digest}"


def format_cpus(cpus: float) -> str:
    if cpus <= 0 or cpus > 64:
        raise SandboxValidationError(f"docker cpus={cpus} is out of range")
    # Фиксированный формат — не user string и не scientific notation.
    text = f"{cpus:.4f}".rstrip("0").rstrip(".")
    return text or "1"


def build_docker_command(
    *,
    policy: SandboxPolicy,
    spec: ComputeSpec,
    image: str,
    container_name: str,
    workspace_host: Path,
    workspace_root: Path,
    runner_host: Path,
    runner_expected: Path,
    run_id: str,
    docker_cfg: DockerRuntimeConfig | None = None,
    include_init: bool = True,
    include_pull_never: bool = True,
) -> DockerCommand:
    """Собрать `docker run ...` только из trusted fields. side-effect free."""
    cfg = docker_cfg or policy.docker
    if cfg is None:
        raise SandboxValidationError("sandbox.docker config is required for DockerSandbox")
    if not _NAME_RE.match(container_name):
        raise SandboxValidationError(f"invalid container name {container_name!r}")

    workspace = validate_bind_source(workspace_host, workspace_root)
    runner = validate_runner_source(runner_host, expected=runner_expected)
    image_ref = validate_image_ref(image)

    if spec.network.value != "deny" or cfg.network_mode != "none":
        raise SandboxSecurityPolicyError("DockerSandbox requires network deny / --network=none")

    argv: list[str] = ["docker", "run", "--name", container_name, "--rm"]
    if include_pull_never:
        argv.extend(["--pull", "never"])
    if include_init:
        argv.append("--init")
    argv.extend(["--network", "none"])
    if cfg.read_only_root:
        argv.append("--read-only")
    if cfg.cap_drop_all:
        argv.extend(["--cap-drop", "ALL"])
    if cfg.no_new_privileges:
        argv.extend(["--security-opt", "no-new-privileges"])
    argv.extend(["--memory", f"{int(spec.memory_mb)}m"])
    if cfg.cpus is not None:
        argv.extend(["--cpus", format_cpus(float(cfg.cpus))])
    argv.extend(["--pids-limit", str(int(spec.max_processes))])
    if cfg.non_root:
        argv.extend(["--user", str(cfg.user)])
    argv.extend(["--workdir", CONTAINER_WORKSPACE])
    argv.extend(["--tmpfs", f"{CONTAINER_TMP}:rw,nosuid,nodev,size=64m"])
    argv.extend(
        [
            "--mount",
            f"type=bind,source={workspace},target={CONTAINER_WORKSPACE}",
            "--mount",
            f"type=bind,source={runner},target={CONTAINER_RUNNER},readonly",
        ]
    )
    argv.extend(["--label", "ai-lab.sandbox=1", "--label", f"ai-lab.run_id={run_id_label(run_id)}"])
    for key, value in _container_env_pairs():
        argv.extend(["-e", f"{key}={value}"])
    argv.append(image_ref)
    # Без shell: python -I runner --workspace /workspace
    argv.extend(["python", "-I", CONTAINER_RUNNER, "--workspace", CONTAINER_WORKSPACE])

    assert_command_safe(argv)
    sanitized = _sanitize_argv(argv, workspace=str(workspace), runner=str(runner))
    return DockerCommand(
        argv=argv,
        container_name=container_name,
        image=image_ref,
        workspace_host=str(workspace),
        runner_host=str(runner),
        sanitized_argv=sanitized,
    )


def assert_command_safe(argv: list[str]) -> None:
    """Runtime guard: forbidden flags must never appear as separate tokens or glued values."""
    if not argv or argv[0] != "docker":
        raise SandboxSecurityPolicyError("docker command must start with docker argv[0]")
    if "shell" in argv or "-c" in argv:
        # docker run python -c would be a different model; we never pass -c.
        if "-c" in argv:
            raise SandboxSecurityPolicyError("docker command must not invoke a shell or python -c")
    for token in argv:
        if token in _FORBIDDEN_ARGV_EXACT:
            raise SandboxSecurityPolicyError(f"forbidden docker flag {token!r}")
        lower = token.lower()
        for bad in FORBIDDEN_DOCKER_FLAGS:
            if bad.lower() in lower and not _allowed_substring_exception(token, bad):
                raise SandboxSecurityPolicyError(f"forbidden docker flag fragment {bad!r} in {token!r}")
        if token.startswith("--cap-add"):
            raise SandboxSecurityPolicyError("cap-add is forbidden")
        if token.startswith("--device"):
            raise SandboxSecurityPolicyError("--device is forbidden")
        if "seccomp=unconfined" in lower or "apparmor=unconfined" in lower:
            raise SandboxSecurityPolicyError("unconfined security profile is forbidden")
    # --network must be exactly none
    for i, token in enumerate(argv):
        if token in {"--network", "--net"}:
            if i + 1 >= len(argv) or argv[i + 1] != "none":
                raise SandboxSecurityPolicyError("docker network must be none")
        if token.startswith("--network=") and token != "--network=none":
            raise SandboxSecurityPolicyError("docker network must be none")
        if token in {"--pid", "--ipc"}:
            raise SandboxSecurityPolicyError("host pid/ipc namespaces are forbidden")
    joined = " ".join(argv)
    if "docker.sock" in joined:
        raise SandboxSecurityPolicyError("docker.sock must not be mounted")


def _allowed_substring_exception(token: str, bad: str) -> bool:
    # `--network` contains `--net` but `--network none` is required; checked separately.
    if bad in {"--net=host", "--network=host"} and token in {"--network", "--net"}:
        return True
    return False


def _container_env_pairs() -> list[tuple[str, str]]:
    """Контейнерный env с нуля. Host secrets сюда не попадают."""
    return [
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONNOUSERSITE", "1"),
        ("PYTHONIOENCODING", "utf-8"),
        ("PYTHONSAFEPATH", "1"),
        ("AI_LAB_SANDBOX", "1"),
        ("AI_LAB_SANDBOX_BACKEND", "docker"),
        ("TEMP", CONTAINER_TMP),
        ("TMP", CONTAINER_TMP),
        ("TMPDIR", CONTAINER_TMP),
    ]


def _sanitize_argv(argv: list[str], *, workspace: str, runner: str) -> list[str]:
    out: list[str] = []
    for token in argv:
        if workspace and workspace in token:
            token = token.replace(workspace, "<workspace>")
        if runner and runner in token:
            token = token.replace(runner, "<runner>")
        out.append(token)
    return out
