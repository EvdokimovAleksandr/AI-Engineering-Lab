"""Structured sandbox errors. Distinct from CheckStatus / ResearchError."""

from __future__ import annotations


class SandboxError(Exception):
    """Base error for ComputeSandbox. Not a scientific FAIL."""


class SandboxValidationError(SandboxError, ValueError):
    """ComputeSpec failed deterministic validation (invalid or policy escalation)."""


class SandboxSpawnError(SandboxError):
    """OS could not start the worker process."""


class SandboxTimeoutError(SandboxError, TimeoutError):
    """Wall-clock timeout. Prefer SandboxStatus.TIMEOUT on the result over raising."""


class SandboxResourceLimitError(SandboxError):
    """Memory / CPU / process-count limit was hit (when the platform enforces it)."""


class SandboxSecurityPolicyError(SandboxError):
    """Trusted policy rejected a requested capability (network, cwd, env, …)."""


class SandboxOutputLimitError(SandboxError):
    """stdout/stderr exceeded the configured cap."""


class SandboxProcessError(SandboxError):
    """Worker crashed or exited in an unexpected way."""


class SandboxUnsupportedError(SandboxError):
    """Requested backend or hard limit is not implemented on this platform."""


class DockerImageUnavailable(SandboxUnsupportedError):
    """Configured image is not present locally. Sandbox never docker pull'ит произвольный image."""
