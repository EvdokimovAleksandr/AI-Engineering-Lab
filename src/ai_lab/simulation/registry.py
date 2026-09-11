"""Trusted solver registry. Identifiers only — no dynamic import from user text."""

from __future__ import annotations

from ai_lab.simulation.errors import UnknownSolverError
from ai_lab.simulation.protocol import EngineeringSolver

# Built-in solvers are imported lazily so validate.py can ask for ids without loading Pint solvers.
_SOLVER_IDS = frozenset({"uniaxial_tension"})


def known_solver_ids() -> frozenset[str]:
    return _SOLVER_IDS


class SolverRegistry:
    """Map trusted solver_id → EngineeringSolver. Unknown ids fail loudly."""

    def __init__(self, solvers: dict[str, EngineeringSolver] | None = None) -> None:
        self._solvers: dict[str, EngineeringSolver] = dict(solvers or default_solvers())
        unknown = set(self._solvers) - _SOLVER_IDS
        if unknown:
            raise UnknownSolverError(sorted(unknown)[0])

    def get(self, solver_id: str) -> EngineeringSolver:
        if solver_id not in _SOLVER_IDS:
            raise UnknownSolverError(solver_id)
        solver = self._solvers.get(solver_id)
        if solver is None:
            raise UnknownSolverError(solver_id)
        return solver

    def ids(self) -> frozenset[str]:
        return frozenset(self._solvers)

    def __contains__(self, solver_id: object) -> bool:
        return isinstance(solver_id, str) and solver_id in self._solvers


def default_solvers() -> dict[str, EngineeringSolver]:
    from ai_lab.simulation.solvers.uniaxial_tension import UniaxialTensionSolver

    return {"uniaxial_tension": UniaxialTensionSolver()}


def default_registry() -> SolverRegistry:
    return SolverRegistry()
