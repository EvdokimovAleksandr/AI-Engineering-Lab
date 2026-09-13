"""Deterministic engineering verification (Pint, AST eval, bounds, provenance)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from ai_lab.checks import run_deterministic_checks
from ai_lab.checks.safe_eval import ForbiddenExpressionError, SafeExpressionEvaluator
from ai_lab.checks.units import convert_to, parse_quantity
from ai_lab.checks.verifier import VERIFIER_VERSION, DeterministicVerifier
from ai_lab.core.enums import (
    AdjudicationStatus,
    CheckStatus,
    EvidenceKind,
    GraphEdgeType,
    GraphNodeType,
    VerificationStatus,
)
from ai_lab.core.models import (
    BlindClaimView,
    BoundsSpec,
    Claim,
    GraphEdge,
    GraphNode,
    LabConfig,
    Quantity,
    RedTeamReport,
    SanityCheck,
    ToleranceSpec,
    VerificationLimits,
    VerificationReport,
    VerificationResult,
    VerificationSpec,
    DeterministicCheckReport,
)
from ai_lab.knowledge.graph import JsonEvidenceRepository
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.adjudication import adjudicate
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime

REPO = Path(__file__).resolve().parents[1]


def _verifier(limits: VerificationLimits | None = None) -> DeterministicVerifier:
    return DeterministicVerifier(limits or VerificationLimits())


def test_units_mpa_equals_gpa() -> None:
    spec = VerificationSpec(
        actual=Quantity(value=100, unit="MPa"),
        expected=Quantity(value=0.1, unit="GPa"),
        tolerance=ToleranceSpec(absolute=Quantity(value=1e-12, unit="GPa")),
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.PASS
    assert result.normalized_actual is not None
    assert result.normalized_expected is not None


def test_units_metre_equals_centimetre() -> None:
    spec = VerificationSpec(
        inputs={"length": Quantity(value=1, unit="m")},
        expression="length",
        expected=Quantity(value=100, unit="cm"),
        tolerance=ToleranceSpec(relative=1e-12),
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.PASS


def test_incompatible_dimensions_mpa_vs_newton() -> None:
    spec = VerificationSpec(
        actual=Quantity(value=100, unit="MPa"),
        expected=Quantity(value=100, unit="N"),
        tolerance=ToleranceSpec(absolute=Quantity(value=1, unit="N")),
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.INCOMPATIBLE_DIMENSIONS
    assert result.passed is False


def test_pint_conversion_not_string_equality() -> None:
    """Registry conversion must succeed even when unit *strings* differ."""
    a = parse_quantity(100, "MPa")
    b = parse_quantity(0.1, "GPa")
    converted = convert_to(a, b)
    assert abs(float(converted.magnitude) - 0.1) < 1e-12


def test_numerical_pass_multiply() -> None:
    spec = VerificationSpec(
        expression="2 * 5",
        expected=Quantity(value=10, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    assert _verifier().verify(spec).status == CheckStatus.PASS


def test_numerical_fail_multiply() -> None:
    spec = VerificationSpec(
        expression="2 * 5",
        expected=Quantity(value=11, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.FAIL
    assert result.diagnostics


def test_absolute_tolerance_pass_and_fail() -> None:
    ok = VerificationSpec(
        actual=Quantity(value=100.0001, unit="MPa"),
        expected=Quantity(value=100, unit="MPa"),
        tolerance=ToleranceSpec(absolute=Quantity(value=0.001, unit="MPa")),
    )
    bad = VerificationSpec(
        actual=Quantity(value=110, unit="MPa"),
        expected=Quantity(value=100, unit="MPa"),
        tolerance=ToleranceSpec(absolute=Quantity(value=0.001, unit="MPa")),
    )
    assert _verifier().verify(ok).status == CheckStatus.PASS
    assert _verifier().verify(bad).status == CheckStatus.FAIL


def test_relative_tolerance_pass_and_fail() -> None:
    ok = VerificationSpec(
        expression="100.5",
        expected=Quantity(value=100, unit=""),
        tolerance=ToleranceSpec(relative=0.01),
    )
    bad = VerificationSpec(
        expression="100.5",
        expected=Quantity(value=100, unit=""),
        tolerance=ToleranceSpec(relative=0.001),
    )
    assert _verifier().verify(ok).status == CheckStatus.PASS
    assert _verifier().verify(bad).status == CheckStatus.FAIL


def test_bounds_temperature_pass_and_out_of_bounds() -> None:
    limits = BoundsSpec(
        minimum=Quantity(value=0, unit="degC"),
        maximum=Quantity(value=500, unit="degC"),
    )
    inside = VerificationSpec(
        actual=Quantity(value=25, unit="degC"),
        expected=Quantity(value=25, unit="degC"),
        tolerance=ToleranceSpec(absolute=Quantity(value=0.1, unit="degC")),
        bounds=limits,
    )
    hot = VerificationSpec(
        actual=Quantity(value=800, unit="degC"),
        expected=Quantity(value=800, unit="degC"),
        tolerance=ToleranceSpec(absolute=Quantity(value=0.1, unit="degC")),
        bounds=limits,
    )
    assert _verifier().verify(inside).status == CheckStatus.PASS
    assert _verifier().verify(hot).status == CheckStatus.OUT_OF_BOUNDS


def test_invalid_units() -> None:
    spec = VerificationSpec(
        actual=Quantity(value=1, unit="not_a_real_unit_xyz"),
        expected=Quantity(value=1, unit="m"),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="m")),
    )
    assert _verifier().verify(spec).status == CheckStatus.INVALID_INPUT


def test_invalid_expression() -> None:
    spec = VerificationSpec(
        expression="2 +",
        expected=Quantity(value=2, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    assert _verifier().verify(spec).status == CheckStatus.INVALID_INPUT


def test_division_by_zero() -> None:
    spec = VerificationSpec(
        expression="1 / 0",
        expected=Quantity(value=1, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    assert _verifier().verify(spec).status == CheckStatus.EVALUATION_ERROR


def test_forbidden_ast_node() -> None:
    spec = VerificationSpec(
        expression="[1, 2]",
        expected=Quantity(value=1, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.INVALID_INPUT
    joined = " ".join(result.diagnostics)
    assert "List" in joined or "Disallowed" in joined or "AST" in joined


def test_forbidden_import() -> None:
    spec = VerificationSpec(
        expression="__import__('os')",
        expected=Quantity(value=1, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.INVALID_INPUT
    ev = SafeExpressionEvaluator(max_ast_nodes=50, max_expression_chars=200)
    with pytest.raises(ForbiddenExpressionError, match="Forbidden"):
        ev.evaluate("__import__('os')", {})


def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = DeterministicVerifier(limits=VerificationLimits(max_computation_seconds=0.05))

    def _slow(_spec: VerificationSpec):
        time.sleep(1.0)
        raise AssertionError("pipeline should have been aborted by timeout")

    monkeypatch.setattr(verifier, "_verify_sync", _slow)
    spec = VerificationSpec(
        expression="2 * 5",
        expected=Quantity(value=10, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    result = verifier.verify(spec)
    assert result.status == CheckStatus.TIMEOUT


def test_sanity_check_extension_point() -> None:
    spec = VerificationSpec(
        expression="2 * 5",
        expected=Quantity(value=10, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
        sanity_checks=[
            SanityCheck(
                name="positive",
                condition="actual > 0",
                failure_message="result must be positive",
            ),
            SanityCheck(
                name="too_small",
                condition="actual > 100",
                failure_message="unphysically small",
            ),
        ],
    )
    result = _verifier().verify(spec)
    assert result.status == CheckStatus.FAIL
    assert "unphysically small" in result.diagnostics


def test_same_spec_is_deterministic() -> None:
    spec = VerificationSpec(
        inputs={"a": Quantity(value=3, unit=""), "b": Quantity(value=4, unit="")},
        expression="a * b + 2",
        expected=Quantity(value=14, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
    )
    v = _verifier()
    r1 = v.verify(spec)
    r2 = v.verify(spec)
    assert r1.status == r2.status == CheckStatus.PASS
    assert r1.provenance is not None and r2.provenance is not None
    assert r1.provenance.spec_hash == r2.provenance.spec_hash
    assert r1.provenance.verifier_version == VERIFIER_VERSION
    assert r1.actual is not None and r2.actual is not None
    assert r1.actual.value == r2.actual.value


def test_provenance_links_claim_spec_result() -> None:
    spec = VerificationSpec(
        claim_id="claim_demo",
        expression="2 * 5",
        expected=Quantity(value=10, unit=""),
        tolerance=ToleranceSpec(absolute=Quantity(value=0, unit="")),
        metadata={"source": "unit-test"},
    )
    result = _verifier().verify(spec)
    assert result.claim_id == "claim_demo"
    assert result.provenance is not None
    assert result.provenance.formula == "2 * 5"
    assert result.provenance.tolerance
    assert "IEEE-754" in result.provenance.numerical_policy
    assert result.provenance.verifier_version == VERIFIER_VERSION


@pytest.mark.asyncio
async def test_claim_math_check_feeds_verifier() -> None:
    claim = BlindClaimView(
        claim_id="c1",
        statement="2*5=10",
        kind=EvidenceKind.CALCULATION,
        math_check={"expression": "2 * 5", "expected": 10, "tolerance": 0, "inputs": {}},
    )
    report = await run_deterministic_checks([claim])
    assert report.verification_results
    assert report.verification_results[0].status == CheckStatus.PASS
    assert report.all_passed


@pytest.mark.asyncio
async def test_verification_spec_on_claim_incompatible() -> None:
    claim = BlindClaimView(
        claim_id="c2",
        statement="pressure vs force",
        kind=EvidenceKind.CALCULATION,
        verification_spec={
            "actual": {"value": 100, "unit": "MPa"},
            "expected": {"value": 100, "unit": "N"},
            "tolerance": {"absolute": {"value": 1, "unit": "N"}},
        },
    )
    report = await run_deterministic_checks([claim])
    assert report.has_critical_failure
    assert report.verification_results[0].status == CheckStatus.INCOMPATIBLE_DIMENSIONS


def test_adjudication_cannot_pass_incompatible_dimensions() -> None:
    vr = VerificationResult(
        spec_id="s1",
        status=CheckStatus.INCOMPATIBLE_DIMENSIONS,
        diagnostics=["Pa vs N"],
    )
    checks = DeterministicCheckReport(
        verification_results=[vr],
        critical_failures=["INCOMPATIBLE_DIMENSIONS"],
    )
    adj = adjudicate(
        check_report=checks,
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=RedTeamReport(summary="ok", recommended_reject=False),
    )
    assert adj.status == AdjudicationStatus.FAIL
    assert adj.deterministic_critical_failure is True


def test_check_graph_nodes_use_existing_types(tmp_path: Path) -> None:
    root = tmp_path / "g"
    root.mkdir()
    store = ProjectStore(root)
    store.ensure_layout()
    g = JsonEvidenceRepository(store)
    claim = g.add_node(
        GraphNode(
            node_type=GraphNodeType.CLAIM,
            project_id=store.name,
            ref_id="clm_v",
            run_id="r1",
        )
    )
    check = g.add_node(
        GraphNode(
            node_type=GraphNodeType.CHECK,
            project_id=store.name,
            ref_id="vres_1",
            run_id="r1",
        )
    )
    g.add_edge(
        GraphEdge(
            edge_type=GraphEdgeType.TESTS,
            source_id=check.node_id,
            target_id=claim.node_id,
            project_id=store.name,
            run_id="r1",
        )
    )
    g.add_edge(
        GraphEdge(
            edge_type=GraphEdgeType.VERIFIED_BY,
            source_id=claim.node_id,
            target_id=check.node_id,
            project_id=store.name,
            run_id="r1",
        )
    )
    assert g.validate_integrity() == []


@pytest.mark.asyncio
async def test_runtime_claim_verification_result_provenance(tmp_path: Path) -> None:
    """Claim → VerificationSpec → VerificationResult is recorded on the existing graph."""
    project_dir = tmp_path / "prov"
    project_dir.mkdir()
    store = ProjectStore(project_dir)
    store.ensure_layout()
    evidence = EvidenceStore(store, run_id="run_prov")
    claim = Claim(
        statement="100 MPa equals 0.1 GPa",
        kind=EvidenceKind.CALCULATION,
        evidence="unit conversion",
        run_id="run_prov",
        project_id="prov",
        verification_spec={
            "actual": {"value": 100, "unit": "MPa"},
            "expected": {"value": 0.1, "unit": "GPa"},
            "tolerance": {"absolute": {"value": 1e-9, "unit": "GPa"}},
        },
        investigation_id="prov",
        task_id="task_test",
    )
    evidence.save_claim(claim, subdirectory="calculations")
    config = LabConfig.model_validate(
        yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    )
    config.provider = "mock"
    runtime = LabRuntime(
        store,
        config,
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
        resume_run_id="run_prov",
    )
    await runtime._run_independent_review()
    assert runtime._last_check_report is not None
    assert runtime._last_check_report.all_passed
    nodes = runtime.graph.list_nodes(run_id="run_prov")
    check_nodes = [n for n in nodes if n.node_type == GraphNodeType.CHECK]
    assert check_nodes
    vr_id = runtime._last_check_report.verification_results[0].result_id
    artifact = project_dir / ".runs" / "run_prov" / "reviews" / "checks" / f"{vr_id}.json"
    assert artifact.is_file()
    edges = runtime.graph.list_edges(run_id="run_prov")
    assert any(e.edge_type == GraphEdgeType.VERIFIED_BY for e in edges)
    assert runtime._last_verification is not None
    assert runtime._last_verification.status == VerificationStatus.PASS
    assert "deterministic" in runtime._last_verification.recomputed


@pytest.mark.asyncio
async def test_verification_agent_cannot_override_fail(tmp_path: Path) -> None:
    project_dir = tmp_path / "ov"
    project_dir.mkdir()
    store = ProjectStore(project_dir)
    store.ensure_layout()
    evidence = EvidenceStore(store, run_id="run_ov")
    evidence.save_claim(
        Claim(
            statement="wrong",
            kind=EvidenceKind.CALCULATION,
            evidence="test",
            run_id="run_ov",
            project_id="ov",
            math_check={"expression": "2 * 5", "expected": 11, "tolerance": 0, "inputs": {}},
            investigation_id="ov",
            task_id="task_test",
        ),
        subdirectory="calculations",
    )
    config = LabConfig.model_validate(
        yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    )
    config.provider = "mock"
    runtime = LabRuntime(
        store,
        config,
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
        resume_run_id="run_ov",
    )
    status = await runtime._run_independent_review()
    assert runtime._last_check_report is not None
    assert runtime._last_check_report.has_critical_failure
    assert runtime._last_verification is not None
    assert runtime._last_verification.status == VerificationStatus.FAIL
    assert status == AdjudicationStatus.FAIL


def test_no_eval_in_safe_evaluator_source() -> None:
    """Guardrail: the AST interpreter must not call builtins eval/exec/compile."""
    src = (REPO / "src" / "ai_lab" / "checks" / "safe_eval.py").read_text(encoding="utf-8")
    stripped = src.replace("self._eval(", "").replace("_eval(", "")
    assert "eval(" not in stripped
    assert "exec(" not in stripped
    assert "compile(" not in stripped
