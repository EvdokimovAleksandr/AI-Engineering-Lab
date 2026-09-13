"""PR-01: ExecutionContext isolation — no cross-task / cross-investigation mixing."""

from __future__ import annotations

import pytest

from ai_lab.checks.calculation_contract import (
    parse_calculation_spec,
    validate_computation_against_spec,
)
from ai_lab.core.enums import EvidenceKind, LabErrorCode
from ai_lab.core.execution_context import (
    ContextMismatchError,
    ExecutionContext,
    attach_research_result_to_context,
    require_artifact_context,
    resume_execution_context,
)
from ai_lab.core.models import CalculationSpec, Claim, ComputationArtifact
from ai_lab.knowledge.models import ResearchResult
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore


def _sofa_ctx(*, task_id: str = "calculation") -> ExecutionContext:
    return ExecutionContext.for_project_run(
        project_id="investigation_sofa_height",
        investigation_id="investigation_sofa_height",
        task_id=task_id,
        run_id="run_sofa_001",
    )


def _rod_ctx(*, task_id: str = "calculation") -> ExecutionContext:
    return ExecutionContext.for_project_run(
        project_id="investigation_rod_stress",
        investigation_id="investigation_rod_stress",
        task_id=task_id,
        run_id="run_rod_001",
    )


def _sofa_spec(**overrides: object) -> CalculationSpec:
    data = {
        "spec_id": "cspec_sofa",
        "project_id": "investigation_sofa_height",
        "investigation_id": "investigation_sofa_height",
        "task_id": "calculation",
        "run_id": "run_sofa_001",
        "objective": "Estimate sofa seat height from anthropometry",
        "required_outputs": ["seat_height_m"],
        "expected_dimensions": {"seat_height_m": "m"},
    }
    data.update(overrides)
    return CalculationSpec.model_validate(data)


def _rod_spec(**overrides: object) -> CalculationSpec:
    data = {
        "spec_id": "cspec_rod",
        "project_id": "investigation_rod_stress",
        "investigation_id": "investigation_rod_stress",
        "task_id": "calculation",
        "run_id": "run_rod_001",
        "objective": "Compute axial stress in a loaded rod",
        "required_outputs": ["axial_stress_pa"],
        "expected_dimensions": {"axial_stress_pa": "Pa"},
    }
    data.update(overrides)
    return CalculationSpec.model_validate(data)


def test_calculation_spec_cannot_cross_task() -> None:
    """Sofa-height task must refuse a rod-stress CalculationSpec (hard CONTEXT_MISMATCH)."""
    sofa = _sofa_ctx(task_id="calculation")
    rod = _rod_spec(task_id="foreign_rod_task")

    with pytest.raises(ContextMismatchError) as ei:
        require_artifact_context(sofa, rod, where="adversarial.sofa+rod")
    assert ei.value.code == LabErrorCode.CONTEXT_MISMATCH
    assert "CONTEXT_MISMATCH" in str(ei.value)

    # parse_calculation_spec also refuses LLM proposals stamped with a foreign task_id.
    with pytest.raises(ContextMismatchError):
        parse_calculation_spec(
            {
                "objective": "rod stress",
                "required_outputs": ["axial_stress_pa"],
                "expected_dimensions": {"axial_stress_pa": "Pa"},
                "task_id": "rod_only",
            },
            execution_context=sofa,
        )


def test_claim_cannot_cross_investigation(tmp_path) -> None:
    """Spider-silk claim must not attach to a sofa-height investigation store."""
    root = tmp_path / "investigation_sofa_height"
    root.mkdir()
    (root / "problem.md").write_text("sofa height", encoding="utf-8")
    store = ProjectStore(root)
    evidence = EvidenceStore(store, run_id="run_sofa_001")

    leaked = Claim(
        statement="Spider silk toughness is ~180 MJ/m^3",
        kind=EvidenceKind.INFERENCE,
        project_id="projects_spider_silk_industrial",
        investigation_id="projects_spider_silk_industrial",
        task_id="research",
        run_id="run_silk_999",
    )
    with pytest.raises(ContextMismatchError) as ei:
        evidence.save_claim(leaked)
    assert ei.value.code == LabErrorCode.CONTEXT_MISMATCH
    assert ei.value.field in {"project_id", "investigation_id", "run_id"}


def test_research_result_cannot_attach_to_other_task() -> None:
    """ResearchResult bound to task A cannot attach to task B."""
    sofa_research = _sofa_ctx(task_id="research")
    foreign = ResearchResult(
        query="rod yield stress",
        project_id="investigation_sofa_height",
        investigation_id="investigation_sofa_height",
        task_id="calculation",  # wrong task for research attach
        run_id="run_sofa_001",
    )
    with pytest.raises(ContextMismatchError) as ei:
        attach_research_result_to_context(foreign, sofa_research, where="research.attach")
    assert ei.value.code == LabErrorCode.CONTEXT_MISMATCH
    assert ei.value.field == "task_id"

    # Matching task stamps successfully.
    ok = ResearchResult(query="sofa anthropometry", task_id="research")
    bound = attach_research_result_to_context(ok, sofa_research)
    assert bound.task_id == "research"
    assert bound.project_id == "investigation_sofa_height"
    assert bound.run_id == "run_sofa_001"


def test_resume_preserves_context() -> None:
    """--resume keeps project/run/investigation; task_id may advance to the next node."""
    prior = _sofa_ctx(task_id="understanding")
    resumed = resume_execution_context(
        project_id=prior.project_id,
        investigation_id=prior.investigation_id,
        run_id=prior.run_id,
        task_id="calculation",
        previous=prior,
    )
    assert resumed.project_id == prior.project_id
    assert resumed.investigation_id == prior.investigation_id
    assert resumed.run_id == prior.run_id
    assert resumed.task_id == "calculation"
    assert resumed.contract_version == prior.contract_version

    with pytest.raises(ContextMismatchError):
        resume_execution_context(
            project_id="investigation_rod_stress",
            investigation_id="investigation_rod_stress",
            run_id=prior.run_id,
            task_id="calculation",
            previous=prior,
        )


def test_sofa_and_rod_specs_cannot_mix_in_one_run() -> None:
    """Adversarial fixture: rod ComputationArtifact vs sofa CalculationSpec → CONTEXT_MISMATCH."""
    sofa_spec = _sofa_spec()
    rod_artifact = ComputationArtifact(
        artifact_id="comp_rod_leak",
        run_id="run_rod_001",
        project_id="investigation_rod_stress",
        investigation_id="investigation_rod_stress",
        task_id="calculation",
        declared_outputs={"axial_stress_pa": {"value": 1.2e8, "unit": "Pa"}},
    )
    with pytest.raises(ContextMismatchError) as ei:
        validate_computation_against_spec(rod_artifact, sofa_spec)
    assert ei.value.code == LabErrorCode.CONTEXT_MISMATCH

    # Same-context sofa artifact is allowed through the identity gate (may still be irrelevant).
    sofa_artifact = ComputationArtifact(
        artifact_id="comp_sofa",
        run_id="run_sofa_001",
        project_id="investigation_sofa_height",
        investigation_id="investigation_sofa_height",
        task_id="calculation",
        calculation_spec_id=sofa_spec.spec_id,
        declared_outputs={"seat_height_m": {"value": 0.45, "unit": "m"}},
    )
    rel = validate_computation_against_spec(sofa_artifact, sofa_spec)
    assert rel.relevant is True


def test_execution_context_is_frozen() -> None:
    ctx = _sofa_ctx()
    with pytest.raises(Exception):
        ctx.task_id = "hijacked"  # type: ignore[misc]
