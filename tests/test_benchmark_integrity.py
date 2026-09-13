"""V2.6 benchmark integrity: calculation contract, empty checks, synthesis grounding."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.checks.calculation_contract import (
    evaluate_evidence_completeness,
    parse_calculation_spec,
    validate_computation_against_spec,
)
from ai_lab.core.enums import AdjudicationStatus, EvidenceKind, ProjectState, VerificationStatus
from ai_lab.core.models import (
    AdjudicationResult,
    CalculationSpec,
    Claim,
    ComputationArtifact,
    DeterministicCheckReport,
    LabConfig,
    MathCheckResult,
    RedTeamReport,
    RunBudget,
    VerificationPolicy,
    VerificationReport,
)
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.adjudication import adjudicate
from ai_lab.orchestrator.budget import record_llm_usage, total_tokens_from_usage
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime
from ai_lab.orchestrator import synthesis as synth_mod

REPO = Path(__file__).resolve().parents[1]


def _heater_spec(**overrides) -> CalculationSpec:
    data = {
        "objective": "calculate_heater_power",
        "required_inputs": ["water_volume_l", "heating_time_s"],
        "required_outputs": ["power"],
        "expected_dimensions": {"power": "W"},
        "domain": "thermal_heating",
        "task_id": "calculation",
    }
    data.update(overrides)
    return CalculationSpec.model_validate(data)


def _comp(*, outputs: dict, spec_id: str | None = None, task_id: str = "calculation") -> ComputationArtifact:
    return ComputationArtifact(
        run_id="run_test",
        kind="calculation",
        task_id=task_id,
        calculation_spec_id=spec_id,
        declared_outputs=outputs,
        returncode=0,
        status="ok",
        sandbox_status="SUCCESS",
        code="x=1",
    )


def test_empty_check_report_cannot_pass() -> None:
    """Architectural invariant: empty verification ≠ PASS when required."""
    empty = DeterministicCheckReport(results=[], verification_results=[], critical_failures=[])
    adj = adjudicate(
        check_report=empty,
        verification=None,
        red_team=None,
        require_independent_review=False,
        require_red_team=False,
        verification_required=True,
    )
    assert adj.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE
    assert adj.status != AdjudicationStatus.PASS


def test_empty_check_report_with_completeness() -> None:
    empty = DeterministicCheckReport()
    completeness = evaluate_evidence_completeness(
        calculation_specs=[_heater_spec()],
        computations=[_comp(outputs={"power": {"value": 3200, "unit": "W"}})],
        check_report=empty,
        verification_policy=VerificationPolicy(verification_required=True, minimum_checks=1),
        require_calculation=True,
    )
    assert not completeness.verification_complete
    adj = adjudicate(
        check_report=empty,
        verification=None,
        red_team=None,
        require_independent_review=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE


def test_unrelated_computation_rejected() -> None:
    spec = _heater_spec(spec_id="cspec_heater")
    comp = _comp(
        outputs={"memory_gib": {"value": 4.0, "unit": "GiB"}},
        spec_id=spec.spec_id,
    )
    rel = validate_computation_against_spec(comp, spec)
    assert rel.relevant is False
    assert "power" in rel.missing_outputs
    completeness = evaluate_evidence_completeness(
        calculation_specs=[spec],
        computations=[comp],
        check_report=DeterministicCheckReport(),
        verification_policy=VerificationPolicy(),
        require_calculation=True,
        relevance_results=[rel],
    )
    assert completeness.computation_relevant is False
    adj = adjudicate(
        check_report=DeterministicCheckReport(),
        verification=None,
        red_team=None,
        require_independent_review=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status != AdjudicationStatus.PASS


def test_missing_computation_insufficient() -> None:
    completeness = evaluate_evidence_completeness(
        calculation_specs=[_heater_spec()],
        computations=[],
        check_report=DeterministicCheckReport(
            results=[MathCheckResult(check_id="c1", passed=True)],
        ),
        verification_policy=VerificationPolicy(),
        require_calculation=True,
    )
    assert completeness.computation_complete is False
    adj = adjudicate(
        check_report=DeterministicCheckReport(
            results=[MathCheckResult(check_id="c1", passed=True)],
        ),
        verification=None,
        red_team=None,
        require_independent_review=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE


def test_wrong_arithmetic_fails() -> None:
    checks = DeterministicCheckReport(
        results=[MathCheckResult(check_id="c1", passed=False, discrepancy="delta")],
        critical_failures=["delta"],
    )
    adj = adjudicate(
        check_report=checks,
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=RedTeamReport(summary="ok"),
        require_independent_review=True,
    )
    assert adj.status == AdjudicationStatus.FAIL
    assert adj.deterministic_critical_failure is True


def test_correct_path_can_pass() -> None:
    spec = _heater_spec(spec_id="cspec_ok")
    comp = _comp(outputs={"power": {"value": 3200, "unit": "W"}}, spec_id=spec.spec_id)
    rel = validate_computation_against_spec(comp, spec)
    assert rel.relevant is True
    checks = DeterministicCheckReport(
        results=[
            MathCheckResult(
                check_id="c1",
                passed=True,
                details={"claim_id": "claim_power"},
            )
        ],
    )
    completeness = evaluate_evidence_completeness(
        calculation_specs=[spec],
        computations=[comp],
        check_report=checks,
        claims=[
            Claim(
                claim_id="claim_power",
                statement="power ≈ 3200 W",
                kind=EvidenceKind.CALCULATION,
                evidence="e",
                computation_artifact_id=comp.artifact_id,
                math_check={"expression": "1", "expected": 1, "tolerance": 0.1, "inputs": {}},
            )
        ],
        verification_policy=VerificationPolicy(),
        require_calculation=True,
        relevance_results=[rel],
    )
    assert completeness.is_complete
    adj = adjudicate(
        check_report=checks,
        verification=None,
        red_team=None,
        require_independent_review=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status == AdjudicationStatus.PASS


def test_synthesis_rejects_hallucinated_number() -> None:
    verified = Claim(
        claim_id="claim_ok",
        statement="Required power is 3200 W",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        computation_artifact_id="comp_1",
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    hallucinated = Claim(
        claim_id="claim_halluc",
        statement="Required power is 4100 W",
        kind=EvidenceKind.CALCULATION,
        evidence="llm prose",
    )
    checks = DeterministicCheckReport(
        results=[
            MathCheckResult(check_id="c1", passed=True, details={"claim_id": "claim_ok"})
        ]
    )
    adj = AdjudicationResult(status=AdjudicationStatus.PASS, reasons=["ok"])
    bundle = synth_mod.build_synthesis_bundle(
        claims=[verified, hallucinated],
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=RedTeamReport(summary="ok"),
        decisions=[],
        adjudication=adj,
        check_report=checks,
        narrative="the required power is 4100 W",
    )
    accepted_ids = {c["claim_id"] for c in bundle.accepted_claims}
    assert "claim_ok" in accepted_ids
    assert "claim_halluc" not in accepted_ids
    assert any("3200" in c["statement"] for c in bundle.verified_results)


def test_prose_wrong_compute_correct_verified_wins() -> None:
    verified = Claim(
        claim_id="claim_ok",
        statement="3200 W",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        computation_artifact_id="comp_1",
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    checks = DeterministicCheckReport(
        results=[MathCheckResult(check_id="c1", passed=True, details={"claim_id": "claim_ok"})]
    )
    bundle = synth_mod.build_synthesis_bundle(
        claims=[verified],
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=None,
        decisions=[],
        adjudication=AdjudicationResult(status=AdjudicationStatus.PASS, reasons=["ok"]),
        check_report=checks,
        narrative="explanation says 4 kW",
    )
    assert bundle.verified_results[0]["statement"] == "3200 W"
    report = synth_mod.render_final_report(bundle, llm_polish={"summary": "explanation says 4 kW"})
    assert "3200 W" in report
    assert "Narrative" in report


def test_llm_cannot_weaken_verification_policy() -> None:
    policy = VerificationPolicy(
        verification_required=True,
        minimum_checks=1,
        required_outputs=["power"],
        required_output_dimensions={"power": "W"},
    )
    spec = parse_calculation_spec(
        {
            "objective": "whatever",
            "required_outputs": [],
            "expected_dimensions": {},
            "minimum_checks": 0,
            "verification_required": False,
        },
        task_id="calculation",
        run_id="run_bench",
        project_id="proj_bench",
        investigation_id="proj_bench",
        policy=policy,
    )
    assert spec is not None
    assert spec.verification_required is True
    assert spec.minimum_checks >= 1
    assert "power" in spec.required_outputs
    assert spec.expected_dimensions.get("power") == "W"


def test_llm_cannot_override_locked_dimensions() -> None:
    """Policy dimensions must win over LLM expected_dimensions (not setdefault)."""
    policy = VerificationPolicy(
        verification_required=True,
        required_outputs=["power"],
        required_output_dimensions={"power": "W"},
    )
    spec = parse_calculation_spec(
        {
            "objective": "override attempt",
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "GiB"},
        },
        task_id="calculation",
        run_id="run_bench",
        project_id="proj_bench",
        investigation_id="proj_bench",
        policy=policy,
    )
    assert spec is not None
    assert spec.expected_dimensions["power"] == "W"



def test_understanding_outputs_lock_offtopic_spec() -> None:
    """Off-topic rod expansion cannot drop problem-bound power output."""
    from ai_lab.checks.calculation_contract import extract_outputs_from_understanding

    names, dims = extract_outputs_from_understanding(
        {
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
        }
    )
    assert names == ["power"]
    policy = VerificationPolicy(
        verification_required=True,
        required_outputs=names,
        required_output_dimensions=dims,
    )
    spec = parse_calculation_spec(
        {
            "objective": "aluminum rod expansion",
            "required_outputs": ["delta_L_m"],
            "expected_dimensions": {"delta_L_m": "m"},
        },
        task_id="calculation",
        run_id="run_bench",
        project_id="proj_bench",
        investigation_id="proj_bench",
        policy=policy,
    )
    assert spec is not None
    assert "power" in spec.required_outputs
    assert spec.expected_dimensions.get("power") == "W"


def test_budget_unknown_not_zero() -> None:
    assert total_tokens_from_usage(None) is None
    assert total_tokens_from_usage({}) is None
    assert total_tokens_from_usage({"prompt_tokens": 10}) is None
    assert total_tokens_from_usage({"total_tokens": 42}) == 42
    assert total_tokens_from_usage({"prompt_tokens": 10, "completion_tokens": 5}) == 15

    budget = RunBudget()
    record_llm_usage(budget, {"total_tokens": 100})
    assert budget.tokens_used == 100
    assert budget.tokens_unknown is False
    record_llm_usage(budget, None)
    assert budget.tokens_unknown is True
    assert budget.tokens_used is None


def test_stdout_alone_not_relevant() -> None:
    spec = _heater_spec(spec_id="cspec_x")
    comp = ComputationArtifact(
        run_id="r",
        kind="calculation",
        calculation_spec_id=spec.spec_id,
        stdout="power = 3200 W",
        returncode=0,
        declared_outputs={},
    )
    rel = validate_computation_against_spec(comp, spec)
    assert rel.relevant is False


def _heater_project(tmp_path: Path) -> ProjectStore:
    src = REPO / "benchmarks" / "simple_heater"
    project_dir = tmp_path / "simple_heater"
    project_dir.mkdir()
    for name in ("problem.md", "requirements.md", "assumptions.md"):
        text = (src / name).read_text(encoding="utf-8") if (src / name).is_file() else f"# {name}\n"
        (project_dir / name).write_text(text, encoding="utf-8")
    (project_dir / "final_report.md").write_text("# final\n", encoding="utf-8")
    store = ProjectStore(project_dir)
    store.ensure_layout()
    return store


def _lab_config() -> LabConfig:
    config_raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    config_raw["provider"] = "mock"
    return LabConfig.model_validate(config_raw)


@pytest.mark.asyncio
async def test_adversarial_kv_cache_fixture_not_pass(tmp_path: Path) -> None:
    """Full SIMPLE run with unrelated KV-cache computation → NOT PASS."""
    store = _heater_project(tmp_path)
    runtime = LabRuntime(
        store,
        _lab_config(),
        repo_root=REPO,
        hitl=HitlGate(auto_approve=True),
        simulation_fixture="kv_cache_unrelated",
    )
    snapshot = await runtime.run()
    assert snapshot.state == ProjectState.COMPLETED
    assert runtime._last_adjudication is not None
    assert runtime._last_adjudication.status != AdjudicationStatus.PASS
    manifest = runtime.run_store.load_manifest()
    assert manifest.engineering_outcome != "PASS"
    assert manifest.engineering_outcome in {"INSUFFICIENT_EVIDENCE", "FAIL"}
    assert manifest.budget is not None
    assert manifest.budget.agent_calls > 0
    assert manifest.budget.tokens_used is None or manifest.budget.tokens_used > 0


@pytest.mark.asyncio
async def test_heater_correct_fixture_can_pass(tmp_path: Path) -> None:
    store = _heater_project(tmp_path)
    runtime = LabRuntime(
        store,
        _lab_config(),
        repo_root=REPO,
        hitl=HitlGate(auto_approve=True),
        simulation_fixture="heater_correct",
    )
    snapshot = await runtime.run()
    assert snapshot.state == ProjectState.COMPLETED
    assert runtime._last_adjudication is not None
    assert runtime._last_adjudication.status == AdjudicationStatus.PASS
    assert runtime._last_check_report is not None
    assert runtime._last_check_report.all_passed
    bundle_path = store.root / "reviews" / "synthesis_bundle.json"
    assert bundle_path.is_file()


@pytest.mark.asyncio
async def test_heater_wrong_math_fails(tmp_path: Path) -> None:
    store = _heater_project(tmp_path)
    runtime = LabRuntime(
        store,
        _lab_config(),
        repo_root=REPO,
        hitl=HitlGate(auto_approve=True),
        simulation_fixture="heater_wrong_math",
    )
    snapshot = await runtime.run()
    assert snapshot.state == ProjectState.COMPLETED
    assert runtime._last_adjudication is not None
    assert runtime._last_adjudication.status == AdjudicationStatus.FAIL
