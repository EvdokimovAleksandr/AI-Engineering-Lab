"""Engineering simulation contracts. Distinct from VerificationSpec / ComputeSpec.

Parameters use existing Quantity; Pint is applied at validation/solve time.
Security-sensitive fields are forbidden so LLM/UI cannot smuggle host control.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_lab.core.enums import (
    EvidenceConfidence,
    EvidenceKind,
    ModelValidity,
    NumericalCorrectness,
    ParameterTrust,
    PhysicalValidity,
    SimulationStatus,
)
from ai_lab.core.models import Quantity, _new_id
from ai_lab.sandbox.models import FORBIDDEN_COMPUTE_KEYS

# Host/runtime control must never appear on a scientific spec.
FORBIDDEN_SIMULATION_KEYS = FORBIDDEN_COMPUTE_KEYS | frozenset(
    {
        "api_key",
        "api_keys",
        "run_budget",
        "budget",
        "routing",
        "routing_policy",
        "sandbox",
        "host_cwd",
        "solver_import",
        "python_import",
        "network",
        "docker_host",
    }
)

ParameterConstraint = Literal["positive", "nonzero", "nonnegative", "any"]


def _reject_forbidden(mapping: dict[str, Any], *, where: str) -> dict[str, Any]:
    bad = FORBIDDEN_SIMULATION_KEYS.intersection(mapping)
    if bad:
        raise ValueError(f"{where} must not contain host-control keys: {sorted(bad)}")
    return mapping


class ParameterProvenance(BaseModel):
    """Where a parameter came from. fixture://synthetic is STUB, never FACT."""

    model_config = ConfigDict(extra="forbid")

    source: str
    trust: ParameterTrust = ParameterTrust.INPUT_UNVERIFIED
    note: str = ""


class EquationSpec(BaseModel):
    """Named assignment evaluated by SafeExpressionEvaluator — not arbitrary Python."""

    model_config = ConfigDict(extra="forbid")

    name: str
    expression: str
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name_is_identifier(cls, v: str) -> str:
        if not v.isidentifier() or v.startswith("_"):
            raise ValueError(f"Equation name must be a public identifier, got {v!r}")
        return v

    @field_validator("expression")
    @classmethod
    def _expression_non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("Equation expression must be non-empty")
        return v.strip()


class Assumption(BaseModel):
    """Explicit model hypothesis. Kind defaults to ASSUMPTION and cannot be FACT."""

    model_config = ConfigDict(extra="forbid")

    id: str
    statement: str
    kind: EvidenceKind = EvidenceKind.ASSUMPTION
    source: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _not_a_fact(self) -> Assumption:
        if self.kind == EvidenceKind.FACT:
            raise ValueError("Assumption cannot be labeled FACT")
        if self.kind != EvidenceKind.ASSUMPTION:
            raise ValueError(f"Assumption.kind must be ASSUMPTION, got {self.kind.value}")
        _reject_forbidden(self.provenance, where="Assumption.provenance")
        return self


class BoundaryCondition(BaseModel):
    """Named constraint on a field (displacement, force, …) with a Quantity value."""

    model_config = ConfigDict(extra="forbid")

    name: str
    variable: str
    value: Quantity


class SolverConfig(BaseModel):
    """Trusted solver identifier only. No image / argv / import path."""

    model_config = ConfigDict(extra="forbid")

    solver_id: str

    @field_validator("solver_id")
    @classmethod
    def _safe_solver_id(cls, v: str) -> str:
        if not v or not v.replace("_", "").isalnum() or ".." in v or "/" in v or "\\" in v:
            raise ValueError(f"Invalid solver_id: {v!r}")
        return v


class NumericalSettings(BaseModel):
    """Solver iteration/tolerance knobs. Comparison still uses DeterministicVerifier policy."""

    model_config = ConfigDict(extra="forbid")

    absolute_tolerance: Quantity | None = None
    relative_tolerance: float | None = Field(default=1e-9, ge=0.0)
    max_iterations: int = Field(default=1, ge=1, le=1_000_000)
    solver_parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("solver_parameters")
    @classmethod
    def _no_host_control(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _reject_forbidden(v, where="NumericalSettings.solver_parameters")


class SimulationSpec(BaseModel):
    """Structured engineering model. Not Python source and not a ComputeSpec."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _new_id("sspec"))
    model_type: str
    parameters: dict[str, Quantity]
    equations: list[EquationSpec] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    boundary_conditions: list[BoundaryCondition] = Field(default_factory=list)
    solver: SolverConfig
    numerical_settings: NumericalSettings = Field(default_factory=NumericalSettings)
    output_schema: str = "simulation_result"
    metadata: dict[str, Any] = Field(default_factory=dict)
    parameter_constraints: dict[str, ParameterConstraint] = Field(default_factory=dict)
    expected_dimensions: dict[str, str] = Field(default_factory=dict)
    parameter_provenance: dict[str, ParameterProvenance] = Field(default_factory=dict)
    # UQ is an extension point — do not pretend it is implemented.
    uncertainty: dict[str, Any] = Field(default_factory=lambda: {"supported": False})

    @field_validator("model_type")
    @classmethod
    def _model_type_safe(cls, v: str) -> str:
        if not v or not v.replace("_", "").isalnum():
            raise ValueError(f"Invalid model_type: {v!r}")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_safe(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _reject_forbidden(v, where="SimulationSpec.metadata")

    @field_validator("uncertainty")
    @classmethod
    def _uncertainty_safe(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _reject_forbidden(v, where="SimulationSpec.uncertainty")

    @model_validator(mode="after")
    def _parameter_names(self) -> SimulationSpec:
        for name in self.parameters:
            if not name.isidentifier() or name.startswith("_"):
                raise ValueError(f"Invalid parameter name: {name!r}")
        return self


class ConvergenceMetadata(BaseModel):
    """Numerical solver bookkeeping. Algebraic solvers report iterations=1."""

    model_config = ConfigDict(extra="forbid")

    converged: bool
    iterations: int = 1
    residual: float | None = None
    tolerance: float | None = None


class SimulationCheck(BaseModel):
    """One deterministic sanity / consistency check recorded on the result."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    message: str = ""
    layer: str = "model_sanity"  # unit | numerical | model_sanity | recomputation | red_team


class ScientificStatus(BaseModel):
    """Four distinct axes — a math PASS is not physical VALIDITY."""

    model_config = ConfigDict(extra="forbid")

    model_validity: ModelValidity = ModelValidity.UNKNOWN
    numerical_correctness: NumericalCorrectness = NumericalCorrectness.UNKNOWN
    physical_validity: PhysicalValidity = PhysicalValidity.UNASSESSED
    evidence_confidence: EvidenceConfidence = EvidenceConfidence.UNASSESSED


class ModelValidationResult(BaseModel):
    """Outcome of validate_simulation_spec. Invalid specs must not be solved."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    status: SimulationStatus
    errors: list[str] = Field(default_factory=list)


class SimulationResult(BaseModel):
    """Structured solver output. Status is not CheckStatus / TaskStatus / SandboxStatus."""

    model_config = ConfigDict(extra="forbid")

    result_id: str = Field(default_factory=lambda: _new_id("sres"))
    spec_id: str
    status: SimulationStatus
    outputs: dict[str, Quantity] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    solver: str
    numerical_metadata: ConvergenceMetadata
    assumptions: list[Assumption] = Field(default_factory=list)
    checks: list[SimulationCheck] = Field(default_factory=list)
    computation_artifact_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    scientific_status: ScientificStatus = Field(default_factory=ScientificStatus)
    diagnostics: list[str] = Field(default_factory=list)

    @field_validator("provenance")
    @classmethod
    def _provenance_safe(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _reject_forbidden(v, where="SimulationResult.provenance")
