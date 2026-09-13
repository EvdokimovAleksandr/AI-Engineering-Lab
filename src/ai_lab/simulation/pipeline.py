"""Run validate → solver → artifact. Optional ComputeSandbox is a future hook, not used here."""

from __future__ import annotations

from ai_lab.memory.run_store import RunStore
from ai_lab.simulation.artifact import in_process_artifact
from ai_lab.simulation.errors import UnknownSolverError
from ai_lab.simulation.models import SimulationResult, SimulationSpec
from ai_lab.simulation.protocol import SolverContext
from ai_lab.simulation.registry import SolverRegistry, default_registry
from ai_lab.simulation.validate import validate_simulation_spec


def run_simulation(
    spec: SimulationSpec,
    context: SolverContext,
    *,
    registry: SolverRegistry | None = None,
    run_store: RunStore | None = None,
) -> SimulationResult:
    """Validate then solve. Invalid specs never reach the solver."""
    allowed = registry.ids() if registry is not None else None
    validation = validate_simulation_spec(spec, allowed_solvers=allowed)
    if not validation.ok:
        from ai_lab.simulation.solvers.uniaxial_tension import _error_result

        return _error_result(spec, validation.status, validation.errors)

    reg = registry or default_registry()
    try:
        solver = reg.get(spec.solver.solver_id)
    except UnknownSolverError as exc:
        from ai_lab.simulation.solvers.uniaxial_tension import _error_result

        return _error_result(spec, exc.status, [str(exc)])

    result = solver.solve(spec, context)
    if result.outputs:
        artifact = in_process_artifact(
            run_id=context.run_id,
            spec=spec,
            outputs=result.outputs,
            solver_id=spec.solver.solver_id,
            project_id=context.project_id,
            investigation_id=context.investigation_id,
            task_id=context.task_id,
            contract_version=context.contract_version,
        )
        if run_store is not None:
            run_store.save_computation(artifact)
        result = result.model_copy(update={"computation_artifact_id": artifact.artifact_id})
    return result
