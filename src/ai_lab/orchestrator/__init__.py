"""Orchestrator package — lazy exports to avoid import cycles with tools.registry."""

from __future__ import annotations

from typing import Any

__all__ = ["LabRuntime", "run_project"]


def __getattr__(name: str) -> Any:
    # tools.registry imports orchestrator.budget while LabRuntime imports tools;
    # eager runtime import in __init__ closes that cycle. Keep submodule imports direct.
    if name == "LabRuntime":
        from ai_lab.orchestrator.runtime import LabRuntime

        return LabRuntime
    if name == "run_project":
        from ai_lab.orchestrator.runtime import run_project

        return run_project
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
