"""EngineeringContract — source of truth for what the investigation must establish.

PR-03: not a graph node. Evolves from InvestigationScope (V2.8 scope gate / HITL),
then stamps ExecutionContext.contract_version. LOCKED is immutable for the run;
material change requires a new version string.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_lab.core.enums import ContractStatus, LabErrorCode, ProblemKind, ScopeStatus
from ai_lab.core.execution_context import UNSET_CONTRACT_VERSION
from ai_lab.core.investigation import InvestigationScope, TypedAssumption
from ai_lab.core.models import TaskGraph, TaskSpec


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# Pipeline (research / calculation / simulation / TaskGraph execution) may start
# only when status ∈ ALLOWED_PIPELINE_STATUSES. READY ≠ “everything known”.
ALLOWED_PIPELINE_STATUSES: frozenset[ContractStatus] = frozenset(
    {ContractStatus.READY, ContractStatus.LOCKED}
)

# Legal status edges — anything else fails loud (no silent repair).
_ALLOWED_TRANSITIONS: dict[ContractStatus, frozenset[ContractStatus]] = {
    ContractStatus.DRAFT: frozenset(
        {ContractStatus.NEEDS_CLARIFICATION, ContractStatus.READY}
    ),
    ContractStatus.NEEDS_CLARIFICATION: frozenset(
        {ContractStatus.DRAFT, ContractStatus.READY, ContractStatus.NEEDS_CLARIFICATION}
    ),
    ContractStatus.READY: frozenset(
        {ContractStatus.LOCKED, ContractStatus.NEEDS_CLARIFICATION, ContractStatus.DRAFT}
    ),
    # LOCKED has no in-place transitions; use bump_version_for_material_change.
    ContractStatus.LOCKED: frozenset(),
}


class ContractError(RuntimeError):
    """Hard contract gate failure — never silently weaken or remapped."""

    code: LabErrorCode

    def __init__(self, message: str, *, code: LabErrorCode, where: str | None = None) -> None:
        self.code = code
        self.where = where
        parts = [f"{code.value}: {message}"]
        if where:
            parts.append(f"where={where}")
        super().__init__(" | ".join(parts))


class ContractObjective(BaseModel):
    """What we are trying to establish (immutable statement once LOCKED)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = ""


class ContractScopeBoundary(BaseModel):
    """Optional system cut — upstream/downstream of the modelled boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    upstream: list[str] = Field(default_factory=list)
    downstream: list[str] = Field(default_factory=list)


class ContractScope(BaseModel):
    """Domain / system framing. Not a second HITL questionnaire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str | None = None
    system: str | None = None
    boundary: ContractScopeBoundary = Field(default_factory=ContractScopeBoundary)


class RequiredOutputSpec(BaseModel):
    """Named quantitative (or structured) output with optional dimension token."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    # Dimension name (length), legacy SI (L), or Pint unit (W) — same as expected_dimensions.
    dimension: str | None = None


class EngineeringContract(BaseModel):
    """Immutable-after-LOCKED investigation contract (source of truth).

    Binding ids align with ExecutionContext (PR-01). ``version`` feeds
    ``ExecutionContext.contract_version`` when the contract is active.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_id: str = Field(default_factory=lambda: _new_id("econ"))
    version: str = "1"
    status: ContractStatus = ContractStatus.DRAFT

    project_id: str
    investigation_id: str
    run_id: str | None = None

    objective: ContractObjective = Field(default_factory=ContractObjective)
    scope: ContractScope = Field(default_factory=ContractScope)

    required_outputs: list[RequiredOutputSpec] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)
    # EvidenceKind value strings (FACT, CALCULATION, …) — kinds we must obtain.
    required_evidence_kinds: list[str] = Field(default_factory=list)

    constraints: list[str] = Field(default_factory=list)
    assumptions: list[TypedAssumption] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    # PR-04: ScopeResolver taxonomy (authority = heuristic/policy, not LLM alone).
    problem_kind: ProblemKind | None = None
    # Structured clarification frame (mirrors InvestigationScope; HITL only for Required).
    known: dict[str, str] = Field(default_factory=dict)
    unknown: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    assumption_candidates: list[str] = Field(default_factory=list)

    # Provenance back to V2.8 scope gate (same HITL flow).
    scope_id: str | None = None
    pipeline_hint: str | None = None
    original_problem: str = ""

    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("version")
    @classmethod
    def _version_not_blank(cls, v: str) -> str:
        if not (v or "").strip() or (v or "").strip() == UNSET_CONTRACT_VERSION:
            raise ValueError(
                "EngineeringContract.version must be a real version string, "
                f"not blank or {UNSET_CONTRACT_VERSION!r}"
            )
        return v.strip()

    @field_validator("project_id", "investigation_id")
    @classmethod
    def _ids_required(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("project_id and investigation_id are required")
        return v.strip()

    def public_dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def missing_ready_fields(self) -> list[str]:
        """Minimum fields for READY — “defined enough”, not complete knowledge.

        required_outputs / evidence kinds enrich the contract but are not mandatory
        for READY (research and mixed problems may only name an objective).
        OPEN_ENDED with non-empty Required still blocks READY.
        """
        missing: list[str] = []
        if not (self.objective.statement or "").strip():
            missing.append("objective.statement")
        if not (self.project_id or "").strip():
            missing.append("project_id")
        if not (self.investigation_id or "").strip():
            missing.append("investigation_id")
        if not (self.version or "").strip():
            missing.append("version")
        # ScopeResolver Required: без них OPEN_ENDED / clarifying contract ≠ READY.
        for field_name in self.required:
            if field_name not in self.known or not str(self.known.get(field_name) or "").strip():
                missing.append(f"required.{field_name}")
        return missing

    def is_ready_minimum_met(self) -> bool:
        return not self.missing_ready_fields()


def next_contract_version(current: str) -> str:
    """Bump version string: ``1``→``2``, ``v3``→``v4``. Fail loud on junk."""
    raw = (current or "").strip()
    if not raw or raw == UNSET_CONTRACT_VERSION:
        raise ContractError(
            f"Cannot bump invalid contract version {current!r}",
            code=LabErrorCode.ILLEGAL_CONTRACT_TRANSITION,
            where="next_contract_version",
        )
    m = re.fullmatch(r"(?P<pre>v)?(?P<n>\d+)", raw, re.IGNORECASE)
    if not m:
        raise ContractError(
            f"Unsupported contract version format {current!r}; expected N or vN",
            code=LabErrorCode.ILLEGAL_CONTRACT_TRANSITION,
            where="next_contract_version",
        )
    n = int(m.group("n")) + 1
    return f"v{n}" if m.group("pre") else str(n)


def transition_status(
    contract: EngineeringContract,
    new_status: ContractStatus,
    *,
    where: str = "transition_status",
) -> EngineeringContract:
    """Apply a legal status edge; LOCKED cannot transition in place."""
    if contract.status == ContractStatus.LOCKED:
        raise ContractError(
            "LOCKED contract cannot change status in place; bump version instead",
            code=LabErrorCode.CONTRACT_LOCKED,
            where=where,
        )
    allowed = _ALLOWED_TRANSITIONS.get(contract.status, frozenset())
    if new_status not in allowed:
        raise ContractError(
            f"Illegal transition {contract.status.value} → {new_status.value}",
            code=LabErrorCode.ILLEGAL_CONTRACT_TRANSITION,
            where=where,
        )
    if new_status == ContractStatus.READY and not contract.is_ready_minimum_met():
        raise ContractError(
            "Cannot enter READY without minimum fields: "
            + ", ".join(contract.missing_ready_fields()),
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    return contract.model_copy(
        update={"status": new_status, "updated_at": _utc_now()}
    )


def lock_contract(
    contract: EngineeringContract,
    *,
    where: str = "lock_contract",
) -> EngineeringContract:
    """READY → LOCKED for the run. Refuses incomplete or already-locked contracts."""
    if contract.status == ContractStatus.LOCKED:
        return contract
    if contract.status != ContractStatus.READY:
        raise ContractError(
            f"Only READY contracts can lock (got {contract.status.value})",
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    if not contract.is_ready_minimum_met():
        raise ContractError(
            "Cannot LOCK incomplete contract: " + ", ".join(contract.missing_ready_fields()),
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    return contract.model_copy(
        update={"status": ContractStatus.LOCKED, "updated_at": _utc_now()}
    )


def mutate_contract(
    contract: EngineeringContract,
    *,
    where: str = "mutate_contract",
    **updates: Any,
) -> EngineeringContract:
    """In-place field updates. LOCKED → fail loud (no silent rewrite)."""
    if contract.status == ContractStatus.LOCKED:
        raise ContractError(
            "LOCKED EngineeringContract rejects mutation; use bump_version_for_material_change",
            code=LabErrorCode.CONTRACT_LOCKED,
            where=where,
        )
    if "status" in updates:
        raise ContractError(
            "Use transition_status / lock_contract for status changes",
            code=LabErrorCode.ILLEGAL_CONTRACT_TRANSITION,
            where=where,
        )
    if "version" in updates:
        raise ContractError(
            "Use bump_version_for_material_change to change version",
            code=LabErrorCode.ILLEGAL_CONTRACT_TRANSITION,
            where=where,
        )
    return contract.model_copy(update={**updates, "updated_at": _utc_now()})


def bump_version_for_material_change(
    contract: EngineeringContract,
    *,
    where: str = "bump_version_for_material_change",
    **updates: Any,
) -> EngineeringContract:
    """Material change on LOCKED (or any) → new version at READY, never mutate LOCKED."""
    forbidden = {"contract_id", "version", "status"}
    bad = forbidden.intersection(updates)
    if bad:
        raise ContractError(
            f"Cannot override {sorted(bad)} via material change kwargs",
            code=LabErrorCode.ILLEGAL_CONTRACT_TRANSITION,
            where=where,
        )
    merged = contract.model_dump(mode="python")
    merged.update(updates)
    merged["version"] = next_contract_version(contract.version)
    merged["status"] = ContractStatus.READY
    merged["updated_at"] = _utc_now()
    # Nested models may arrive as dicts from dump — re-validate.
    candidate = EngineeringContract.model_validate(merged)
    if not candidate.is_ready_minimum_met():
        raise ContractError(
            "Bumped contract fails READY minimum: "
            + ", ".join(candidate.missing_ready_fields()),
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    return candidate


def require_pipeline_allowed(
    contract: EngineeringContract | None,
    *,
    where: str = "pipeline",
) -> EngineeringContract:
    """Fail loud if research/calculation/simulation must not start yet."""
    if contract is None:
        raise ContractError(
            "EngineeringContract is required before pipeline stages",
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    if contract.status not in ALLOWED_PIPELINE_STATUSES:
        raise ContractError(
            f"Pipeline refused: contract status is {contract.status.value} "
            f"(need READY or LOCKED)",
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    if not contract.is_ready_minimum_met():
        raise ContractError(
            "Pipeline refused: contract missing "
            + ", ".join(contract.missing_ready_fields()),
            code=LabErrorCode.CONTRACT_NOT_READY,
            where=where,
        )
    return contract


def require_spec_matches_contract_version(
    *,
    active_version: str,
    spec_contract_version: str | None,
    where: str = "CalculationSpec",
    hard: bool = True,
) -> None:
    """When a spec stamps contract_version, it must match the active contract.

    Soft mode (hard=False): only warn via exception if both set and disagree —
    we still raise (fail loud); soft means *unset* on the spec is allowed.
    """
    if spec_contract_version is None or spec_contract_version == "":
        if hard and active_version and active_version != UNSET_CONTRACT_VERSION:
            # Soft default: legacy specs without stamp are allowed; hard=True
            # only enforces mismatch when the stamp is present. Documented rule:
            # unset on spec is OK; wrong stamp is never OK.
            return
        return
    if spec_contract_version != active_version:
        raise ContractError(
            f"contract_version mismatch: active={active_version!r} "
            f"spec={spec_contract_version!r}",
            code=LabErrorCode.CONTEXT_MISMATCH,
            where=where,
        )


def contract_from_investigation_scope(
    scope: InvestigationScope,
    *,
    project_id: str,
    investigation_id: str,
    run_id: str | None,
    version: str = "1",
    contract_id: str | None = None,
) -> EngineeringContract:
    """Map V2.8 InvestigationScope → EngineeringContract (same HITL, no parallel Qs)."""
    if not project_id or not investigation_id:
        raise ContractError(
            "project_id and investigation_id required to build EngineeringContract",
            code=LabErrorCode.CONTRACT_NOT_READY,
            where="contract_from_investigation_scope",
        )

    outputs = [
        RequiredOutputSpec(
            name=name,
            dimension=(scope.expected_dimensions or {}).get(name),
        )
        for name in (scope.required_outputs or [])
    ]
    evidence_kinds: list[str] = []
    if scope.evidence_requirements:
        # Research topics imply we need FACT-level literature evidence (kinds, not topics).
        evidence_kinds.append("FACT")
    if scope.pipeline_hint == "calculation" or outputs:
        evidence_kinds.append("CALCULATION")
    # Stable unique order.
    evidence_kinds = list(dict.fromkeys(evidence_kinds))

    open_qs = list(scope.ambiguity or []) + list(scope.unknown_parameters or [])
    if scope.clarification is not None:
        open_qs.append(scope.clarification.question)

    # Known + answers already applied to known_parameters; Required still missing → clarify.
    known = dict(scope.known_parameters or {})
    required = list(scope.required_fields or [])
    if scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION:
        status = ContractStatus.NEEDS_CLARIFICATION
    elif scope.status == ScopeStatus.SCOPE_UNRESOLVED:
        status = ContractStatus.DRAFT
    elif scope.status in {ScopeStatus.SCOPE_RESOLVED, ScopeStatus.SCOPE_ASSUMED}:
        status = ContractStatus.READY
    else:
        status = ContractStatus.DRAFT

    # OPEN_ENDED with unmet Required cannot be READY even if scope status drifted.
    unmet_required = [
        r for r in required if r not in known or not str(known.get(r) or "").strip()
    ]
    if unmet_required and status == ContractStatus.READY:
        status = ContractStatus.NEEDS_CLARIFICATION

    contract = EngineeringContract(
        contract_id=contract_id or _new_id("econ"),
        version=version,
        status=ContractStatus.DRAFT,  # set via transition after construction
        project_id=project_id,
        investigation_id=investigation_id,
        run_id=run_id,
        objective=ContractObjective(statement=(scope.objective or "").strip()),
        scope=ContractScope(
            domain=scope.domain,
            system=None,
            boundary=ContractScopeBoundary(
                upstream=[],
                downstream=list(scope.out_of_scope or []),
            ),
        ),
        required_outputs=outputs,
        success_metrics=list(scope.success_criteria or []),
        required_evidence_kinds=evidence_kinds,
        constraints=list(scope.constraints or []),
        assumptions=list(scope.assumptions or []),
        open_questions=open_qs,
        problem_kind=scope.problem_kind,
        known=known,
        unknown=list(scope.unknown_parameters or []),
        required=required,
        optional=list(scope.optional_fields or []),
        assumption_candidates=list(scope.assumption_candidates or []),
        scope_id=scope.scope_id,
        pipeline_hint=scope.pipeline_hint,
        original_problem=scope.original_problem,
    )

    # Apply target status with READY validation (fail loud if resolved but empty).
    if status == ContractStatus.READY:
        if not contract.is_ready_minimum_met():
            # Scope said resolved but contract shell is empty — stay DRAFT loudly
            # only when we cannot justify READY; research-with-objective still OK.
            raise ContractError(
                "Scope is RESOLVED/ASSUMED but contract fails READY minimum: "
                + ", ".join(contract.missing_ready_fields()),
                code=LabErrorCode.CONTRACT_NOT_READY,
                where="contract_from_investigation_scope",
            )
        return contract.model_copy(
            update={"status": ContractStatus.READY, "updated_at": _utc_now()}
        )
    if status == ContractStatus.NEEDS_CLARIFICATION:
        return contract.model_copy(
            update={
                "status": ContractStatus.NEEDS_CLARIFICATION,
                "updated_at": _utc_now(),
            }
        )
    return contract.model_copy(
        update={"status": ContractStatus.DRAFT, "updated_at": _utc_now()}
    )


def active_contract_version(contract: EngineeringContract | None) -> str:
    """Value for ExecutionContext.contract_version — unset only if no contract."""
    if contract is None:
        return UNSET_CONTRACT_VERSION
    return contract.version


def stamp_task_graph_to_contract(
    graph: TaskGraph,
    contract: EngineeringContract,
) -> TaskGraph:
    """Bind TaskGraph + each task to EngineeringContract (TaskGraph = f(contract)).

    Stamps contract_version, investigation_id, and where practical required_outputs /
    acceptance (success_metrics). Mismatched pre-existing stamps fail loud.
    """
    if contract.status not in ALLOWED_PIPELINE_STATUSES:
        raise ContractError(
            f"Cannot stamp TaskGraph onto contract status={contract.status.value}",
            code=LabErrorCode.CONTRACT_NOT_READY,
            where="stamp_task_graph_to_contract",
        )
    cv = contract.version
    acceptance = list(contract.success_metrics)
    output_names = [o.name for o in contract.required_outputs]

    meta = dict(graph.metadata or {})
    prior_cv = meta.get("contract_version")
    if prior_cv not in (None, "", cv):
        raise ContractError(
            f"TaskGraph.metadata contract_version mismatch: {prior_cv!r} != {cv!r}",
            code=LabErrorCode.CONTEXT_MISMATCH,
            where="stamp_task_graph_to_contract",
        )
    meta["contract_version"] = cv
    meta["investigation_id"] = contract.investigation_id
    meta["project_id"] = contract.project_id
    if contract.problem_kind is not None:
        meta["problem_kind"] = contract.problem_kind.value
    if output_names:
        meta["required_outputs"] = output_names
    if acceptance:
        meta["acceptance"] = acceptance

    stamped_tasks: list[TaskSpec] = []
    for task in graph.tasks:
        tmeta = dict(task.metadata or {})
        t_cv = tmeta.get("contract_version")
        if t_cv not in (None, "", cv):
            raise ContractError(
                f"{task.task_id}: contract_version mismatch {t_cv!r} != {cv!r}",
                code=LabErrorCode.CONTEXT_MISMATCH,
                where="stamp_task_graph_to_contract",
            )
        tmeta["contract_version"] = cv
        tmeta["investigation_id"] = contract.investigation_id
        if output_names:
            tmeta.setdefault("required_outputs", output_names)
        if acceptance:
            tmeta.setdefault("acceptance", acceptance)
        stamped_tasks.append(task.model_copy(update={"metadata": tmeta}))

    return graph.model_copy(update={"metadata": meta, "tasks": stamped_tasks})
