"""V2.8 HITL clarification, evidence-gap semantics, empty-research regression."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.agents.base import AgentContext
from ai_lab.agents.research import ResearchAgent
from ai_lab.core.enums import AgentRole, EvidenceKind, ProjectState, ResearchOutcome, ScopeStatus
from ai_lab.core.models import LabConfig, TaskSpec
from ai_lab.llm.mock import MockProvider
from ai_lab.memory.decision_log import DecisionLog
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.tracing import RunEventSink
from ai_lab.orchestrator.hitl import HitlDecision, HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.orchestrator.scope import apply_clarification, resolve_scope
from ai_lab.orchestrator.synthesis import render_final_report
from ai_lab.core.models import SynthesisBundle
from ai_lab.tools.base import ToolSpec
from ai_lab.tools.registry import ToolRegistry
from ai_lab.ui.templates import INDEX_HTML


REPO = Path(__file__).resolve().parents[1]


def _config() -> LabConfig:
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
async def test_clarification_pauses_run(tmp_path: Path) -> None:
    store = _project(tmp_path, "hitl_pause", "How powerful a heater do I need for 20 L of water?")
    rt = LabRuntime(store, _config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False))
    snap = await rt.run()
    assert snap.state == ProjectState.AWAITING_HUMAN
    assert snap.run_id == rt.run_id


@pytest.mark.asyncio
async def test_resume_uses_user_answer(tmp_path: Path) -> None:
    problem = "How powerful a heater do I need for 20 L of water?"
    store = _project(tmp_path, "hitl_resume", problem)
    first = LabRuntime(store, _config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False))
    snap = await first.run()
    run_id = snap.run_id
    assert snap.state == ProjectState.AWAITING_HUMAN

    resumed = LabRuntime(
        store,
        _config(),
        repo_root=tmp_path,
        hitl=HitlGate(
            auto_approve=False,
            pending_decision=HitlDecision(
                approved=True,
                note="from 20°C to 80°C in 30 minutes",
            ),
        ),
        resume_run_id=run_id,
    )
    out = await resumed.run()
    assert out.run_id == run_id
    orig = (store.root / ".runs" / run_id / "inputs" / "original_problem.md").read_text(
        encoding="utf-8"
    )
    assert orig.strip() == problem
    import json

    scope = json.loads(
        (store.root / ".runs" / run_id / "planner" / "scope.json").read_text(encoding="utf-8")
    )
    assert scope["original_problem"].strip() == problem
    assert scope["status"] == ScopeStatus.SCOPE_RESOLVED.value
    assert scope["locked"] is True
    assert out.state != ProjectState.AWAITING_HUMAN


@pytest.mark.asyncio
async def test_scope_recomputed_after_clarification() -> None:
    prior = resolve_scope("Насколько прочен этот материал?")
    assert prior.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
    updated = apply_clarification(prior, choice="Предел прочности при растяжении (UTS)")
    resolved = resolve_scope(updated.original_problem, prior=updated)
    assert resolved.status == ScopeStatus.SCOPE_RESOLVED
    assert "uts" in resolved.required_outputs
    assert resolved.original_problem == prior.original_problem


@pytest.mark.asyncio
async def test_clarification_does_not_restart_run_as_new_project(tmp_path: Path) -> None:
    store = _project(tmp_path, "same_proj", "Насколько прочен этот материал?")
    first = LabRuntime(store, _config(), repo_root=tmp_path, hitl=HitlGate(auto_approve=False))
    snap = await first.run()
    run_id = snap.run_id
    second = LabRuntime(
        store,
        _config(),
        repo_root=tmp_path,
        hitl=HitlGate(
            pending_decision=HitlDecision(
                approved=True, choice="Предел прочности при растяжении (UTS)"
            )
        ),
        resume_run_id=run_id,
    )
    out = await second.run()
    assert out.run_id == run_id
    assert store.name == "same_proj"
    runs = list((store.root / ".runs").iterdir())
    assert len([p for p in runs if p.is_dir() and p.name.startswith("run_")]) == 1


def test_missing_evidence_is_not_assumption() -> None:
    bundle = SynthesisBundle(
        report_gate="INSUFFICIENT_EVIDENCE",
        evidence_gaps=["No sufficient public evidence was retrieved after bounded query refinement."],
        research_status=ResearchOutcome.RESEARCH_EMPTY.value,
        scope={"original_problem": "obscure topic", "status": "SCOPE_RESOLVED"},
    )
    md = render_final_report(bundle)
    assert "## Evidence gaps" in md
    assert "No sufficient public evidence" in md
    assert "No additional public sources available" not in md
    # Gap text must not appear as a silent assumption-as-fact.
    assert "ASSUMPTION:\nthere is no evidence" not in md


def test_zero_sources_cannot_be_final_fact() -> None:
    from ai_lab.core.models import Claim

    claim = Claim(
        statement="No sufficient public evidence was retrieved after bounded query refinement.",
        kind=EvidenceKind.EVIDENCE_GAP,
        evidence="outcome=RESEARCH_EMPTY",
        agent_id=AgentRole.RESEARCH.value,
    )
    assert claim.kind != EvidenceKind.FACT
    assert claim.kind != EvidenceKind.ASSUMPTION


@pytest.mark.asyncio
async def test_empty_research_regression_is_evidence_gap_not_assumption(tmp_path: Path) -> None:
    """Screenshot regression: 0 sources must not become 'no public sources' ASSUMPTION."""
    store = _project(tmp_path, "empty_research", "Research industrial spider silk production.")
    tools = ToolRegistry()

    async def query(*, query: str = "", **_kw: object) -> dict:
        return {
            "query": query,
            "sources": [],
            "evidence": [],
            "metadata": {"raw_hit_count": 0},
            "outcome": ResearchOutcome.RESEARCH_EMPTY.value,
        }

    async def save(*, path: str, data: object, **_kw: object) -> dict:
        return {"path": path, "saved": True}

    tools.register(ToolSpec(name="research.query", description="q", handler=query))
    tools.register(ToolSpec(name="artifacts.save", description="a", handler=save))
    ctx = AgentContext(
        run_id="run_empty",
        store=store,
        evidence=EvidenceStore(store, run_id="run_empty"),
        decisions=DecisionLog(store.root / "decisions" / "decision_log.jsonl"),
        tools=tools,
        llm=MockProvider(),
        config=_config(),
        sink=RunEventSink(store.root / ".runs" / "run_empty" / "events.jsonl"),
        extra={"investigation_scope": resolve_scope("Research industrial spider silk production.")},
    )
    task = TaskSpec(
        task_id="research",
        role=AgentRole.RESEARCH,
        objective="industrial spider silk",
        output_schema="research_findings",
        allowed_tools=["research.query", "artifacts.save"],
    )
    result = await ResearchAgent().run(task, ctx)
    kinds = {c.kind for c in result.claims}
    statements = " ".join(c.statement for c in result.claims)
    assert EvidenceKind.EVIDENCE_GAP in kinds
    assert EvidenceKind.FACT not in kinds
    assert "No additional public sources available" not in statements
    assert all(c.kind != EvidenceKind.ASSUMPTION for c in result.claims)
    sufficiency = result.raw.get("research_sufficiency") or {}
    assert sufficiency.get("refinement_count", 0) >= 1
    assert sufficiency.get("outcome") in {
        ResearchOutcome.RESEARCH_EMPTY.value,
        ResearchOutcome.RESEARCH_FILTERED.value,
        ResearchOutcome.RESEARCH_PARTIAL.value,
    }


def test_ui_clarification_and_gaps_are_present() -> None:
    assert "Нужно уточнение" in INDEX_HTML
    assert "hitl-continue" in INDEX_HTML
    assert "/resume" in INDEX_HTML
    assert "Пробелы в доказательствах" in INDEX_HTML
    assert "SCOPE_RESOLUTION" in INDEX_HTML
    assert "Clarification provided by user" in INDEX_HTML
    # INDEX_HTML is a Python """ string: split('\n') would become a real newline and
    # break the entire SPA script. Served JS must keep an escaped newline.
    assert "split('\\n')" in INDEX_HTML
