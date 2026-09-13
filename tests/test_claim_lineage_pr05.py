"""PR-05: Claim/Evidence lineage, support_status gates, coverage verifiers."""

from __future__ import annotations

import pytest

from ai_lab.checks.calculation_contract import evaluate_evidence_completeness
from ai_lab.checks.lineage_coverage import (
    attach_evidence_to_claim,
    evaluate_lineage_and_contract_coverage,
    require_evidence_type_refs,
    set_claim_support_status,
)
from ai_lab.core.enums import (
    AdjudicationStatus,
    ClaimSupportStatus,
    EvidenceKind,
    EvidenceType,
    VerificationStatus,
)
from ai_lab.core.execution_context import ContextMismatchError, ExecutionContext
from ai_lab.core.models import (
    AdjudicationResult,
    Claim,
    ComputationArtifact,
    DeterministicCheckReport,
    MathCheckResult,
    RedTeamReport,
    VerificationPolicy,
    VerificationReport,
)
from ai_lab.knowledge.models import EvidenceRecord
from ai_lab.orchestrator import synthesis as synth_mod
from ai_lab.orchestrator.adjudication import adjudicate


def test_claim_without_evidence_cannot_become_supported() -> None:
    claim = Claim(
        statement="stress ratio is 0.25",
        kind=EvidenceKind.INFERENCE,
    )
    assert claim.support_status == ClaimSupportStatus.PROPOSED
    with pytest.raises(ValueError, match="without evidence cannot become SUPPORTED"):
        set_claim_support_status(claim, ClaimSupportStatus.SUPPORTED)
    with pytest.raises(ValueError, match="without evidence cannot become SUPPORTED"):
        Claim(
            statement="bare assertion",
            kind=EvidenceKind.INFERENCE,
            support_status=ClaimSupportStatus.SUPPORTED,
        )


def test_claim_with_evidence_ids_can_become_supported() -> None:
    claim = Claim(
        statement="retrieved fact",
        kind=EvidenceKind.INFERENCE,
        source="https://example.org/paper",
        evidence="excerpt",
        evidence_ids=["ev_1"],
    )
    elevated = set_claim_support_status(claim, ClaimSupportStatus.SUPPORTED)
    assert elevated.support_status == ClaimSupportStatus.SUPPORTED


def test_wrong_investigation_cannot_attach_evidence() -> None:
    claim = Claim(
        statement="sofa height",
        kind=EvidenceKind.CALCULATION,
        investigation_id="investigation_sofa",
        project_id="investigation_sofa",
        run_id="run_sofa",
        computation_artifact_id="comp_sofa",
        evidence="e",
    )
    foreign = EvidenceRecord(
        evidence_id="ev_rod",
        source_id="src_rod",
        text="rod stress",
        content_hash="abc",
        evidence_type=EvidenceType.LITERATURE,
        investigation_id="investigation_rod",
        project_id="investigation_rod",
        run_id="run_rod",
    )
    with pytest.raises(ContextMismatchError) as ei:
        attach_evidence_to_claim(claim, foreign)
    assert ei.value.code.value == "CONTEXT_MISMATCH"
    assert "investigation_id" in str(ei.value)


def test_calculated_evidence_requires_computation_ref() -> None:
    with pytest.raises(ValueError, match="computation_artifact_id"):
        EvidenceRecord(
            evidence_id="ev_calc",
            source_id="src_x",
            text="numeric",
            content_hash="h",
            evidence_type=EvidenceType.CALCULATED,
        )
    ok = EvidenceRecord(
        evidence_id="ev_calc",
        source_id="src_x",
        text="numeric",
        content_hash="h",
        evidence_type=EvidenceType.CALCULATED,
        computation_artifact_id="comp_1",
    )
    require_evidence_type_refs(ok)


def test_literature_evidence_requires_source() -> None:
    with pytest.raises(ValueError, match="source_id"):
        EvidenceRecord(
            evidence_id="ev_lit",
            source_id="",
            text="quote",
            content_hash="h",
            evidence_type=EvidenceType.LITERATURE,
        )


def test_contract_outputs_without_evidence_coverage_blocks_adjudication_pass() -> None:
    """Required contract outputs without claim+evidence → INSUFFICIENT_EVIDENCE."""
    claim = Claim(
        statement="unrelated narrative",
        kind=EvidenceKind.INFERENCE,
        investigation_id="investigation_rod",
        project_id="investigation_rod",
        run_id="run_1",
        # PROPOSED without lineage — cannot cover stress_ratio
    )
    checks = DeterministicCheckReport(
        results=[MathCheckResult(check_id="c1", passed=True, details={"claim_id": "x"})]
    )
    completeness = evaluate_evidence_completeness(
        calculation_specs=[],
        computations=[],
        check_report=checks,
        claims=[claim],
        verification_policy=VerificationPolicy(
            verification_required=True,
            calculation_required=False,
            minimum_checks=1,
        ),
        require_calculation=False,
        required_contract_outputs=["stress_ratio"],
    )
    assert completeness.contract_coverage_ok is False
    assert completeness.is_complete is False
    assert completeness.coverage_ratio == 0.0
    adj = adjudicate(
        check_report=checks,
        verification=None,
        red_team=None,
        require_independent_review=False,
        require_red_team=False,
        evidence_completeness=completeness,
        verification_required=True,
    )
    assert adj.status == AdjudicationStatus.INSUFFICIENT_EVIDENCE


def test_contract_output_covered_by_supported_claim_with_lineage() -> None:
    claim = Claim(
        statement="stress_ratio equals 0.25",
        kind=EvidenceKind.CALCULATION,
        investigation_id="investigation_rod",
        project_id="investigation_rod",
        run_id="run_1",
        computation_artifact_id="comp_1",
        evidence="sandbox",
        support_status=ClaimSupportStatus.SUPPORTED,
        conditions={"covers_outputs": ["stress_ratio"]},
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    art = ComputationArtifact(
        artifact_id="comp_1",
        run_id="run_1",
        project_id="investigation_rod",
        investigation_id="investigation_rod",
        declared_outputs={"stress_ratio": 0.25},
    )
    report = evaluate_lineage_and_contract_coverage(
        claims=[claim],
        computations=[art],
        required_outputs=["stress_ratio"],
    )
    assert report.contract_coverage_ok is True
    assert report.coverage_ratio == 1.0
    assert report.lineage_ok is True


def test_synthesis_rejects_proposed_without_verified_lineage() -> None:
    bare = Claim(
        statement="power is 99 kW",
        kind=EvidenceKind.CALCULATION,
        evidence="prose only",
        support_status=ClaimSupportStatus.PROPOSED,
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    unverified = Claim(
        statement="power is 3.2 kW",
        kind=EvidenceKind.CALCULATION,
        computation_artifact_id="comp_1",
        evidence="e",
        support_status=ClaimSupportStatus.UNVERIFIED,
        math_check={"expression": "1", "expected": 1.0, "tolerance": 0.1, "inputs": {}},
    )
    checks = DeterministicCheckReport(
        results=[
            MathCheckResult(check_id="c1", passed=True, details={"claim_id": bare.claim_id}),
            MathCheckResult(
                check_id="c2", passed=True, details={"claim_id": unverified.claim_id}
            ),
        ]
    )
    bundle = synth_mod.build_synthesis_bundle(
        claims=[bare, unverified],
        verification=VerificationReport(status=VerificationStatus.PASS),
        red_team=RedTeamReport(summary="ok"),
        decisions=[],
        adjudication=AdjudicationResult(status=AdjudicationStatus.PASS, reasons=["ok"]),
        check_report=checks,
    )
    assert bundle.accepted_claims == []
    assert any("PROPOSED" in c or "UNVERIFIED" in c for c in bundle.caveats)


def test_lineage_verifier_flags_cross_run_computation() -> None:
    claim = Claim(
        statement="stress_ratio",
        kind=EvidenceKind.CALCULATION,
        investigation_id="investigation_rod",
        project_id="investigation_rod",
        run_id="run_a",
        computation_artifact_id="comp_x",
        evidence="e",
        conditions={"covers_outputs": ["stress_ratio"]},
    )
    foreign_comp = ComputationArtifact(
        artifact_id="comp_x",
        run_id="run_b",
        project_id="investigation_rod",
        investigation_id="investigation_rod",
        declared_outputs={"stress_ratio": 0.25},
    )
    report = evaluate_lineage_and_contract_coverage(
        claims=[claim],
        computations=[foreign_comp],
        required_outputs=["stress_ratio"],
    )
    assert report.lineage_ok is False
    assert any("CONTEXT_MISMATCH" in r or "run_id" in r for r in report.reasons)


def test_attach_evidence_same_investigation_ok() -> None:
    ctx = ExecutionContext.for_project_run(
        project_id="investigation_rod",
        investigation_id="investigation_rod",
        task_id="research",
        run_id="run_1",
        contract_version="1",
    )
    claim = Claim(
        statement="method X",
        kind=EvidenceKind.INFERENCE,
        project_id="investigation_rod",
        investigation_id="investigation_rod",
        run_id="run_1",
        task_id="research",
        contract_version="1",
    )
    ev = EvidenceRecord(
        evidence_id="ev_1",
        source_id="src_1",
        text="excerpt",
        content_hash="h",
        evidence_type=EvidenceType.LITERATURE,
        project_id="investigation_rod",
        investigation_id="investigation_rod",
        run_id="run_1",
        task_id="research",
        contract_version="1",
    )
    linked = attach_evidence_to_claim(claim, ev, expected=ctx)
    assert "ev_1" in linked.evidence_ids
    elevated = set_claim_support_status(linked, ClaimSupportStatus.SUPPORTED)
    assert elevated.support_status == ClaimSupportStatus.SUPPORTED
