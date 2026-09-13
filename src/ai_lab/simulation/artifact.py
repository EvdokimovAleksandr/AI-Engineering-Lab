"""In-process ComputationArtifact for algebraic solvers (same RunStore as sandbox)."""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from typing import Any

from ai_lab.core.models import ComputationArtifact, Quantity
from ai_lab.knowledge.hashing import sha256_json
from ai_lab.sandbox.hashing import (
    code_hash,
    computation_hash,
    dependency_fingerprint,
    environment_hash,
    input_hash,
    package_version,
    python_version_fingerprint,
)
from ai_lab.simulation.models import SimulationSpec

SOLVER_TOOL_VERSION = "engineering-solver-v2.5"


def in_process_artifact(
    *,
    run_id: str,
    spec: SimulationSpec,
    outputs: dict[str, Quantity],
    solver_id: str,
    project_id: str | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    contract_version: str | None = None,
) -> ComputationArtifact:
    """Reproducibility metadata for a fully deterministic in-process solve.

    Uses the existing ComputationArtifact type and hash primitives — not a second store.
    sandbox_backend=in_process records that Docker was not required for this algebra.
    """
    py = python_version_fingerprint()
    code_payload = {
        "solver_id": solver_id,
        "model_type": spec.model_type,
        "equations": [eq.model_dump(mode="json") for eq in spec.equations],
        "assumptions": [a.id for a in spec.assumptions],
        "numerical_settings": spec.numerical_settings.model_dump(mode="json"),
    }
    inputs_payload: dict[str, Any] = {
        "parameters": {k: v.model_dump(mode="json") for k, v in spec.parameters.items()},
        "boundary_conditions": [bc.model_dump(mode="json") for bc in spec.boundary_conditions],
    }
    dep = dependency_fingerprint(
        package_version=package_version(),
        pyproject_hash=None,
    )
    env_h = environment_hash(
        python_version=py,
        platform_name=platform.platform(),
        dependency_hash=dep,
        runner_hash=sha256_json({"solver": SOLVER_TOOL_VERSION, "solver_id": solver_id}),
    )
    code_h = code_hash(sha256_json(code_payload))
    input_h = input_hash(inputs_payload)
    policy_h = sha256_json(
        {
            "backend": "in_process",
            "network": "deny",
            "filesystem": "none",
            "tool_version": SOLVER_TOOL_VERSION,
        }
    )
    identity = computation_hash(
        code_h=code_h,
        input_h=input_h,
        environment_h=env_h,
        policy_h=policy_h,
        python_version=py,
    )
    now = datetime.now(timezone.utc)
    result_dump = {name: qty.model_dump(mode="json") for name, qty in outputs.items()}
    return ComputationArtifact(
        run_id=run_id,
        project_id=project_id,
        investigation_id=investigation_id,
        task_id=task_id,
        contract_version=contract_version,
        kind="simulation",
        tool_version=SOLVER_TOOL_VERSION,
        status="ok",
        code=sha256_json(code_payload),
        result=result_dump,
        metadata={
            "solver_id": solver_id,
            "spec_id": spec.id,
            "model_type": spec.model_type,
            "backend": "in_process",
            "solver_determinism": "algebraic",
        },
        code_hash=code_h,
        input_hash=input_h,
        environment_hash=env_h,
        dependency_hash=dep,
        computation_hash=identity,
        python_version=py,
        sandbox_backend="in_process",
        sandbox_policy_version="in-process-v2.5",
        environment_reproducibility="full",
        determinism="unknown",
        started_at=now,
        finished_at=now,
        stdout_hash="unknown",
        stderr_hash="unknown",
    )
