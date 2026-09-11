"""Build the configured ComputeSandbox. Unknown backend fails — no silent local fallback."""

from __future__ import annotations

from pathlib import Path

from ai_lab.core.models import LabConfig
from ai_lab.sandbox.capabilities import docker_capabilities, local_subprocess_capabilities
from ai_lab.sandbox.docker import DockerSandbox
from ai_lab.sandbox.docker_discover import detect_docker_capabilities
from ai_lab.sandbox.errors import SandboxValidationError
from ai_lab.sandbox.local import LocalSubprocessSandbox
from ai_lab.sandbox.models import SandboxCapabilities, SandboxPolicy
from ai_lab.sandbox.policy import sandbox_policy_from_config
from ai_lab.sandbox.protocol import ComputeSandbox


def build_compute_sandbox(
    policy: SandboxPolicy,
    *,
    repo_root: Path | None = None,
    require_available: bool = True,
) -> ComputeSandbox:
    if policy.backend == "local_subprocess":
        return LocalSubprocessSandbox(policy, repo_root=repo_root)
    if policy.backend == "docker":
        if policy.fallback_backend != "none":
            raise SandboxValidationError(
                f"sandbox.fallback_backend={policy.fallback_backend!r} is forbidden; "
                "requested backend != available backend must fail loud"
            )
        sandbox = DockerSandbox(policy, repo_root=repo_root)
        if require_available:
            sandbox.require_available()
        return sandbox
    raise SandboxValidationError(f"Unknown sandbox backend {policy.backend!r}")


def sandbox_from_config(config: LabConfig, *, repo_root: Path | None = None) -> tuple[SandboxPolicy, ComputeSandbox]:
    policy = sandbox_policy_from_config(config)
    return policy, build_compute_sandbox(policy, repo_root=repo_root, require_available=True)


def report_capabilities(config: LabConfig) -> SandboxCapabilities:
    """Capabilities of the *selected* backend. CLI also prints a comparison of both."""
    policy = sandbox_policy_from_config(config)
    sandbox = build_compute_sandbox(policy, require_available=False)
    return sandbox.capabilities()


def report_all_backends(config: LabConfig) -> dict[str, object]:
    """Honest comparison: selected backend + local + docker discovery."""
    policy = sandbox_policy_from_config(config)
    docker_disc = detect_docker_capabilities(docker_cfg=policy.docker, check_image=bool(policy.docker))
    return {
        "selected": policy.backend,
        "policy_version": policy.version,
        "local_subprocess": local_subprocess_capabilities(policy),
        "docker": docker_capabilities(policy, docker_disc),
        "docker_discovery": docker_disc,
    }


def local_capabilities(policy: SandboxPolicy | None = None) -> SandboxCapabilities:
    return local_subprocess_capabilities(policy)
