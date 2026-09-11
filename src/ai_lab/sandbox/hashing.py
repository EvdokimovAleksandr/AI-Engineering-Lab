"""Deterministic hashes for computation identity. code_hash is never the whole identity."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ai_lab.knowledge.hashing import sha256_json, sha256_text
from ai_lab.sandbox.models import ComputeSpec, SandboxPolicy


def code_hash(code: str) -> str:
    return sha256_text(code)


def input_hash(inputs: dict[str, Any]) -> str:
    return sha256_json(inputs)


def stdout_hash(text: str) -> str:
    return sha256_text(text)


def stderr_hash(text: str) -> str:
    return sha256_text(text)


def python_version_fingerprint(explicit: str | None = None) -> str:
    """Record the interpreter that will actually run the worker."""
    if explicit:
        return explicit
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def dependency_fingerprint(*, package_version: str, pyproject_hash: str | None) -> str:
    """What is known about dependencies — not a pretend lockfile freeze."""
    payload: dict[str, Any] = {
        "package": "ai-engineering-lab",
        "package_version": package_version,
        "python_implementation": sys.implementation.name,
    }
    if pyproject_hash:
        payload["pyproject_dependencies_hash"] = pyproject_hash
    else:
        payload["lock"] = "unknown"
    return sha256_json(payload)


def environment_hash(
    *,
    python_version: str,
    platform_name: str,
    dependency_hash: str,
    runner_hash: str,
    image_digest: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "python_version": python_version,
        "platform": platform_name,
        "dependency_hash": dependency_hash,
        "runner_hash": runner_hash,
    }
    # Docker identity: ключ добавляем только когда backend его знает,
    # чтобы local hashes не сдвинулись.
    if image_digest is not None:
        payload["image_digest"] = image_digest
    return sha256_json(payload)


def policy_hash(policy: SandboxPolicy) -> str:
    payload: dict[str, Any] = {
        "version": policy.version,
        "backend": policy.backend,
        "timeout_s": policy.timeout_s,
        "memory_mb": policy.memory_mb,
        "cpu_time_s": policy.cpu_time_s,
        "network": policy.network.value,
        "filesystem": policy.filesystem.value,
        "max_stdout_bytes": policy.max_stdout_bytes,
        "max_stderr_bytes": policy.max_stderr_bytes,
        "max_processes": policy.max_processes,
        "kill_on_timeout": policy.kill_on_timeout,
    }
    # Docker image/cpus входят в identity только для docker backend.
    if policy.backend == "docker" and policy.docker is not None:
        payload["docker"] = {
            "image": policy.docker.image,
            "image_digest": policy.docker.image_digest,
            "read_only_root": policy.docker.read_only_root,
            "cap_drop_all": policy.docker.cap_drop_all,
            "no_new_privileges": policy.docker.no_new_privileges,
            "cpus": policy.docker.cpus,
            "non_root": policy.docker.non_root,
            "user": policy.docker.user,
            "network_mode": policy.docker.network_mode,
        }
    return sha256_json(payload)


def computation_hash(
    *,
    code_h: str,
    input_h: str,
    environment_h: str,
    policy_h: str,
    python_version: str,
) -> str:
    """Identity of a computation environment, not of a scientific result."""
    return sha256_json(
        {
            "code_hash": code_h,
            "input_hash": input_h,
            "environment_hash": environment_h,
            "policy_hash": policy_h,
            "python_version": python_version,
        }
    )


def hashes_for_spec(
    spec: ComputeSpec,
    policy: SandboxPolicy,
    *,
    python_version: str,
    platform_name: str,
    dependency_hash: str,
    runner_hash: str,
    image_digest: str | None = None,
) -> dict[str, str]:
    code_h = code_hash(spec.code)
    input_h = input_hash(spec.inputs)
    env_h = environment_hash(
        python_version=python_version,
        platform_name=platform_name,
        dependency_hash=dependency_hash,
        runner_hash=runner_hash,
        image_digest=image_digest,
    )
    pol_h = policy_hash(policy)
    return {
        "code_hash": code_h,
        "input_hash": input_h,
        "environment_hash": env_h,
        "policy_hash": pol_h,
        "computation_hash": computation_hash(
            code_h=code_h,
            input_h=input_h,
            environment_h=env_h,
            policy_h=pol_h,
            python_version=python_version,
        ),
        "python_version": python_version,
        "dependency_hash": dependency_hash,
        "runner_hash": runner_hash,
    }


def hash_pyproject_dependencies(repo_root: Path | None) -> str | None:
    """Hash only the declared project.dependencies list if pyproject.toml is present."""
    if repo_root is None:
        return None
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return sha256_text(text)


def package_version() -> str:
    try:
        from importlib.metadata import version

        return version("ai-engineering-lab")
    except Exception:
        return "unknown"
