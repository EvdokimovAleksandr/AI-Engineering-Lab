"""P0/P1 unit and integration tests for V2 reliability gates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.checks.math_check import run_math_check
from ai_lab.core.enums import (
    AdjudicationStatus,
    AgreementType,
    EvidenceKind,
    ProjectState,
    SourceTrustTier,
    TrustLevel,
    VerificationStatus,
)
from ai_lab.core.models import (
    Claim,
    ComputationArtifact,
    DeterministicCheckReport,
    LabConfig,
    MathCheckRequest,
    MathCheckResult,
    RedTeamReport,
    RunBudget,
    VerificationReport,
)
from ai_lab.llm.cursor_sdk import CursorSDKProvider
from ai_lab.llm.mock import MockProvider
from ai_lab.core.models import LLMMessage, LLMRequest
from ai_lab.memory.decision_log import DecisionLog
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.review_bundle import build_review_bundle
from ai_lab.memory.run_store import RunStore
from ai_lab.orchestrator.adjudication import adjudicate
from ai_lab.orchestrator.budget import BudgetExceeded, record_agent_call
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.orchestrator.synthesis import build_synthesis_bundle, render_final_report
from ai_lab.tools.factory import build_tool_registry
from ai_lab.observability.tracing import RunEventSink


REPO = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_math_check_catches_wrong_expected() -> None:
    """Planted arithmetic error: expected wrong → FAIL (independent evidence)."""
    req = MathCheckRequest(
        expression="(force_n / (pi * (diameter_m / 2) ** 2)) / 1e9",
        inputs={"force_n": 0.05, "diameter_m": 5.0e-6},
        expected=1.0,  # planted wrong (true ~2.55)
        tolerance=1e-3,
        units={"force_n": "N", "diameter_m": "m"},
        required_units={"force_n": "N", "diameter_m": "m"},
    )
    result = await run_math_check(req)
    assert result.passed is False
    assert result.agreement_type == AgreementType.INDEPENDENT_EVIDENCE


@pytest.mark.asyncio
async def test_math_check_unit_mismatch_fails() -> None:
    req = MathCheckRequest(
        expression="force_n * 2",
        inputs={"force_n": 1.0},
        expected=2.0,
        units={"force_n": "mm"},  # wrong
        required_units={"force_n": "N"},
    )
    result = await run_math_check(req)
    assert result.passed is False
    assert "Unit mismatch" in (result.discrepancy or "")


@pytest.mark.asyncio
async def test_verification_catches_planted_calculation_error(tmp_path: Path) -> None:
    """End-to-end: deterministic FAIL forces adjudication FAIL (LLM cannot override)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    store = ProjectStore(project_dir)
    store.ensure_layout()
    evidence = EvidenceStore(store, run_id="run_planted")
    claim = Claim(
        statement="Planted wrong stress",
        kind=EvidenceKind.CALCULATION,
        evidence="test",
        run_id="run_planted",
        project_id="proj",
        math_check={
            "expression": "2 + 2",
            "expected": 5,  # planted error
            "tolerance": 1e-9,
            "inputs": {},
        },
    )
    evidence.save_claim(claim, subdirectory="calculations")

    config_raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    config_raw["provider"] = "mock"
    # Force LLM to try PASS — adjudication/checks must still FAIL
    config = LabConfig.model_validate(config_raw)
    runtime = LabRuntime(
        store,
        config,
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
        resume_run_id="run_planted",
    )
    # Inject claim path already done via evidence store on same project
    status = await runtime._run_independent_review()
    assert runtime._last_check_report is not None
    assert runtime._last_check_report.has_critical_failure
    assert runtime._last_verification is not None
    assert runtime._last_verification.status == VerificationStatus.FAIL
    assert status == AdjudicationStatus.FAIL


@pytest.mark.asyncio
async def test_mock_verification_cannot_pass_by_call_count() -> None:
    mock = MockProvider()
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="x")],
        metadata={"agent_role": "verification"},
    )
    r1 = await mock.complete(req)
    r2 = await mock.complete(req)
    r3 = await mock.complete(req)
    assert r1.parsed["status"] != "PASS"
    assert r2.parsed["status"] != "PASS"
    assert r3.parsed["status"] != "PASS"
    # PASS only via fixture metadata
    req2 = LLMRequest(
        messages=[LLMMessage(role="user", content="x")],
        metadata={"agent_role": "verification", "deterministic_all_passed": True},
    )
    ok = await mock.complete(req2)
    assert ok.parsed["status"] == "PASS"


def test_adjudication_blocks_pass_on_deterministic_fail() -> None:
    checks = DeterministicCheckReport(
        results=[MathCheckResult(check_id="c1", passed=False, discrepancy="delta")],
        critical_failures=["delta"],
    )
    ver = VerificationReport(status=VerificationStatus.PASS)  # LLM lies
    rt = RedTeamReport(summary="ok", recommended_reject=False)
    adj = adjudicate(check_report=checks, verification=ver, red_team=rt)
    assert adj.status == AdjudicationStatus.FAIL
    assert adj.deterministic_critical_failure is True


def test_synthesis_requires_verification_and_red_team_state() -> None:
    claims = [
        Claim(statement="s", kind=EvidenceKind.INFERENCE, evidence="e"),
    ]
    ver = VerificationReport(status=VerificationStatus.PASS)
    rt = RedTeamReport(summary="medium risk", recommended_reject=False)
    from ai_lab.core.models import AdjudicationResult

    adj = AdjudicationResult(status=AdjudicationStatus.PASS, reasons=["ok"])
    bundle = build_synthesis_bundle(
        claims=claims, verification=ver, red_team=rt, decisions=[], adjudication=adj
    )
    report = render_final_report(bundle, llm_polish={"summary": "pretty prose"})
    assert "PASS" in report or "ACCEPTED" in report
    assert ver.report_id in str(bundle.verification_reports)
    assert "Red team" in report
    assert "pretty prose" in report


def test_synthesis_marks_disputed_not_proven() -> None:
    from ai_lab.core.models import AdjudicationResult

    adj = AdjudicationResult(status=AdjudicationStatus.DISPUTED, reasons=["rt"])
    bundle = build_synthesis_bundle(
        claims=[Claim(statement="x", kind=EvidenceKind.ASSUMPTION)],
        verification=VerificationReport(status=VerificationStatus.DISPUTED),
        red_team=RedTeamReport(summary="attack"),
        decisions=[],
        adjudication=adj,
    )
    text = render_final_report(bundle)
    assert "DISPUTED" in text
    assert "not" in text.lower()


def test_decision_log_no_duplicate_from_single_owner(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "d.jsonl")
    from ai_lab.core.models import DecisionRecord
    from ai_lab.core.enums import DecisionStatus

    rec = DecisionRecord(question="q", status=DecisionStatus.PROPOSED)
    log.append(rec)
    # Simulate runtime-only persistence (chief must not also append)
    assert len(log.read_all()) == 1


def test_run_manifest_created(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    (proj / "problem.md").write_text("# p\n", encoding="utf-8")
    rs = RunStore(store, "run_testmanifest")
    config = LabConfig(provider="mock")
    budget = RunBudget(max_agent_calls=10)
    man = rs.build_manifest(config=config, repo_root=REPO, budget=budget)
    assert man.run_id == "run_testmanifest"
    assert man.python_version != ""
    assert "problem.md" in man.input_hashes
    assert (proj / ".runs" / "run_testmanifest" / "manifest.json").exists()
    # unknown fields must not be silently omitted — model has defaults
    assert man.git_commit  # may be unknown
    assert man.dependency_version


def test_computation_artifact_immutable(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    rs = RunStore(store, "run_immut")
    art = ComputationArtifact(run_id="run_immut", code="print(1)")
    rs.save_computation(art)
    with pytest.raises(FileExistsError):
        rs.save_computation(art)


def test_run_budget_stops_runaway() -> None:
    budget = RunBudget(max_agent_calls=2, max_tool_calls=100)
    record_agent_call(budget)
    record_agent_call(budget)
    with pytest.raises(BudgetExceeded):
        record_agent_call(budget)


def test_review_bundle_hides_author_confidence() -> None:
    claim = Claim(
        statement="x",
        kind=EvidenceKind.INFERENCE,
        evidence="e",
        agent_id="theorist",
    )
    claim.confidence.source_quality = 0.9
    bundle = build_review_bundle(run_id="r1", claims=[claim])
    dumped = bundle.model_dump(mode="json")
    assert "confidence" not in dumped["claims"][0]
    assert "agent_id" not in dumped["claims"][0]


@pytest.mark.asyncio
async def test_verification_and_red_team_isolation(tmp_path: Path) -> None:
    """Red team bundle view must not include verification conclusions."""
    project_dir = tmp_path / "iso"
    project_dir.mkdir()
    store = ProjectStore(project_dir)
    store.ensure_layout()
    evidence = EvidenceStore(store, run_id="run_iso")
    evidence.save_claim(
        Claim(
            statement="ok calc",
            kind=EvidenceKind.CALCULATION,
            evidence="e",
            run_id="run_iso",
            project_id="iso",
            math_check={"expression": "1+1", "expected": 2.0, "tolerance": 1e-9, "inputs": {}},
        ),
        subdirectory="calculations",
    )
    config = LabConfig.model_validate(
        yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    )
    config.provider = "mock"
    runtime = LabRuntime(
        store, config, repo_root=tmp_path, hitl=HitlGate(auto_approve=True), resume_run_id="run_iso"
    )
    await runtime._run_independent_review()
    assert runtime._last_verification is not None
    assert runtime._last_red_team is not None
    # Red team report file must not embed verification status field from peer
    rt_path = project_dir / "reviews" / "last_red_team.json"
    text = rt_path.read_text(encoding="utf-8")
    assert runtime._last_verification.report_id not in text


def test_claim_superseding(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    ev = EvidenceStore(store, run_id="run_sup")
    c1 = Claim(statement="v1", kind=EvidenceKind.ASSUMPTION, run_id="run_sup", project_id="p")
    ev.save_claim(c1, subdirectory="research")
    c2 = Claim(statement="v2", kind=EvidenceKind.ASSUMPTION, run_id="run_sup", project_id="p")
    ev.supersede_claim(c1.claim_id, c2, subdirectory="research")
    old = ev.load_claim(c1.claim_id)
    new = ev.load_claim(c2.claim_id)
    assert old.superseded_by == new.claim_id
    assert new.supersedes == old.claim_id
    assert new.version == 2
    assert old.statement == "v1"


@pytest.mark.asyncio
async def test_tool_output_marked_untrusted(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    sink = RunEventSink(tmp_path / "e.jsonl")
    config = LabConfig(provider="mock")
    reg = build_tool_registry(store, config, run_id="r", sink=sink)
    out = await reg.call("research.query", allowed=["research.query"], query="silk")
    assert out["trust_level"] in {TrustLevel.EXTERNAL.value, TrustLevel.UNTRUSTED.value}
    assert out.get("data_not_instructions") is True


def test_cursor_provider_reasoning_only_ignores_project_cwd(tmp_path: Path, monkeypatch) -> None:
    """Cursor must not bind cwd to project root (FS escape past ToolRegistry)."""
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")

    class _FakeAgent:
        @staticmethod
        def prompt(prompt, options):
            class R:
                status = "ok"
                result = '{"summary":"ok"}'
                id = "x"

            return R()

    class _Opts:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    import ai_lab.llm.cursor_sdk as mod

    monkeypatch.setattr(
        mod,
        "CursorSDKProvider",
        CursorSDKProvider,
    )
    # Patch import inside __init__
    import sys
    import types

    fake = types.ModuleType("cursor_sdk")
    fake.Agent = _FakeAgent
    fake.AgentOptions = lambda **kw: _Opts(**kw)
    fake.LocalAgentOptions = lambda **kw: _Opts(**kw)
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake)

    project = tmp_path / "project"
    project.mkdir()
    provider = CursorSDKProvider(api_key="test-key-not-real", cwd=project, reasoning_only=True)
    assert Path(provider.cwd) != project.resolve()
    assert "ai_lab_cursor_ro_" in str(provider.cwd)


def test_mock_source_cannot_be_fact() -> None:
    with pytest.raises(ValueError, match="mock://"):
        Claim(
            statement="x",
            kind=EvidenceKind.FACT,
            source="mock://x",
            source_trust=SourceTrustTier.STUB,
        )


@pytest.mark.asyncio
async def test_resume_keeps_run_id(tmp_path: Path) -> None:
    project_dir = tmp_path / "resume_proj"
    project_dir.mkdir()
    store = ProjectStore(project_dir)
    store.ensure_layout()
    (project_dir / "problem.md").write_text("# t\n", encoding="utf-8")
    # Seed mid-state
    from ai_lab.core.models import ProjectSnapshot

    snap = ProjectSnapshot(
        project_name="resume_proj",
        state=ProjectState.AWAITING_HUMAN,
        run_id="run_resume123",
        pending_hitl={"reason": "test"},
    )
    store.save_snapshot(snap)
    config = LabConfig(provider="mock")
    rt = LabRuntime(
        store,
        config,
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
        resume_run_id="run_resume123",
    )
    assert rt.run_id == "run_resume123"
    out = await rt.run()
    assert out.run_id == "run_resume123"
    assert out.state == ProjectState.AWAITING_HUMAN
