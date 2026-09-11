"""CLI helper for `python -m ai_lab sandbox` — capability report, no execution."""

from __future__ import annotations

import sys
from pathlib import Path

from ai_lab.config_loader import load_config
from ai_lab.core.enums import EnforcementLevel
from ai_lab.llm.config import apply_provider_override
from ai_lab.sandbox.factory import report_all_backends
from ai_lab.sandbox.policy import sandbox_policy_from_config


def run_sandbox_cli(*, config_path: Path | None, provider: str | None = None) -> int:
    config = load_config(config_path)
    if provider:
        config = apply_provider_override(config, provider)
    policy = sandbox_policy_from_config(config)
    report = report_all_backends(config)
    selected = str(report["selected"])
    local = report["local_subprocess"]
    docker = report["docker"]
    disc = report["docker_discovery"]

    print(f"Selected backend: {selected}")
    print(f"Platform: {sys.platform}")
    print(f"Policy version: {policy.version}")
    print()
    _print_backend("local_subprocess", local)
    print()
    _print_backend("docker", docker)
    print()
    print("Docker:")
    print(f"  installed: {_yes(getattr(disc, 'installed', False))}")
    print(f"  daemon: {'available' if getattr(disc, 'daemon_available', False) else 'unavailable'}")
    print(f"  backend: {'available' if getattr(disc, 'backend_available', False) else 'unavailable'}")
    print(f"  os_type: {getattr(disc, 'os_type', None) or 'n/a'}")
    image_ref = getattr(disc, "image_ref", None)
    if image_ref:
        print(f"  image: {image_ref}")
        print(f"  image_available: {_yes(getattr(disc, 'image_available', False))}")
        print(f"  image_digest: {getattr(disc, 'image_digest', None) or 'unknown'}")
    print(f"  reason: {getattr(disc, 'reason', '')}")
    print()
    print("Requested policy:")
    print(f"  network={policy.network.value}")
    print(f"  filesystem={policy.filesystem.value}")
    print(f"  timeout_s={policy.timeout_s}")
    print(f"  memory_mb={policy.memory_mb}")
    print(f"  cpu_time_s={policy.cpu_time_s}")
    print(f"  kill_on_timeout={policy.kill_on_timeout}")
    print(f"  fallback_backend={policy.fallback_backend}")
    print()
    print("LocalSubprocessSandbox is process-isolated compute with enforced timeout")
    print("and a scoped workspace. It is not a fully secure container.")
    print("Network/memory/CPU isolation are platform-dependent (see enforcement above).")
    print()
    print("DockerSandbox is container-isolated compute with Docker-enforced network,")
    print("resource and filesystem boundaries. Docker daemon, host kernel and")
    print("container runtime remain the trusted computing base.")
    if selected == "docker" and not getattr(disc, "backend_available", False):
        print()
        print("WARNING: backend=docker is selected but Docker is unavailable — execution will fail loud.")
    return 0


def _print_backend(name: str, caps: object) -> None:
    print(f"Backend: {name}")
    print(f"  Timeout: {_lvl(caps, 'timeout')}")
    print(f"  Process kill: {_lvl(caps, 'process_kill')}")
    print(f"  Process tree kill: {_lvl(caps, 'process_tree_kill')}")
    print(f"  Filesystem isolation: {_lvl(caps, 'filesystem_jail')}")
    print(f"  Network isolation: {_lvl(caps, 'network_deny')}")
    print(f"  Memory limit: {_lvl(caps, 'memory_limit')}")
    print(f"  CPU limit: {_lvl(caps, 'cpu_limit')}")
    print(f"  Output cap: {_lvl(caps, 'output_limit')}")
    print(f"  Environment isolation: {_lvl(caps, 'environment_isolation')}")
    print(f"  Max processes: {_lvl(caps, 'max_processes')}")
    print(f"  Job objects: {_lvl(caps, 'job_objects')}")


def _lvl(caps: object, field: str) -> str:
    val = getattr(caps, field, None)
    if isinstance(val, EnforcementLevel):
        return val.value
    return str(val)


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"
