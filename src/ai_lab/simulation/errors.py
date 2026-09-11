"""Structured simulation errors. Distinct from SandboxError / CheckStatus."""

from __future__ import annotations

from ai_lab.core.enums import SimulationStatus


class SimulationError(Exception):
    """Base error for the engineering simulation layer."""

    def __init__(self, message: str, *, status: SimulationStatus) -> None:
        super().__init__(message)
        self.status = status


class InvalidSimulationSpecError(SimulationError):
    def __init__(self, message: str, *, status: SimulationStatus = SimulationStatus.INVALID_MODEL) -> None:
        super().__init__(message, status=status)


class UnknownSolverError(SimulationError):
    def __init__(self, solver_id: str) -> None:
        super().__init__(f"Unknown solver_id: {solver_id!r}", status=SimulationStatus.SOLVER_ERROR)
        self.solver_id = solver_id
