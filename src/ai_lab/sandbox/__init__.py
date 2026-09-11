"""Reproducible compute sandbox (V2.4c).

General-purpose Python runs here. DeterministicVerifier stays in checks/.
Backends: LocalSubprocessSandbox (Windows-first) and DockerSandbox (optional).
"""

from ai_lab.sandbox.compare import ReproductionReport, compare_computations
from ai_lab.sandbox.docker import DockerSandbox
from ai_lab.sandbox.errors import (
    DockerImageUnavailable,
    SandboxError,
    SandboxOutputLimitError,
    SandboxProcessError,
    SandboxResourceLimitError,
    SandboxSecurityPolicyError,
    SandboxSpawnError,
    SandboxTimeoutError,
    SandboxUnsupportedError,
    SandboxValidationError,
)
from ai_lab.sandbox.factory import build_compute_sandbox, sandbox_from_config
from ai_lab.sandbox.local import LocalSubprocessSandbox
from ai_lab.sandbox.models import ComputeSpec, SandboxCapabilities, SandboxContext, SandboxPolicy, SandboxResult
from ai_lab.sandbox.protocol import ComputeSandbox

__all__ = [
    "ComputeSandbox",
    "ComputeSpec",
    "DockerImageUnavailable",
    "DockerSandbox",
    "LocalSubprocessSandbox",
    "ReproductionReport",
    "SandboxCapabilities",
    "SandboxContext",
    "SandboxError",
    "SandboxOutputLimitError",
    "SandboxPolicy",
    "SandboxProcessError",
    "SandboxResourceLimitError",
    "SandboxResult",
    "SandboxSecurityPolicyError",
    "SandboxSpawnError",
    "SandboxTimeoutError",
    "SandboxUnsupportedError",
    "SandboxValidationError",
    "build_compute_sandbox",
    "compare_computations",
    "sandbox_from_config",
]
