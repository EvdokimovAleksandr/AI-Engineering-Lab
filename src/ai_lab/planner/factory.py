"""Planner factory — unknown kinds fail loudly (no silent fallback)."""

from __future__ import annotations

from ai_lab.core.models import LabConfig
from ai_lab.planner.llm import LLMPlanner
from ai_lab.planner.static import StaticPlanner


def create_planner(
    config: LabConfig,
    *,
    llm: object | None = None,
    pipeline_override: str | None = None,
) -> StaticPlanner | LLMPlanner:
    """Build a planner.

    `pipeline_override` comes from TaskRouter (trusted runtime decision),
    not from LLM/UI text. Explicit simulation.pipeline=uniaxial_tension
    still wins when override is omitted.
    """
    kind = str((config.runtime or {}).get("planner") or "static").strip().lower()
    if pipeline_override:
        pipeline = pipeline_override.strip().lower()
    else:
        pipeline = str((config.simulation or {}).get("pipeline") or "default").strip().lower()
    if kind == "static":
        return StaticPlanner(pipeline=pipeline)
    if kind == "llm":
        if llm is None:
            raise ValueError("runtime.planner=llm requires an LLM provider")
        return LLMPlanner(llm)
    raise ValueError(f"Unknown runtime.planner {kind!r} (expected static|llm)")
