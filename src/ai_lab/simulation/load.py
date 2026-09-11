"""Load trusted SimulationSpec fixtures. Arbitrary host paths are rejected."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_lab.core.enums import ParameterTrust
from ai_lab.core.models import Quantity
from ai_lab.simulation.errors import InvalidSimulationSpecError
from ai_lab.simulation.models import (
    Assumption,
    BoundaryCondition,
    EquationSpec,
    NumericalSettings,
    ParameterProvenance,
    SimulationSpec,
    SolverConfig,
)

TRUSTED_SPEC_IDS = frozenset({"uniaxial_tension"})

# Relative to repo root. Planner/UI cannot pass a host path; they name a spec_id.
_FIXTURE_REL = Path("fixtures") / "simulation" / "uniaxial_tension.json"


def load_trusted_spec(spec_id: str, *, repo_root: Path) -> SimulationSpec:
    """Load a pinned fixture by registry id. Unknown ids fail loudly."""
    if spec_id not in TRUSTED_SPEC_IDS:
        raise InvalidSimulationSpecError(f"Untrusted simulation spec_id: {spec_id!r}")
    path = (repo_root / _FIXTURE_REL).resolve()
    root = repo_root.resolve()
    if path != (root / _FIXTURE_REL).resolve() or not str(path).startswith(str(root)):
        raise InvalidSimulationSpecError("Simulation fixture path escaped repo root")
    if not path.is_file():
        raise InvalidSimulationSpecError(f"Missing trusted fixture: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InvalidSimulationSpecError("Simulation fixture root must be an object")
    return spec_from_fixture(data)


def spec_from_fixture(data: dict[str, Any]) -> SimulationSpec:
    """Parse fixture JSON into SimulationSpec. Extra host-control keys fail via the model."""
    params = {
        name: Quantity.model_validate(qty) for name, qty in (data.get("parameters") or {}).items()
    }
    provenance_raw = data.get("parameter_provenance") or {}
    default_prov = data.get("provenance") or {}
    provenance: dict[str, ParameterProvenance] = {}
    for name in params:
        row = provenance_raw.get(name) or default_prov
        provenance[name] = ParameterProvenance(
            source=str(row.get("source") or "fixture://synthetic"),
            trust=ParameterTrust(str(row.get("trust") or ParameterTrust.STUB.value)),
            note=str(row.get("note") or ""),
        )
    equations = [EquationSpec.model_validate(eq) for eq in data.get("equations") or []]
    assumptions = [Assumption.model_validate(a) for a in data.get("assumptions") or []]
    bcs = [BoundaryCondition.model_validate(bc) for bc in data.get("boundary_conditions") or []]
    solver = SolverConfig.model_validate(data.get("solver") or {"solver_id": "uniaxial_tension"})
    ns_raw = data.get("numerical_settings") or {}
    abs_tol = ns_raw.get("absolute_tolerance")
    numerical = NumericalSettings(
        absolute_tolerance=Quantity.model_validate(abs_tol) if abs_tol else Quantity(value=0.0, unit="Pa"),
        relative_tolerance=float(ns_raw.get("relative_tolerance", 1e-9)),
        max_iterations=int(ns_raw.get("max_iterations", 1)),
        solver_parameters=dict(ns_raw.get("solver_parameters") or {}),
    )
    return SimulationSpec(
        id=str(data.get("id") or "uniaxial_tension_synthetic"),
        model_type=str(data.get("model_type") or "uniaxial_tension"),
        parameters=params,
        equations=equations,
        assumptions=assumptions,
        boundary_conditions=bcs,
        solver=solver,
        numerical_settings=numerical,
        output_schema=str(data.get("output_schema") or "simulation_result"),
        metadata=dict(data.get("metadata") or {}),
        parameter_constraints=dict(data.get("parameter_constraints") or {}),
        expected_dimensions=dict(data.get("expected_dimensions") or {}),
        parameter_provenance=provenance,
        uncertainty=dict(data.get("uncertainty") or {"supported": False}),
    )
