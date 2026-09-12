"""V2.8 investigation scope: resolution, lock, clarification, immutability."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.core.enums import AgentRole, AssumptionKind, ProjectState, ScopeStatus
from ai_lab.core.models import LabConfig
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlDecision, HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.orchestrator.scope import (
    lock_scope,
    original_problem_hash,
    resolve_scope,
)


REPO = Path(__file__).resolve().parents[1]
HEATER_COMPLETE = (
    REPO / "benchmarks" / "simple_heater" / "problem.md"
).read_text(encoding="utf-8")


def test_clear_problem_resolves_scope() -> None:
    scope = resolve_scope(
        "Calculate heater power for 20 L from 20°C to 80°C in 30 min with 15% losses."
    )
    assert scope.status == ScopeStatus.SCOPE_RESOLVED
    assert "power" in scope.required_outputs
    assert scope.clarification is None
    assert scope.original_problem.startswith("Calculate heater power")


def test_ambiguous_problem_requests_clarification() -> None:
    scope = resolve_scope("Насколько прочен этот материал?")
    assert scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
    assert scope.clarification is not None
    assert "прочн" in scope.clarification.question.lower()
    assert len(scope.clarification.options) >= 3


def test_missing_required_parameter_requests_clarification() -> None:
    scope = resolve_scope("Сколько мощности нужно, чтобы нагреть 20 литров воды?")
    assert scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
    assert "t_initial" in scope.unknown_parameters
    assert "duration" in scope.unknown_parameters
    assert scope.clarification is not None
    assert scope.clarification.input_mode == "text"


def test_safe_default_does_not_trigger_clarification() -> None:
    # Density/cp are safe PARAMETER_ESTIMATE; missing % losses is non-blocking.
    scope = resolve_scope(
        "Сколько мощности нужно, чтобы нагреть 20 литров воды с 20°C до 80°C за 30 минут?"
    )
    assert scope.status == ScopeStatus.SCOPE_RESOLVED
    kinds = {a.kind for a in scope.assumptions}
    assert AssumptionKind.PARAMETER_ESTIMATE in kinds
    assert not any(a.blocking for a in scope.assumptions)


def test_scope_is_locked_after_resolution() -> None:
    scope = resolve_scope(
        "Calculate heater power for 20 L from 20°C to 80°C in 30 min."
    )
    locked = lock_scope(scope)
    assert locked.locked is True
    with pytest.raises(ValueError, match="Cannot lock"):
        lock_scope(
            resolve_scope("Насколько прочен этот материал?")
        )


def test_original_problem_is_immutable() -> None:
    first = resolve_scope("Исследуй промышленное производство искусственного паучьего шёлка.")
    second = resolve_scope("totally different prompt", prior=first)
    assert second.original_problem == first.original_problem
    assert original_problem_hash(second.original_problem) == original_problem_hash(
        first.original_problem
    )


def test_benchmark_heater_markdown_resolves() -> None:
    scope = resolve_scope(HEATER_COMPLETE)
    assert scope.status == ScopeStatus.SCOPE_RESOLVED
    assert scope.required_outputs == ["power"]


def test_no_new_scoping_agent_role() -> None:
    values = {r.value for r in AgentRole}
    assert "scope_analyst" not in values
    assert "requirements_analyst" not in values
    assert "research_manager" not in values
    assert "technical_writer" not in values


def _lab_config() -> LabConfig:
    raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    raw["provider"] = "mock"
    raw["runtime"]["hitl_on_disputed"] = False
    return LabConfig.model_validate(raw)


def _project(tmp_path: Path, name: str, problem: str) -> ProjectStore:
    root = tmp_path / name
    root.mkdir()
    (root / "problem.md").write_text(problem, encoding="utf-8")
    (root / "requirements.md").write_text("", encoding="utf-8")
    (root / "assumptions.md").write_text("", encoding="utf-8")
    store = ProjectStore(root)
    store.ensure_layout()
    return store


@pytest.mark.asyncio
async def test_heater_missing_parameters_requests_clarification(tmp_path: Path) -> None:
    store = _project(
        tmp_path,
        "heater_gap",
        "Сколько мощности нужно, чтобы нагреть 20 литров воды?",
    )
    rt = LabRuntime(
        store, _lab_config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False)
    )
    snap = await rt.run()
    assert snap.state == ProjectState.AWAITING_HUMAN
    pending = snap.pending_hitl or {}
    ctx = pending.get("context") or {}
    assert ctx.get("kind") == "scope_clarification"
    assert pending.get("requested_action") == "clarify_scope"


@pytest.mark.asyncio
async def test_heater_complete_parameters_runs_without_clarification(tmp_path: Path) -> None:
    store = _project(tmp_path, "heater_ok", HEATER_COMPLETE)
    rt = LabRuntime(
        store, _lab_config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False)
    )
    snap = await rt.run()
    assert snap.state != ProjectState.AWAITING_HUMAN
    scope_path = store.root / ".runs" / snap.run_id / "planner" / "scope.json"
    assert scope_path.is_file()
    import json

    payload = json.loads(scope_path.read_text(encoding="utf-8"))
    assert payload["status"] == ScopeStatus.SCOPE_RESOLVED.value
    assert payload["locked"] is True


@pytest.mark.asyncio
async def test_ambiguous_strength_question_requests_clarification(tmp_path: Path) -> None:
    store = _project(tmp_path, "strength_q", "Насколько прочен этот материал?")
    rt = LabRuntime(
        store, _lab_config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False)
    )
    snap = await rt.run()
    assert snap.state == ProjectState.AWAITING_HUMAN
    options = (snap.pending_hitl or {}).get("options") or []
    assert any("прочн" in str(o).lower() or "UTS" in str(o) for o in options)
