"""PR-03: EngineeringContract lifecycle, pipeline gate, ExecutionContext stamp."""

from __future__ import annotations

import pytest

from ai_lab.checks.calculation_contract import parse_calculation_spec
from ai_lab.core.enums import ContractStatus, LabErrorCode, ScopeStatus
from ai_lab.core.engineering_contract import (
    ContractError,
    ContractObjective,
    EngineeringContract,
    active_contract_version,
    bump_version_for_material_change,
    contract_from_investigation_scope,
    lock_contract,
    mutate_contract,
    next_contract_version,
    require_pipeline_allowed,
    require_spec_matches_contract_version,
    transition_status,
)
from ai_lab.core.execution_context import (
    UNSET_CONTRACT_VERSION,
    ExecutionContext,
    resume_execution_context,
)
from ai_lab.orchestrator.scope import lock_scope, resolve_scope


def _draft(**overrides: object) -> EngineeringContract:
    data = {
        "project_id": "investigation_heater",
        "investigation_id": "investigation_heater",
        "run_id": "run_test_001",
        "version": "1",
        "status": ContractStatus.DRAFT,
        "objective": ContractObjective(statement="Calculate heater power"),
        "required_outputs": [{"name": "power", "dimension": "W"}],
    }
    data.update(overrides)
    return EngineeringContract.model_validate(data)


def test_cannot_enter_ready_without_objective() -> None:
    c = _draft(objective=ContractObjective(statement=""))
    with pytest.raises(ContractError) as ei:
        transition_status(c, ContractStatus.READY)
    assert ei.value.code == LabErrorCode.CONTRACT_NOT_READY
    assert "objective.statement" in str(ei.value)


def test_ready_then_lock_allows_pipeline() -> None:
    c = transition_status(_draft(), ContractStatus.READY)
    assert c.status == ContractStatus.READY
    locked = lock_contract(c)
    assert locked.status == ContractStatus.LOCKED
    require_pipeline_allowed(locked)
    require_pipeline_allowed(c)  # READY also allowed


def test_pipeline_refuses_draft_and_needs_clarification() -> None:
    draft = _draft()
    with pytest.raises(ContractError) as ei:
        require_pipeline_allowed(draft)
    assert ei.value.code == LabErrorCode.CONTRACT_NOT_READY

    clarifying = transition_status(draft, ContractStatus.NEEDS_CLARIFICATION)
    with pytest.raises(ContractError) as ei2:
        require_pipeline_allowed(clarifying)
    assert ei2.value.code == LabErrorCode.CONTRACT_NOT_READY

    with pytest.raises(ContractError) as ei3:
        require_pipeline_allowed(None)
    assert ei3.value.code == LabErrorCode.CONTRACT_NOT_READY


def test_locked_rejects_mutation_requires_version_bump() -> None:
    locked = lock_contract(transition_status(_draft(), ContractStatus.READY))
    with pytest.raises(ContractError) as ei:
        mutate_contract(
            locked,
            objective=ContractObjective(statement="Something else"),
        )
    assert ei.value.code == LabErrorCode.CONTRACT_LOCKED

    with pytest.raises(ContractError) as ei2:
        transition_status(locked, ContractStatus.READY)
    assert ei2.value.code == LabErrorCode.CONTRACT_LOCKED

    bumped = bump_version_for_material_change(
        locked,
        objective=ContractObjective(statement="Revised heater duty"),
    )
    assert bumped.version == "2"
    assert bumped.status == ContractStatus.READY
    assert bumped.objective.statement == "Revised heater duty"
    # Old locked instance unchanged (frozen).
    assert locked.version == "1"
    assert locked.status == ContractStatus.LOCKED


def test_next_contract_version_formats() -> None:
    assert next_contract_version("1") == "2"
    assert next_contract_version("v3") == "v4"
    with pytest.raises(ContractError):
        next_contract_version(UNSET_CONTRACT_VERSION)


def test_contract_from_scope_heater_ready_then_lock() -> None:
    scope = resolve_scope(
        "Calculate heater power for 20 L from 20°C to 80°C in 30 min."
    )
    assert scope.status == ScopeStatus.SCOPE_RESOLVED
    scope = lock_scope(scope)
    contract = contract_from_investigation_scope(
        scope,
        project_id="investigation_heater",
        investigation_id="investigation_heater",
        run_id="run_h1",
    )
    assert contract.status == ContractStatus.READY
    assert "power" in [o.name for o in contract.required_outputs]
    locked = lock_contract(contract)
    assert locked.status == ContractStatus.LOCKED
    assert active_contract_version(locked) == "1"
    assert active_contract_version(None) == UNSET_CONTRACT_VERSION


def test_contract_from_ambiguous_scope_is_needs_clarification() -> None:
    scope = resolve_scope("Насколько прочен этот материал?")
    assert scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION
    contract = contract_from_investigation_scope(
        scope,
        project_id="investigation_mat",
        investigation_id="investigation_mat",
        run_id="run_m1",
    )
    assert contract.status == ContractStatus.NEEDS_CLARIFICATION
    with pytest.raises(ContractError) as ei:
        require_pipeline_allowed(contract)
    assert ei.value.code == LabErrorCode.CONTRACT_NOT_READY


def test_execution_context_stamps_contract_version() -> None:
    locked = lock_contract(transition_status(_draft(), ContractStatus.READY))
    ctx = ExecutionContext.for_project_run(
        project_id=locked.project_id,
        investigation_id=locked.investigation_id,
        task_id="calculation",
        run_id=locked.run_id or "run_test_001",
        contract_version=active_contract_version(locked),
    )
    assert ctx.contract_version == "1"
    assert ctx.contract_version != UNSET_CONTRACT_VERSION

    resumed = resume_execution_context(
        project_id=ctx.project_id,
        investigation_id=ctx.investigation_id,
        task_id="simulation",
        run_id=ctx.run_id,
        contract_version=active_contract_version(locked),
        previous=ctx,
    )
    assert resumed.contract_version == "1"


def test_calculation_spec_gets_contract_version_from_context() -> None:
    locked = lock_contract(transition_status(_draft(), ContractStatus.READY))
    exec_ctx = ExecutionContext.for_project_run(
        project_id=locked.project_id,
        investigation_id=locked.investigation_id,
        task_id="calculation",
        run_id=locked.run_id or "run_test_001",
        contract_version=active_contract_version(locked),
    )
    spec = parse_calculation_spec(
        {
            "objective": "heater power",
            "required_outputs": ["power"],
            "expected_dimensions": {"power": "W"},
        },
        execution_context=exec_ctx,
    )
    assert spec is not None
    assert spec.contract_version == "1"
    require_spec_matches_contract_version(
        active_version="1",
        spec_contract_version=spec.contract_version,
        hard=True,
    )
    with pytest.raises(ContractError) as ei:
        require_spec_matches_contract_version(
            active_version="1",
            spec_contract_version="99",
            hard=True,
        )
    assert ei.value.code == LabErrorCode.CONTEXT_MISMATCH


def test_illegal_transition_fails_loud() -> None:
    c = _draft()
    with pytest.raises(ContractError) as ei:
        transition_status(c, ContractStatus.LOCKED)
    assert ei.value.code == LabErrorCode.ILLEGAL_CONTRACT_TRANSITION
