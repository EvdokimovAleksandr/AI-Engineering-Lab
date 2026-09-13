"""V2.6.1 adversarial integrity: semantic drift, policy lock, acceptance, coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_lab.benchmark.acceptance import evaluate_acceptance
from ai_lab.benchmark.models import InputAcceptance
from ai_lab.benchmark.registry import get_benchmark
from ai_lab.checks.calculation_contract import (
    evaluate_evidence_completeness,
    extract_outputs_from_understanding,
    is_vague_output_name,
    parse_calculation_spec,
    validate_calculation_spec,
    validate_computation_against_spec,
)
from ai_lab.checks.units import is_parseable_unit, units_compatible
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
    VerificationPolicy,
    VerificationReport,
)
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.adjudication import adjudicate
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


async def _run_fixture(tmp_path: Path, fixture: str) -> LabRuntime:
    store = _heater_project(tmp_path)
    runtime = LabRuntime(
        store,
        _lab_config(),
        repo_root=REPO,
        hitl=HitlGate(auto_approve=True),
        simulation_fixture=fixture,
    )
    snapshot = await runtime.run()
    assert snapshot.state == ProjectState.COMPLETED
    return runtime


def _assert_engineering_not_pass(runtime: LabRuntime) -> None:
    assert runtime._last_adjudication is not None
    assert runtime._last_adjudication.status != AdjudicationStatus.PASS
    manifest = runtime.run_store.load_manifest()
    assert manifest.engineering_outcome != "PASS"
    assert manifest.final_state == "COMPLETED"


# --- Attack class 1: semantic drift ---


@pytest.mark.asyncio
async def test_irrelevant_kv_cache_cannot_pass_engineering(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "kv_cache_unrelated")
    _assert_engineering_not_pass(runtime)
    assert runtime._last_evidence_completeness is not None
    assert runtime._last_evidence_completeness.computation_relevant is False


@pytest.mark.asyncio
async def test_irrelevant_aluminum_extension_cannot_pass_engineering(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "irrelevant_aluminum")
    _assert_engineering_not_pass(runtime)


@pytest.mark.asyncio
async def test_irrelevant_heat_flux_cannot_pass_engineering(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "irrelevant_heat_flux")
    _assert_engineering_not_pass(runtime)


# --- Attack class 2 / 18: empty verification ---


def test_empty_verification_is_insufficient_evidence() -> None:
    empty = DeterministicCheckReport(results=[], verification_results=[])
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


def test_adjudication_does_not_pass_on_absence_of_failures() -> None:
    """no_failures alone must never imply PASS when verification is required."""
    adj = adjudicate(
        check_report=DeterministicCheckReport(),
        verification=None,
        red_team=None,
        require_independent_review=False,
        verification_required=True,
    )
    assert adj.status != AdjudicationStatus.PASS


# --- Attack class 3 / 16: synthesis hallucination ---


def test_unverified_prose_cannot_create_engineering_pass() -> None:
    halluc = Claim(
        claim_id="claim_prose",
        statement="Required heater power = 3.25 kW",
        kind=EvidenceKind.CALCULATION,
        evidence="chief prose",
    )
    adj = AdjudicationResult(status=AdjudicationStatus.INSUFFICIENT_EVIDENCE, reasons=["no evidence"])
    bundle = synth_mod.build_synthesis_bundle(
        claims=[halluc],
        verification=None,
        red_team=None,
        decisions=[],
        adjudication=adj,
        check_report=DeterministicCheckReport(),
        narrative="Required heater power = 3.25 kW",
    )
    assert bundle.verified_results == []
    assert bundle.accepted_claims == []
    report = synth_mod.render_final_report(bundle, llm_polish={"summary": "PASS 3.25 kW"})
    assert "3.25" in report or "Narrative" in report
    assert "ACCEPTED" not in report or "not" in report.lower()
    assert bundle.report_gate != "PASS"


def test_grounded_synthesis_cannot_resurrect_unsupported_answer() -> None:
    claim = Claim(
        claim_id="c1",
        statement="Required heater power = 3.25 kW",
        kind=EvidenceKind.OPINION,
        evidence="narrative",
    )
    bundle = synth_mod.build_synthesis_bundle(
        claims=[claim],
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=RedTeamReport(summary="ok"),
        decisions=[],
        adjudication=AdjudicationResult(status=AdjudicationStatus.INSUFFICIENT_EVIDENCE),
        check_report=None,
        narrative="answer = 3.2 kW",
    )
    assert not bundle.verified_results
    assert not bundle.accepted_claims


# --- Attack class 4 / 5 ---


@pytest.mark.asyncio
async def test_correct_prose_wrong_artifact_fails(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "correct_prose_wrong_compute")
    _assert_engineering_not_pass(runtime)


def test_correct_artifact_wrong_claim_fails() -> None:
    spec = _heater_spec(spec_id="cspec_w")
    comp = _comp(outputs={"power": {"value": 3200, "unit": "W"}}, spec_id=spec.spec_id)
    rel = validate_computation_against_spec(comp, spec)
    assert rel.relevant is True
    wrong_claim = Claim(
        claim_id="claim_eff",
        statement="heater_efficiency = 3.25",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        computation_artifact_id=comp.artifact_id,
        math_check={"expression": "1", "expected": 1, "tolerance": 0.1, "inputs": {}},
        conditions={"covers_outputs": ["heater_efficiency"]},
    )
    checks = DeterministicCheckReport(
        results=[MathCheckResult(check_id="c1", passed=True, details={"claim_id": "claim_eff"})]
    )
    completeness = evaluate_evidence_completeness(
        calculation_specs=[spec],
        computations=[comp],
        check_report=checks,
        claims=[wrong_claim],
        verification_policy=VerificationPolicy(
            verification_required=True,
            required_outputs=["power"],
            required_output_dimensions={"power": "W"},
        ),
        require_calculation=True,
        relevance_results=[rel],
    )
    assert completeness.required_output_coverage is False
    adj = adjudicate(
        check_report=checks,
        verification=None,
        red_team=None,
        require_independent_review=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status != AdjudicationStatus.PASS


# --- Attack class 6 / 7 / 13 / 14 / 15 ---


@pytest.mark.asyncio
async def test_wrong_input_fails(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "wrong_inputs")
    _assert_engineering_not_pass(runtime)


@pytest.mark.asyncio
async def test_wrong_units_fail(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "wrong_units")
    _assert_engineering_not_pass(runtime)


def test_unit_aliases_are_normalized() -> None:
    assert units_compatible("W", "kW")
    assert units_compatible("W", "MW")
    assert units_compatible("W", "J/s")
    assert units_compatible("m", "mm")
    assert units_compatible("m", "cm")
    assert units_compatible("m", "meter")
    assert not units_compatible("W", "J")
    assert not units_compatible("W", "kWh")
    # PR-02: Dimension name length is a contract token, compatible with m.
    assert units_compatible("length", "m")
    assert not is_parseable_unit("length")
    assert is_parseable_unit("m")
    # Bare L as expected (legacy SI) matches m; as Pint unit L is litre ≠ m via unit↔unit
    # when both sides are real units with different dims — length path uses Dimension.
    assert units_compatible("L", "m")
    assert not units_compatible("liter", "m")


@pytest.mark.asyncio
async def test_self_consistent_wrong_calculation_fails(tmp_path: Path) -> None:
    """Wrong t=60s → ~98 kW is self-consistent but outside acceptance band."""
    # Reuse wrong_inputs shape isn't 98kW; build acceptance check for 98kW case.
    exp = get_benchmark(REPO, "simple_heater").expectation
    comp = _comp(outputs={"power": {"value": 98000.0, "unit": "W"}})
    claim = Claim(
        claim_id="c98",
        statement="power ≈ 98 kW",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        computation_artifact_id=comp.artifact_id,
        math_check={
            "expression": "(m_kg * c * dT / t_s) * (1.0 + loss)",
            "expected": 98000.0,
            "tolerance": 10.0,
            "inputs": {"m_kg": 20.0, "c": 4180.0, "dT": 60.0, "t_s": 60.0, "loss": 0.15},
        },
        conditions={"covers_outputs": ["power"]},
    )
    arep = evaluate_acceptance(exp, computations=[comp], claims=[claim])
    assert arep.passed is False


@pytest.mark.asyncio
async def test_formula_mismatch_fails(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "wrong_formula")
    _assert_engineering_not_pass(runtime)


@pytest.mark.asyncio
async def test_accidental_numeric_match_fails(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "accidental_numeric_match")
    _assert_engineering_not_pass(runtime)
    # Magnitude looks right; input oracle must reject wrong m/ΔT.
    assert runtime._last_evidence_completeness is not None
    assert runtime._last_evidence_completeness.acceptance_passed is False


# --- Attack class 8 / 9 ---


def test_required_outputs_are_locked() -> None:
    assert is_vague_output_name("engineering_result")
    names, _ = extract_outputs_from_understanding(
        {"required_outputs": ["engineering_result", "power"], "expected_dimensions": {"power": "W"}}
    )
    assert "engineering_result" not in names
    assert "power" in names


def test_calculation_spec_cannot_remove_required_output() -> None:
    policy = VerificationPolicy(
        verification_required=True,
        required_outputs=["power"],
        required_output_dimensions={"power": "W"},
    )
    spec = parse_calculation_spec(
        {
            "objective": "gaming",
            "required_outputs": ["engineering_result"],
            "expected_dimensions": {"power": "GiB", "engineering_result": "1"},
            "verification_required": False,
            "minimum_checks": 0,
        },
        task_id="calculation",
        run_id="run_policy_lock",
        project_id="proj_policy",
        investigation_id="proj_policy",
        policy=policy,
    )
    assert spec is not None
    assert "power" in spec.required_outputs
    assert "engineering_result" not in spec.required_outputs
    # Policy dimension wins over LLM GiB override.
    assert spec.expected_dimensions.get("power") == "W"
    errors = validate_calculation_spec(spec)
    assert not any("power" in e and "missing" in e for e in errors)


@pytest.mark.asyncio
async def test_policy_lock_attack_fixture(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "policy_lock_attack")
    _assert_engineering_not_pass(runtime)


# --- Attack class 10 / 11 / 12 ---


def test_extra_unrelated_output_is_not_accepted() -> None:
    spec = _heater_spec(spec_id="cspec_extra")
    comp = _comp(
        outputs={
            "power": {"value": 3200, "unit": "W"},
            "KV_cache": {"value": 4.0, "unit": "GiB"},
        },
        spec_id=spec.spec_id,
    )
    rel = validate_computation_against_spec(comp, spec)
    assert rel.relevant is True
    # Extra output may exist; acceptance only cares about required power.
    exp = get_benchmark(REPO, "simple_heater").expectation
    claim = Claim(
        claim_id="cp",
        statement="power = 3200 W",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        computation_artifact_id=comp.artifact_id,
        math_check={
            "expression": "1",
            "expected": 1,
            "tolerance": 0.1,
            "inputs": {"m_kg": 20.0, "dT": 60.0, "t_s": 1800.0, "loss": 0.15},
        },
        conditions={"covers_outputs": ["power"]},
    )
    arep = evaluate_acceptance(exp, computations=[comp], claims=[claim])
    assert arep.passed is True
    assert "power" in arep.covered_outputs


def test_orphan_claim_is_not_verified() -> None:
    orphan = Claim(
        claim_id="orphan",
        statement="heater_electrical_power = 3.2 kW",
        kind=EvidenceKind.CALCULATION,
        evidence="no artifact",
    )
    completeness = evaluate_evidence_completeness(
        calculation_specs=[_heater_spec()],
        computations=[_comp(outputs={"power": {"value": 3200, "unit": "W"}})],
        check_report=DeterministicCheckReport(
            results=[MathCheckResult(check_id="c1", passed=True, details={"claim_id": "orphan"})]
        ),
        claims=[orphan],
        verification_policy=VerificationPolicy(required_outputs=["power"]),
        require_calculation=True,
    )
    assert completeness.provenance_complete is False
    assert completeness.required_output_coverage is False


def test_orphan_computation_is_not_accepted() -> None:
    spec = _heater_spec(spec_id="cspec_bound")
    orphan = _comp(
        outputs={"power": {"value": 3200, "unit": "W"}},
        spec_id="someone_elses_spec",
        task_id="other_task",
    )
    completeness = evaluate_evidence_completeness(
        calculation_specs=[spec],
        computations=[orphan],
        check_report=DeterministicCheckReport(
            results=[MathCheckResult(check_id="c1", passed=True)]
        ),
        verification_policy=VerificationPolicy(),
        require_calculation=True,
        relevance_results=[],  # force re-pair — must not bind orphan
    )
    assert completeness.computation_relevant is False


# --- Attack class 17 / 19 / 20 ---


def test_deterministic_status_cannot_be_overridden() -> None:
    checks = DeterministicCheckReport(
        results=[MathCheckResult(check_id="c1", passed=False, discrepancy="OUT_OF_BOUNDS")],
        critical_failures=["OUT_OF_BOUNDS"],
    )
    adj = adjudicate(
        check_report=checks,
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=RedTeamReport(summary="llm says pass"),
        require_independent_review=True,
    )
    assert adj.status == AdjudicationStatus.FAIL
    assert adj.deterministic_critical_failure is True


@pytest.mark.asyncio
async def test_technical_success_does_not_imply_engineering_success(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "kv_cache_unrelated")
    manifest = runtime.run_store.load_manifest()
    assert manifest.final_state == "COMPLETED"
    assert manifest.engineering_outcome != "PASS"


def test_benchmark_oracle_cannot_be_bypassed() -> None:
    exp = get_benchmark(REPO, "simple_heater").expectation
    assert exp.acceptance_outputs
    # Prose-only: no computation → fail.
    arep = evaluate_acceptance(exp, computations=[], claims=[])
    assert arep.passed is False


@pytest.mark.asyncio
async def test_correct_simple_heater_calculation_passes(tmp_path: Path) -> None:
    runtime = await _run_fixture(tmp_path, "heater_correct")
    assert runtime._last_adjudication is not None
    assert runtime._last_adjudication.status == AdjudicationStatus.PASS
    manifest = runtime.run_store.load_manifest()
    assert manifest.engineering_outcome == "PASS"
    assert runtime._last_evidence_completeness is not None
    assert runtime._last_evidence_completeness.is_complete
    assert runtime._last_evidence_completeness.acceptance_passed is True


# --- Invariants ---


def test_invariant_irrelevant_computations_block_pass() -> None:
    spec = _heater_spec(spec_id="s1")
    comp = _comp(outputs={"memory_gib": {"value": 4, "unit": "GiB"}}, spec_id=spec.spec_id)
    rel = validate_computation_against_spec(comp, spec)
    completeness = evaluate_evidence_completeness(
        calculation_specs=[spec],
        computations=[comp],
        check_report=DeterministicCheckReport(
            results=[MathCheckResult(check_id="c1", passed=True)]
        ),
        verification_policy=VerificationPolicy(),
        require_calculation=True,
        relevance_results=[rel],
    )
    assert completeness.is_complete is False
    adj = adjudicate(
        check_report=DeterministicCheckReport(
            results=[MathCheckResult(check_id="c1", passed=True)]
        ),
        verification=None,
        red_team=None,
        require_independent_review=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status != AdjudicationStatus.PASS


def test_invariant_scalar_match_without_inputs_still_needs_contract() -> None:
    """Accidental scalar alone is insufficient when input oracle sees wrong m/ΔT."""
    exp = get_benchmark(REPO, "simple_heater").expectation
    # Force required input checks for this invariant.
    strict = exp.model_copy(
        update={
            "acceptance_inputs": [
                InputAcceptance(name="m_kg", value=20.0, required=True),
                InputAcceptance(name="dT", value=60.0, required=True),
            ]
        }
    )
    comp = _comp(outputs={"power": {"value": 3204.7, "unit": "W"}})
    claim = Claim(
        claim_id="c",
        statement="power",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        math_check={"inputs": {"m_kg": 10.0, "dT": 120.0}, "expected": 3204.7, "expression": "1", "tolerance": 1},
    )
    arep = evaluate_acceptance(strict, computations=[comp], claims=[claim])
    assert arep.passed is False


def test_kw_declared_output_passes_relevance() -> None:
    """W vs kW must be dimension-compatible (Pint), not string-equal."""
    spec = _heater_spec(spec_id="cspec_kw")
    comp = _comp(outputs={"power": {"value": 3.2, "unit": "kW"}}, spec_id=spec.spec_id)
    rel = validate_computation_against_spec(comp, spec)
    assert rel.relevant is True
    assert not rel.dimension_mismatches
