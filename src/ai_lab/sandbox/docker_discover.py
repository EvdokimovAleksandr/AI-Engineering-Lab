"""Docker engine discovery. Binary presence is not sufficient."""

from __future__ import annotations

import shutil
import subprocess

from pydantic import BaseModel, Field

from ai_lab.observability.logger import get_logger
from ai_lab.sandbox.docker_cmd import resolve_image_ref, validate_image_ref
from ai_lab.sandbox.models import DockerRuntimeConfig

logger = get_logger(__name__)

# Флаги, которые DockerSandbox реально передаёт. Отсутствие → не HARD.
_REQUIRED_RUN_FLAGS = (
    "--network",
    "--read-only",
    "--cap-drop",
    "--security-opt",
    "--memory",
    "--cpus",
    "--pids-limit",
    "--user",
    "--tmpfs",
    "--mount",
    "--rm",
)


class DockerDiscovery(BaseModel):
    """Честный отчёт: installed ≠ daemon ≠ backend."""

    installed: bool = False
    docker_bin: str | None = None
    daemon_available: bool = False
    os_type: str | None = None
    image_available: bool = False
    image_ref: str | None = None
    image_digest: str | None = None
    python_version: str | None = None
    flags_supported: dict[str, bool] = Field(default_factory=dict)
    platform_supported: bool = False
    backend_available: bool = False
    reason: str = "not probed"


class DockerCommandRunner:
    """Адаптер Docker CLI. shell=False всегда. Не SDK."""

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("docker")

    def invoke(
        self,
        args: list[str],
        *,
        timeout: float = 15.0,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if not self.binary:
            raise FileNotFoundError("docker binary not found")
        cmd = [self.binary, *args]
        return subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )


def detect_docker_capabilities(
    *,
    docker_cfg: DockerRuntimeConfig | None = None,
    runner: DockerCommandRunner | None = None,
    check_image: bool = True,
) -> DockerDiscovery:
    """Проверить binary, daemon, Linux containers, flags, optional image. Без pull."""
    r = runner or DockerCommandRunner()
    if not r.binary:
        return DockerDiscovery(installed=False, reason="docker binary not found")
    help_text = ""
    try:
        help_proc = r.invoke(["run", "--help"], timeout=8.0)
        help_text = (help_proc.stdout or "") + (help_proc.stderr or "")
    except FileNotFoundError:
        return DockerDiscovery(installed=False, reason="docker binary not found")
    except subprocess.TimeoutExpired:
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            reason="docker run --help timed out",
        )
    except OSError as exc:
        logger.error("docker run --help failed: %s", exc)
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            reason=f"docker CLI not executable: {exc}",
        )

    flags = {flag: flag in help_text for flag in _REQUIRED_RUN_FLAGS}
    flags["--pull"] = "--pull" in help_text
    flags["--init"] = "--init" in help_text

    try:
        info = r.invoke(["info", "--format", "{{.ServerVersion}}|{{.OSType}}"], timeout=8.0)
    except subprocess.TimeoutExpired:
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            daemon_available=False,
            flags_supported=flags,
            reason="docker daemon not reachable (docker info timed out)",
        )
    except OSError as exc:
        logger.error("docker info failed: %s", exc)
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            daemon_available=False,
            flags_supported=flags,
            reason=f"docker info failed: {exc}",
        )

    if info.returncode != 0:
        err = (info.stderr or info.stdout or "docker info failed").strip()
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            daemon_available=False,
            flags_supported=flags,
            reason=err[:500] or "docker daemon unavailable",
        )

    server, _, os_type = (info.stdout or "").strip().partition("|")
    os_type = (os_type or "").strip().lower() or None
    platform_ok = os_type in {None, "", "linux"}
    # Docker Desktop on Windows reports OSType=linux for Linux containers.
    if os_type and os_type != "linux":
        platform_ok = False
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            daemon_available=True,
            os_type=os_type,
            flags_supported=flags,
            platform_supported=False,
            reason=f"Docker OSType={os_type!r} is not linux containers",
        )

    missing_flags = [f for f, ok in flags.items() if f in _REQUIRED_RUN_FLAGS and not ok]
    if missing_flags:
        return DockerDiscovery(
            installed=True,
            docker_bin=r.binary,
            daemon_available=True,
            os_type=os_type,
            flags_supported=flags,
            platform_supported=platform_ok,
            reason=f"docker run flags unsupported: {missing_flags}",
        )

    image_ref = None
    image_available = False
    image_digest = None
    python_version = None
    reason = "docker daemon available"
    if check_image and docker_cfg is not None:
        try:
            image_ref = resolve_image_ref(docker_cfg.image, docker_cfg.image_digest)
            inspect = _inspect_image(r, image_ref)
            if inspect is None:
                return DockerDiscovery(
                    installed=True,
                    docker_bin=r.binary,
                    daemon_available=True,
                    os_type=os_type,
                    image_ref=image_ref,
                    image_available=False,
                    flags_supported=flags,
                    platform_supported=True,
                    reason=f"image not available locally (no docker pull): {image_ref}",
                )
            image_available = True
            image_digest = inspect.get("digest")
            python_version = inspect.get("python_version")
        except Exception as exc:
            logger.error("docker image inspect failed: %s", exc)
            return DockerDiscovery(
                installed=True,
                docker_bin=r.binary,
                daemon_available=True,
                os_type=os_type,
                flags_supported=flags,
                platform_supported=True,
                reason=str(exc),
            )
    elif not check_image:
        reason = "docker daemon available (image not probed)"

    backend_ok = True
    return DockerDiscovery(
        installed=True,
        docker_bin=r.binary,
        daemon_available=True,
        os_type=os_type,
        image_available=image_available if docker_cfg is not None and check_image else False,
        image_ref=image_ref,
        image_digest=image_digest,
        python_version=python_version,
        flags_supported=flags,
        platform_supported=True,
        backend_available=backend_ok
        and (image_available if (check_image and docker_cfg is not None) else True),
        reason=reason,
    )


def inspect_local_image(image: str, *, runner: DockerCommandRunner | None = None) -> dict[str, str | None]:
    """Inspect only. Never docker pull."""
    r = runner or DockerCommandRunner()
    ref = validate_image_ref(image)
    found = _inspect_image(r, ref)
    if found is None:
        return {}
    return found


def _inspect_image(runner: DockerCommandRunner, image: str) -> dict[str, str | None] | None:
    proc = runner.invoke(
        ["image", "inspect", "-f", "{{.Id}}|{{json .RepoDigests}}|{{json .Config.Env}}", image],
        timeout=15.0,
    )
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip()
    image_id, _, rest = line.partition("|")
    digests_json, _, env_json = rest.partition("|")
    digest = _digest_from_inspect(image_id.strip(), digests_json)
    py = _python_version_from_env_json(env_json)
    return {"id": image_id.strip() or None, "digest": digest, "python_version": py}


def _digest_from_inspect(image_id: str, digests_json: str) -> str | None:
    # RepoDigests: ["python@sha256:abc..."] — сильнее чем tag. .Id всегда локальный sha256.
    import json

    try:
        digests = json.loads(digests_json) if digests_json else []
    except json.JSONDecodeError:
        digests = []
    if isinstance(digests, list):
        for item in digests:
            if isinstance(item, str) and "@sha256:" in item:
                return item.split("@", 1)[1]
    if image_id.startswith("sha256:"):
        return image_id
    return image_id or None


def _python_version_from_env_json(env_json: str) -> str | None:
    import json

    try:
        env_list = json.loads(env_json) if env_json else []
    except json.JSONDecodeError:
        return None
    if not isinstance(env_list, list):
        return None
    for item in env_list:
        if isinstance(item, str) and item.startswith("PYTHON_VERSION="):
            return item.split("=", 1)[1]
    return None


def local_docker_host_allowed(docker_host: str | None) -> bool:
    """Remote TCP daemon — не поддерживается."""
    if not docker_host:
        return True
    host = docker_host.strip().lower()
    if host.startswith("npipe:") or host.startswith("unix:"):
        return True
    if host.startswith("tcp:") or host.startswith("http:") or host.startswith("ssh:"):
        return False
    return True
