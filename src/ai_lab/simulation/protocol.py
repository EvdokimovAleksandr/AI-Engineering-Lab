"""EngineeringSolver protocol. Solvers are deterministic computations, not agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ai_lab.simulation.models import SimulationResult, SimulationSpec


@dataclass
class SolverContext:
    """Runtime facts a solver may use. Not LLM-controlled and not a host shell."""

    run_id: str
    task_id: str | None = None
    repo_root: Path | None = None
    extra: dict[str, Any] | None = None


@runtime_checkable
class EngineeringSolver(Protocol):
    """Deterministic engineering computation for one model_type."""

    solver_id: str

    def solve(self, spec: SimulationSpec, context: SolverContext) -> SimulationResult:
        ...
