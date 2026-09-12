"""V2.7.2 planner reliability: closed-world roles, no remap, retry, static recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ai_lab.core.enums import AgentRole, TaskGraphValidationReason, TaskKind
from ai_lab.core.models import (
    LabConfig,
    LLMRequest,
    LLMResponse,
    PlannerResolution,
    RunBudget,
    TaskGraphProposal,
)
from ai_lab.llm.errors import ProviderUnavailable
from ai_lab.llm.mock import MockProvider
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.planner.context import ProblemContext
from ai_lab.planner.llm import LLMPlanner, PLANNER_SYSTEM
from ai_lab.planner.pipeline import plan_and_validate, plan_with_recovery
from ai_lab.planner.proposal import ProposalError, parse_proposal
from ai_lab.planner.schemas import allowed_role_values, allowed_task_kind_values
from ai_lab.planner.static import StaticPlanner
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph
from ai_lab.ui.report import build_result_view

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures" / "planner"


def _problem() -> ProblemContext:
    return ProblemContext(
        project_id="p",
        run_id="run_test",
        problem_text="How does max stress change if a cylinder diameter doubles?",
        budget=RunBudget(max_agent_calls=100, max_tool_calls=500, max_tokens=1_000_000, max_cost=100.0),
    )


def _vctx() -> TaskGraphValidationContext:
    return TaskGraphValidationContext(budget=_problem().budget)


def _load_invented() -> dict[str, Any]:
    return json.loads((FIXTURES / "invented_roles_malformed.json").read_text(encoding="utf-8"))


class ScriptedPlannerProvider:
    """Deterministic LLM stand-in: sequence of planner payloads or exceptions."""

    name = "scripted"

    def __init__(self, planner_payloads: list[Any]) -> None:
        self.planner_payloads = list(planner_payloads)
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.response_schema_name != "TaskGraphProposal":
            raise AssertionError("ScriptedPlannerProvider is planner-only")
        if self.calls >= len(self.planner_payloads):
            raise AssertionError("unexpected extra planner call")
        item = self.planner_payloads[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        parsed = item if isinstance(item, dict) else None
        return LLMResponse(
            content=json.dumps(parsed) if parsed is not None else "not json",
            parsed=parsed,
            provider=self.name,
            model="scripted",
        )


class BrokenStaticPlanner:
    """Fallback that itself fails the shared validator — no special bypass."""

    name = "static"

    async def propose(self, context: ProblemContext, *, repair_errors=None) -> TaskGraphProposal:
        del context, repair_errors
        return TaskGraphProposal(
            graph_id="broken_static",
            tasks=[
                {
                    "task_id": "rogue",
                    "role": "super_orchestrator",
                    "objective": "should not execute",
                    "inputs": ["problem.md"],
                    "output_schema": "agent_result",
                }
            ],
        )


def _valid_llm_graph() -> dict[str, Any]:
    return json.loads((FIXTURES / "valid_proposal.json").read_text(encoding="utf-8"))


def test_prompt_uses_canonical_role_enum() -> None:
    for role in allowed_role_values():
        assert role in PLANNER_SYSTEM
    assert "requirements_analyst" not in allowed_role_values()
    for kind in allowed_task_kind_values():
        assert kind in PLANNER_SYSTEM or kind.replace("_", " ") in PLANNER_SYSTEM.lower()


def test_unknown_role_not_remapped() -> None:
    with pytest.raises(ProposalError) as exc:
        parse_proposal(_load_invented())
    assert exc.value.reason == TaskGraphValidationReason.UNKNOWN_ROLE
    blob = " ".join(exc.value.errors)
    assert "requirements_analyst" in blob
    assert "mechanics_analyst" in blob
    assert "technical_writer" in blob
    assert "chief_engineer" not in blob or "Input should be" in blob
    # Runtime must not rewrite invented roles into legal ones.
    assert "→" not in blob


@pytest.mark.asyncio
async def test_unknown_role_recovers_to_static() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _load_invented()]))
    outcome = await plan_with_recovery(
        planner,
        _problem(),
        validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="standard"),
        fallback_profile="standard",
    )
    assert outcome.graph is not None
    assert outcome.validation.ok
    assert outcome.resolution.accepted is False
    assert outcome.resolution.recovered is True
    assert outcome.resolution.fallback == "static"
    assert outcome.resolution.fallback_profile == "standard"
    assert outcome.resolution.final_planner_type == "static"
    assert {t.role for t in outcome.graph.tasks if t.role} <= set(AgentRole)


@pytest.mark.asyncio
async def test_multiple_unknown_roles_no_remap() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _load_invented()]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="standard"),
        fallback_profile="standard",
    )
    dumped = json.dumps(outcome.rejected_proposal.model_dump(mode="json") if outcome.rejected_proposal else {})
    assert "requirements_analyst" in dumped
    assert "mechanics_analyst" in dumped
    assert "technical_writer" in dumped
    roles = {t.role.value for t in outcome.graph.tasks if t.role}
    assert "requirements_analyst" not in roles
    assert "mechanics_analyst" not in roles
    assert "technical_writer" not in roles


@pytest.mark.asyncio
async def test_invalid_budget_type_rejected() -> None:
    payload = {
        "graph_id": "bad_budget",
        "version": 1,
        "tasks": [
            {
                "task_id": "research",
                "role": "research",
                "objective": "research",
                "inputs": ["problem.md"],
                "output_schema": "research_findings",
                "budget_slice": 3,
            }
        ],
    }
    planner = LLMPlanner(ScriptedPlannerProvider([payload, payload]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="simple"),
        fallback_profile="simple",
    )
    assert outcome.resolution.recovered
    assert outcome.resolution.rejection_reason == TaskGraphValidationReason.MALFORMED_PROPOSAL.value
    assert any("budget_slice" in e for e in outcome.resolution.validation_errors)


@pytest.mark.asyncio
async def test_invalid_inputs_type_rejected() -> None:
    payload = {
        "graph_id": "bad_inputs",
        "version": 1,
        "tasks": [
            {
                "task_id": "research",
                "role": "research",
                "objective": "research",
                "inputs": {},
                "output_schema": "research_findings",
            }
        ],
    }
    planner = LLMPlanner(ScriptedPlannerProvider([payload, payload]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="simple"),
        fallback_profile="simple",
    )
    assert outcome.resolution.recovered
    assert any("inputs" in e for e in outcome.resolution.validation_errors)


@pytest.mark.asyncio
async def test_mixed_invalid_graph_single_rejection() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _load_invented()]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="standard"),
        fallback_profile="standard",
    )
    errors = outcome.resolution.validation_errors
    assert outcome.resolution.planner_fallbacks == 1
    assert any("role" in e for e in errors)
    assert any("budget_slice" in e or "inputs" in e for e in errors)
    task_ids = {t.task_id for t in outcome.graph.tasks}
    assert "req_analysis" not in task_ids
    assert "mechanics" not in task_ids


@pytest.mark.asyncio
async def test_valid_llm_planner_no_fallback() -> None:
    class Guard:
        name = "static"

        async def propose(self, context, *, repair_errors=None):
            raise AssertionError("static fallback must not run for a valid LLM graph")

    planner = LLMPlanner(ScriptedPlannerProvider([_valid_llm_graph()]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=Guard(),
        fallback_profile="standard",
    )
    assert outcome.resolution.accepted is True
    assert outcome.resolution.recovered is False
    assert outcome.resolution.fallback is None
    assert outcome.resolution.final_planner_type == "llm"
    assert outcome.graph is not None
    assert outcome.graph.graph_id == "fixture_valid"


@pytest.mark.asyncio
async def test_static_fallback_also_validated() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _load_invented()]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=BrokenStaticPlanner(),
        fallback_profile="standard",
    )
    assert outcome.graph is None
    assert outcome.validation.ok is False
    assert outcome.resolution.recovered is False
    assert outcome.resolution.fallback == "static"
    assert outcome.resolution.final_planner_type == "static"


@pytest.mark.asyncio
async def test_retry_succeeds() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _valid_llm_graph()]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=BrokenStaticPlanner(),
        fallback_profile="standard",
    )
    assert outcome.resolution.retry_count == 1
    assert outcome.resolution.recovered is False
    assert outcome.resolution.accepted is True
    assert outcome.resolution.final_planner_type == "llm"
    assert outcome.graph.graph_id == "fixture_valid"


@pytest.mark.asyncio
async def test_retry_fails_then_static() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _load_invented()]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="standard"),
        fallback_profile="standard",
    )
    assert outcome.resolution.retry_count == 1
    assert outcome.resolution.fallback == "static"
    assert outcome.resolution.recovered is True
    assert outcome.resolution.planner_attempts == 2


@pytest.mark.asyncio
async def test_fallback_uses_original_problem_not_bad_graph() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented(), _load_invented()]))
    ctx = _problem()
    outcome = await plan_with_recovery(
        planner, ctx, validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="standard"),
        fallback_profile="standard",
    )
    assert outcome.graph.graph_id == "standard_pipeline"
    assert outcome.graph.metadata.get("project_id") == ctx.project_id
    assert outcome.proposal is not None
    assert outcome.proposal.metadata.get("planner") == "static"
    assert outcome.rejected_proposal is not None
    assert outcome.graph.graph_id != "invented_roles_malformed"


@pytest.mark.asyncio
async def test_provider_failure_is_not_fake_llm_success() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([ProviderUnavailable("cursor down")]))
    outcome = await plan_with_recovery(
        planner, _problem(), validation_context=_vctx(),
        fallback_planner=StaticPlanner(pipeline="simple"),
        fallback_profile="simple",
    )
    assert outcome.resolution.accepted is False
    assert outcome.resolution.final_planner_type == "static"
    assert outcome.resolution.failure_class == "provider_error"
    assert outcome.resolution.recovered is True
    assert outcome.graph is not None
    # Must not pretend the LLM produced this graph.
    assert outcome.proposal.metadata.get("planner") == "static"


def test_planning_recovered_and_engineering_pass_are_independent(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "problem.md").write_text("# p\n", encoding="utf-8")
    store = ProjectStore(project_dir)
    store.ensure_layout()
    run_id = "run_axes"
    run_dir = store.root / ".runs" / run_id
    (run_dir / "reviews").mkdir(parents=True)
    (run_dir / "planner").mkdir(parents=True)
    (run_dir / "claims").mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "project_id": store.name,
        "final_state": "COMPLETED",
        "engineering_outcome": "PASS",
        "planner": PlannerResolution(
            requested="llm",
            accepted=False,
            recovered=True,
            fallback="static",
            fallback_profile="standard",
            retry_count=1,
            planner_attempts=2,
            planner_rejections=2,
            planner_fallbacks=1,
            final_planner_type="static",
            rejection_reason="UNKNOWN_ROLE",
            validation_errors=["tasks[0].role: unknown role 'mechanics_analyst'"],
        ).model_dump(mode="json"),
        "task_graph_id": "standard_pipeline",
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "planner" / "task_graph.json").write_text(
        json.dumps({"graph_id": "standard_pipeline", "tasks": []}), encoding="utf-8"
    )
    view = build_result_view(store, run_id, manifest=manifest, lifecycle={"status": "COMPLETED"})
    assert view["planning"]["status"] == "RECOVERED"
    assert view["engineering_outcome"] == "PASS"
    assert view["lifecycle_status"] == "COMPLETED"
    assert view["error"] in (None, "")


@pytest.mark.asyncio
async def test_runtime_regression_invented_roles_recovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _load_invented()

    def _bad_proposal(self: MockProvider) -> dict[str, Any]:
        return fixture

    monkeypatch.setattr(MockProvider, "_planner_proposal", _bad_proposal)

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "problem.md").write_text(
        "# How does max stress change if diameter doubles?\n", encoding="utf-8"
    )
    (project_dir / "requirements.md").write_text("TODO\n", encoding="utf-8")
    (project_dir / "assumptions.md").write_text("TODO\n", encoding="utf-8")
    store = ProjectStore(project_dir)
    store.ensure_layout()
    config = LabConfig.model_validate(
        yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    )
    config.provider = "mock"
    config.runtime["planner"] = "llm"
    config.runtime["hitl_on_disputed"] = False
    runtime = LabRuntime(store, config, repo_root=tmp_path, hitl=HitlGate(auto_approve=True))
    snapshot = await runtime.run()
    from ai_lab.core.enums import ProjectState

    assert snapshot.state == ProjectState.COMPLETED
    run_dir = project_dir / ".runs" / snapshot.run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    planner = manifest["planner"]
    assert planner["accepted"] is False
    assert planner["recovered"] is True
    assert planner["fallback"] == "static"
    assert planner["final_planner_type"] == "static"
    graph = json.loads((run_dir / "planner" / "task_graph.json").read_text(encoding="utf-8"))
    assert graph["graph_id"] == manifest["task_graph_id"]
    assert graph["graph_id"] != "invented_roles_malformed"
    roles = {t.get("role") for t in graph["tasks"]}
    assert "requirements_analyst" not in roles
    rejected = json.loads((run_dir / "planner" / "rejected_proposal.json").read_text(encoding="utf-8"))
    assert rejected["graph_id"] == "invented_roles_malformed"
    events = (tmp_path / ".runs" / f"{snapshot.run_id}.jsonl").read_text(encoding="utf-8")
    assert "planner.proposal_rejected" in events
    assert "planner.recovered" in events
    assert "pipeline.ready" in events
    view = build_result_view(
        store, snapshot.run_id, manifest=manifest, lifecycle={"status": "COMPLETED"}
    )
    assert view["planning"]["status"] == "RECOVERED"
    assert view["lifecycle_status"] != "ERROR"
    # Engineering axis is independent of planner recovery.
    assert view["engineering_outcome"] is not None


def test_ui_explains_planner_recovery() -> None:
    from ai_lab.ui.templates import INDEX_HTML

    assert "Предложение AI-планировщика отклонено" in INDEX_HTML
    assert "planner.proposal_rejected" in INDEX_HTML
    assert "planner.recovered" in INDEX_HTML


def test_kinds_remain_closed_world() -> None:
    assert "requirements_analysis" not in allowed_task_kind_values()
    assert TaskKind.AGENT.value in allowed_task_kind_values()


@pytest.mark.asyncio
async def test_plan_and_validate_still_rejects_without_recovery() -> None:
    planner = LLMPlanner(ScriptedPlannerProvider([_load_invented()]))
    proposal, graph, result = await plan_and_validate(planner, _problem(), validation_context=_vctx())
    assert graph is None
    assert result.ok is False
    assert result.reason == TaskGraphValidationReason.UNKNOWN_ROLE
