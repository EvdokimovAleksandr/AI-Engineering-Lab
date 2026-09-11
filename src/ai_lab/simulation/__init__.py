"""Engineering Simulation Framework (V2.5)."""

from ai_lab.simulation.models import SimulationResult, SimulationSpec
from ai_lab.simulation.pipeline import run_simulation
from ai_lab.simulation.protocol import EngineeringSolver, SolverContext
from ai_lab.simulation.registry import SolverRegistry, default_registry, known_solver_ids
from ai_lab.simulation.validate import validate_simulation_spec

__all__ = [
    "EngineeringSolver",
    "SimulationResult",
    "SimulationSpec",
    "SolverContext",
    "SolverRegistry",
    "default_registry",
    "known_solver_ids",
    "run_simulation",
    "validate_simulation_spec",
]
