"""Honest capability reporting for the active sandbox backend/platform."""

from __future__ import annotations

import sys

from ai_lab.core.enums import EnforcementLevel
from ai_lab.sandbox.models import SandboxCapabilities, SandboxPolicy
from ai_lab.sandbox.windows_job import job_objects_available


def local_subprocess_capabilities(
    policy: SandboxPolicy | None = None,
    *,
    job_attached: bool | None = None,
) -> SandboxCapabilities:
    """Capabilities of LocalSubprocessSandbox on this OS.

    `job_attached` is used after a real spawn: if assignment failed, tree-kill
    / memory / CPU must not be reported as HARD for that invocation.
    """
    _ = policy
    win = sys.platform == "win32"
    jobs_ok = win and job_objects_available()
    if job_attached is False:
        jobs_ok = False

    if win:
        job_level = EnforcementLevel.HARD if jobs_ok else EnforcementLevel.UNSUPPORTED
        return SandboxCapabilities(
            backend="local_subprocess",
            platform=sys.platform,
            timeout=EnforcementLevel.HARD,
            process_kill=EnforcementLevel.HARD,
            process_tree_kill=job_level,
            network_deny=EnforcementLevel.UNSUPPORTED,
            filesystem_jail=EnforcementLevel.BEST_EFFORT,
            memory_limit=job_level,
            cpu_limit=job_level,
            output_limit=EnforcementLevel.HARD,
            environment_isolation=EnforcementLevel.HARD,
            max_processes=job_level,
            job_objects=job_level,
        )

    # POSIX: timeout/kill/pg are real; rlimits are applied in the runner when available.
    rlimit = _posix_rlimit_available()
    rlimit_level = EnforcementLevel.HARD if rlimit else EnforcementLevel.UNSUPPORTED
    return SandboxCapabilities(
        backend="local_subprocess",
        platform=sys.platform,
        timeout=EnforcementLevel.HARD,
        process_kill=EnforcementLevel.HARD,
        process_tree_kill=EnforcementLevel.HARD,
        network_deny=EnforcementLevel.UNSUPPORTED,
        filesystem_jail=EnforcementLevel.BEST_EFFORT,
        memory_limit=rlimit_level,
        cpu_limit=rlimit_level,
        output_limit=EnforcementLevel.HARD,
        environment_isolation=EnforcementLevel.HARD,
        max_processes=EnforcementLevel.UNSUPPORTED,
        job_objects=EnforcementLevel.UNSUPPORTED,
    )


def docker_capabilities(
    policy: SandboxPolicy | None = None,
    discovery: object | None = None,
) -> SandboxCapabilities:
    """Capabilities of DockerSandbox on this machine.

    HARD only if the daemon is up and the flags we actually pass are supported.
    Without a reachable daemon we cannot enforce isolation — report UNSUPPORTED.
    """
    from ai_lab.sandbox.docker_discover import DockerDiscovery, detect_docker_capabilities

    if isinstance(discovery, DockerDiscovery):
        disc = discovery
    else:
        cfg = policy.docker if policy is not None else None
        disc = detect_docker_capabilities(docker_cfg=cfg, check_image=False)

    can_run = bool(disc.daemon_available and disc.platform_supported)
    # Image probe провален — контейнер не стартует, HARD врать нельзя.
    if disc.image_ref and not disc.image_available:
        can_run = False
    flags = disc.flags_supported or {}

    def _flag(name: str) -> bool:
        return can_run and bool(flags.get(name, False))

    hard_if = EnforcementLevel.HARD if can_run else EnforcementLevel.UNSUPPORTED
    network = EnforcementLevel.HARD if _flag("--network") else EnforcementLevel.UNSUPPORTED
    fs = (
        EnforcementLevel.HARD
        if _flag("--read-only") and _flag("--mount")
        else EnforcementLevel.UNSUPPORTED
    )
    memory = EnforcementLevel.HARD if _flag("--memory") else EnforcementLevel.UNSUPPORTED
    cpu = EnforcementLevel.HARD if _flag("--cpus") else EnforcementLevel.UNSUPPORTED
    pids = EnforcementLevel.HARD if _flag("--pids-limit") else EnforcementLevel.UNSUPPORTED

    cfg = policy.docker if policy is not None else None
    if cfg is not None and can_run:
        if not cfg.read_only_root:
            fs = EnforcementLevel.UNSUPPORTED
        if cfg.network_mode != "none":
            network = EnforcementLevel.UNSUPPORTED
        if cfg.cpus is None:
            cpu = EnforcementLevel.UNSUPPORTED

    return SandboxCapabilities(
        backend="docker",
        platform=f"{sys.platform}/docker:{disc.os_type or 'n/a'}",
        timeout=hard_if,
        process_kill=hard_if,
        process_tree_kill=hard_if,
        network_deny=network,
        filesystem_jail=fs,
        memory_limit=memory,
        cpu_limit=cpu,
        output_limit=hard_if,
        environment_isolation=hard_if,
        max_processes=pids,
        job_objects=EnforcementLevel.UNSUPPORTED,
    )


def _posix_rlimit_available() -> bool:
    if sys.platform == "win32":
        return False
    try:
        import resource  # noqa: F401

        return True
    except Exception:
        return False
