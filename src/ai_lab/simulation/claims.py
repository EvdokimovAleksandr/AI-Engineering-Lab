"""Turn SimulationResult quantitative outputs into VerificationSpec + Claim candidates.

LLM does not get to declare PASS. DeterministicVerifier remains the authority.
"""

from __future__ import annotations

from ai_lab.core.enums import EvidenceKind, EvidenceStrength, SimulationStatus, SourceTrustTier
from ai_lab.core.models import Claim, ConfidenceBreakdown, Quantity, SanityCheck, ToleranceSpec, VerificationSpec
from ai_lab.simulation.models import SimulationResult, SimulationSpec
from ai_lab.simulation.solvers.uniaxial_tension import CLOSED_FORM_EQUATIONS, SI_UNITS


def verification_specs_from_simulation(
    spec: SimulationSpec,
    result: SimulationResult,
) -> list[VerificationSpec]:
    """Independent recomputation specs for key tensile outputs."""
    if result.status not in {SimulationStatus.SUCCESS, SimulationStatus.OUT_OF_DOMAIN}:
        return []
    inputs = dict(spec.parameters)
    rtol = float(spec.numerical_settings.relative_tolerance or 1e-9)
    atol_stress = Quantity(value=1e-6, unit="Pa")
    specs: list[VerificationSpec] = []
    mapping = (
        ("area", CLOSED_FORM_EQUATIONS["area"], "m**2"),
        ("stress", CLOSED_FORM_EQUATIONS["stress"], "Pa"),
        ("strain", CLOSED_FORM_EQUATIONS["strain"], ""),
        ("elastic_prediction", CLOSED_FORM_EQUATIONS["elastic_prediction"], "Pa"),
        ("failure_margin", CLOSED_FORM_EQUATIONS["failure_margin"], ""),
        ("mass", CLOSED_FORM_EQUATIONS["mass"], "kg"),
    )
    for name, expression, unit in mapping:
        expected = result.outputs.get(name)
        if expected is None:
            continue
        abs_tol = Quantity(value=1e-18, unit=unit) if unit else Quantity(value=1e-18, unit="")
        if name in {"stress", "elastic_prediction"}:
            abs_tol = atol_stress
        specs.append(
            VerificationSpec(
                spec_id=f"sim-{result.result_id}-{name}",
                inputs=inputs,
                expression=expression,
                expected=expected,
                tolerance=ToleranceSpec(absolute=abs_tol, relative=rtol),
                sanity_checks=_sanity_for(name),
                metadata={
                    "simulation_result_id": result.result_id,
                    "output": name,
                    "layer": "independent_recomputation",
                },
            )
        )
    if "stress" in result.outputs and "elastic_prediction" in result.outputs:
        specs.append(
            VerificationSpec(
                spec_id=f"sim-{result.result_id}-stress-vs-elastic",
                inputs={
                    "stress": result.outputs["stress"],
                    "elastic_prediction": result.outputs["elastic_prediction"],
                },
                expression="stress",
                expected=result.outputs["elastic_prediction"],
                tolerance=ToleranceSpec(absolute=atol_stress, relative=rtol),
                metadata={
                    "simulation_result_id": result.result_id,
                    "output": "stress_vs_E_strain",
                    "layer": "independent_recomputation",
                    "note": "Discrepancy is reported; it is not hidden.",
                },
            )
        )
    return specs


def _sanity_for(name: str) -> list[SanityCheck]:
    if name in {"area", "mass"}:
        return [SanityCheck(name=f"{name}_positive", condition="actual > 0", failure_message=f"{name} must be > 0")]
    if name in {"stress", "failure_margin", "strain", "elastic_prediction"}:
        return [SanityCheck(name=f"{name}_nonneg", condition="actual >= 0", failure_message=f"{name} must be >= 0")]
    return []


def claims_from_simulation(
    spec: SimulationSpec,
    result: SimulationResult,
    *,
    verification_specs: list[VerificationSpec],
    project_id: str | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    contract_version: str | None = None,
) -> list[Claim]:
    """Quantitative claims tied to SimulationResult + optional VerificationSpec."""
    claims: list[Claim] = []
    by_output = {
        str((vs.metadata or {}).get("output")): vs
        for vs in verification_specs
        if vs.metadata.get("output")
    }
    statements = {
        "stress": _stress_statement(spec, result),
        "elastic_prediction": _elastic_statement(spec, result),
        "area": _qty_statement("cross-section area", result.outputs.get("area")),
        "strain": _qty_statement("axial strain", result.outputs.get("strain")),
        "failure_margin": _qty_statement("failure margin (tensile_strength/stress)", result.outputs.get("failure_margin")),
        "mass": _qty_statement("fiber mass", result.outputs.get("mass")),
    }
    stub_source = next(
        (p.source for p in spec.parameter_provenance.values()),
        "fixture://synthetic",
    )
    stub = any(p.trust.value == "STUB" for p in spec.parameter_provenance.values()) or stub_source.startswith(
        "fixture://"
    )
    for name, statement in statements.items():
        if statement is None:
            continue
        vspec = by_output.get(name)
        claims.append(
            Claim(
                statement=statement,
                kind=EvidenceKind.CALCULATION,
                source=stub_source,
                source_trust=SourceTrustTier.STUB if stub else None,
                evidence=f"simulation_result={result.result_id}",
                assumptions=[a.statement for a in result.assumptions],
                falsifiers=[
                    "Independent DeterministicVerifier recomputation disagrees",
                    "Material parameters lack primary evidence",
                ],
                conditions={
                    "simulation_status": result.status.value,
                    "output": name,
                    "scientific_status": result.scientific_status.model_dump(mode="json"),
                },
                agent_id="uniaxial_tension",
                verification_spec=vspec.model_dump(mode="json") if vspec else None,
                computation_artifact_id=result.computation_artifact_id,
                evidence_strength=EvidenceStrength.PRIMARY_CALCULATION,
                confidence=ConfidenceBreakdown(
                    compute_check=0.9 if result.status.value == "SUCCESS" else 0.2,
                    assumption_quality=0.3,
                    source_quality=0.0 if stub else 0.4,
                ),
                project_id=project_id,
                investigation_id=investigation_id,
                task_id=task_id,
                run_id=run_id,
                contract_version=contract_version,
            )
        )
    return claims


def _stress_statement(spec: SimulationSpec, result: SimulationResult) -> str | None:
    stress = result.outputs.get("stress")
    diameter = spec.parameters.get("diameter")
    force = spec.parameters.get("force")
    if stress is None or diameter is None or force is None:
        return None
    mpa = _as_unit(stress, "MPa")
    return (
        f"For the specified fiber diameter ({diameter.value} {diameter.unit}) and force "
        f"({force.value} {force.unit}), the resulting tensile stress is {mpa} MPa. "
        f"[synthetic/stub inputs — not a scientific FACT about spider silk]"
    )


def _elastic_statement(spec: SimulationSpec, result: SimulationResult) -> str | None:
    elastic = result.outputs.get("elastic_prediction")
    extension = spec.parameters.get("extension")
    if elastic is None or extension is None:
        return None
    mpa = _as_unit(elastic, "MPa")
    return (
        f"At extension ΔL = {extension.value} {extension.unit}, the linear-elastic model "
        f"predicts stress {mpa} MPa. "
        f"[synthetic/stub inputs — not a scientific FACT about spider silk]"
    )


def _qty_statement(label: str, qty: Quantity | None) -> str | None:
    if qty is None:
        return None
    unit = qty.unit or "1"
    return f"Computed {label} is {qty.value} {unit} (synthetic benchmark)."


def _as_unit(qty: Quantity, unit: str) -> float:
    from ai_lab.checks.units import convert_to, parse_quantity, to_pint

    aligned = convert_to(to_pint(qty), parse_quantity(1.0, unit))
    return float(aligned.magnitude)


def si_unit_for(name: str) -> str:
    return SI_UNITS.get(name, "")
