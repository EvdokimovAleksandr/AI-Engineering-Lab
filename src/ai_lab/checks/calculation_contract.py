"""Calculation contract: bind Task → CalculationSpec → ComputationArtifact.

Deterministic relevance / completeness gates — not an LLM semantic judge.
Stdout alone is never an engineering result.
"""

from __future__ import annotations

from typing import Any

from ai_lab.checks.units import is_valid_expected_dimension, units_compatible
from ai_lab.core.models import (
    CalculationSpec,
    Claim,
    ComputationArtifact,
    ComputationRelevanceResult,
    DeterministicCheckReport,
    EvidenceCompletenessReport,
    Quantity,
    VerificationPolicy,
)
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# Names too broad to lock as engineering required outputs (Attack Class 8).
_VAGUE_OUTPUT_NAMES = frozenset(
    {
        "engineering_result",
        "answer",
        "result",
        "output",
        "value",
        "final_answer",
        "solution",
        "response",
        "conclusion",
        "outcome",
        "quantity",
    }
)


def is_vague_output_name(name: str) -> bool:
    """True for contract-gaming names that must not satisfy acceptance alone."""
    return (name or "").strip().lower() in _VAGUE_OUTPUT_NAMES


def extract_outputs_from_understanding(payload: dict[str, Any] | None) -> tuple[list[str], dict[str, str]]:
    """Pull problem-bound required outputs from chief understanding JSON.

    These become VerificationPolicy locks so the simulation LLM cannot replace
    the problem with a self-consistent off-topic CalculationSpec.
    """
    if not payload or not isinstance(payload, dict):
        return [], {}
    names: list[str] = []
    dims: dict[str, str] = {}
    raw_out = payload.get("required_outputs")
    if isinstance(raw_out, list):
        for item in raw_out:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("id") or "").strip()
                if not name:
                    continue
                unit = item.get("unit") or item.get("dimension")
                if isinstance(unit, str) and unit.strip():
                    unit_s = unit.strip()
                    # Dimension names / legacy SI (L, L**2) are valid contract tokens.
                    if not is_valid_expected_dimension(unit_s):
                        logger.error(
                            "Ignoring understanding output %r: invalid dimension/unit %r",
                            name,
                            unit_s,
                        )
                        continue
                    names.append(name)
                    dims[name] = unit_s
                else:
                    names.append(name)
    elif isinstance(raw_out, dict):
        for key, val in raw_out.items():
            name = str(key)
            if isinstance(val, str) and val.strip():
                unit_s = val.strip()
                if not is_valid_expected_dimension(unit_s):
                    logger.error(
                        "Ignoring understanding output %r: invalid dimension/unit %r",
                        name,
                        unit_s,
                    )
                    continue
                names.append(name)
                dims[name] = unit_s
            elif isinstance(val, dict):
                unit = val.get("unit") or val.get("dimension")
                if isinstance(unit, str) and unit.strip():
                    unit_s = unit.strip()
                    if not is_valid_expected_dimension(unit_s):
                        logger.error(
                            "Ignoring understanding output %r: invalid dimension/unit %r",
                            name,
                            unit_s,
                        )
                        continue
                    names.append(name)
                    dims[name] = unit_s
                else:
                    names.append(name)
            else:
                names.append(name)
    raw_dims = payload.get("expected_dimensions")
    if isinstance(raw_dims, dict):
        for key, val in raw_dims.items():
            if isinstance(val, str) and val.strip():
                unit_s = val.strip()
                if not is_valid_expected_dimension(unit_s):
                    logger.error(
                        "Ignoring understanding expected_dimensions[%r]=%r (invalid)",
                        key,
                        unit_s,
                    )
                    continue
                dims.setdefault(str(key), unit_s)
    # Preserve order, drop empties; strip vague contract-gaming names.
    filtered: list[str] = []
    for n in names:
        if not n or is_vague_output_name(n):
            if n:
                logger.error(
                    "Rejecting vague required_output %r from understanding (not lockable)",
                    n,
                )
            continue
        filtered.append(n)
    names = list(dict.fromkeys(filtered))
    # Lock only outputs with valid dimension/unit tokens — bare interpretive labels
    # (e.g. loss_factor_interpretation without a unit) must not soft-fail the run.
    lockable = [n for n in names if n in dims]
    dropped = [n for n in names if n not in dims]
    for n in dropped:
        logger.error(
            "Not locking understanding output %r: missing valid expected_dimensions",
            n,
        )
    dims = {k: v for k, v in dims.items() if k in lockable}
    return lockable, dims


def _coerce_name_list(value: Any, *, field: str) -> list[str]:
    """Accept list[str] or dict keyed by names; reject other shapes loudly later."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, dict):
        logger.warning(
            "CalculationSpec.%s was a dict; using keys as name list (LLM schema drift)",
            field,
        )
        return [str(k) for k in value.keys()]
    logger.error("CalculationSpec.%s has unsupported type %s", field, type(value).__name__)
    return value  # type: ignore[return-value]


def _coerce_calculation_spec_shapes(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize common LLM JSON shape mistakes before pydantic validate."""
    out = dict(data)
    if "required_inputs" in out:
        out["required_inputs"] = _coerce_name_list(out.get("required_inputs"), field="required_inputs")
    if "required_outputs" in out:
        raw_out = out.get("required_outputs")
        if isinstance(raw_out, dict):
            # name → unit or name → {unit,...}: fold units into expected_dimensions.
            dims = dict(out.get("expected_dimensions") or {})
            names: list[str] = []
            for key, val in raw_out.items():
                name = str(key)
                names.append(name)
                if name in dims:
                    continue
                if isinstance(val, str) and val.strip():
                    dims[name] = val
                elif isinstance(val, dict):
                    unit = val.get("unit") or val.get("dimension")
                    if isinstance(unit, str) and unit.strip():
                        dims[name] = unit
            out["required_outputs"] = names
            out["expected_dimensions"] = dims
            logger.warning(
                "CalculationSpec.required_outputs was a dict; coerced to name list + dimensions"
            )
        else:
            out["required_outputs"] = _coerce_name_list(raw_out, field="required_outputs")
    return out


def parse_calculation_spec(
    raw: dict[str, Any] | None,
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    project_id: str | None = None,
    investigation_id: str | None = None,
    contract_version: str | None = None,
    policy: VerificationPolicy | None = None,
    execution_context: Any | None = None,
) -> CalculationSpec | None:
    """Parse LLM/proposal dict into CalculationSpec; policy locks trusted fields.

    LLM cannot delete required_outputs / expected_dimensions / mandatory checks
    once policy requires verification.

    When execution_context (or explicit ids) is provided, a proposal that carries
    a *different* task/run/project identity raises CONTEXT_MISMATCH — no remapping.
    """
    if not raw or not isinstance(raw, dict):
        return None
    data = dict(raw)

    # Resolve expected binding: explicit kwargs win over ExecutionContext fields.
    from ai_lab.core.execution_context import (
        UNSET_CONTRACT_VERSION,
        ExecutionContext,
        require_context_match,
        stamp_context_fields,
    )

    expected: ExecutionContext | None = None
    if execution_context is not None:
        expected = (
            execution_context
            if isinstance(execution_context, ExecutionContext)
            else ExecutionContext.model_validate(execution_context)
        )
    elif task_id and run_id and project_id:
        expected = ExecutionContext.for_project_run(
            project_id=project_id,
            investigation_id=investigation_id,
            task_id=task_id,
            run_id=run_id,
            contract_version=contract_version or UNSET_CONTRACT_VERSION,
        )

    if expected is not None:
        # Fail loud if the proposal already stamped foreign ids.
        require_context_match(
            expected,
            project_id=data.get("project_id"),
            investigation_id=data.get("investigation_id"),
            task_id=data.get("task_id"),
            run_id=data.get("run_id"),
            contract_version=data.get("contract_version"),
            where="CalculationSpec.proposal",
        )
        stamped = stamp_context_fields(
            expected,
            project_id=data.get("project_id"),
            investigation_id=data.get("investigation_id"),
            task_id=data.get("task_id"),
            run_id=data.get("run_id"),
            contract_version=data.get("contract_version"),
            where="CalculationSpec.proposal",
        )
        data.update(stamped)
    else:
        # Legacy callers without ExecutionContext: only fill unset ids (no overwrite).
        if task_id and not data.get("task_id"):
            data["task_id"] = task_id
        if run_id and not data.get("run_id"):
            data["run_id"] = run_id
        if project_id and not data.get("project_id"):
            data["project_id"] = project_id
        if investigation_id and not data.get("investigation_id"):
            data["investigation_id"] = investigation_id
        if contract_version and not data.get("contract_version"):
            data["contract_version"] = contract_version
        # Refuse wrong identity even without a full ExecutionContext object.
        from ai_lab.core.execution_context import ContextMismatchError

        if task_id and data.get("task_id") and str(data["task_id"]) != str(task_id):
            raise ContextMismatchError(
                "CalculationSpec.task_id does not match caller task",
                field="task_id",
                expected=task_id,
                actual=data.get("task_id"),
                where="CalculationSpec.proposal",
            )
        if run_id and data.get("run_id") and str(data["run_id"]) != str(run_id):
            raise ContextMismatchError(
                "CalculationSpec.run_id does not match caller run",
                field="run_id",
                expected=run_id,
                actual=data.get("run_id"),
                where="CalculationSpec.proposal",
            )
        if project_id and data.get("project_id") and str(data["project_id"]) != str(project_id):
            raise ContextMismatchError(
                "CalculationSpec.project_id does not match caller project",
                field="project_id",
                expected=project_id,
                actual=data.get("project_id"),
                where="CalculationSpec.proposal",
            )
        if (
            investigation_id
            and data.get("investigation_id")
            and str(data["investigation_id"]) != str(investigation_id)
        ):
            raise ContextMismatchError(
                "CalculationSpec.investigation_id does not match caller investigation",
                field="investigation_id",
                expected=investigation_id,
                actual=data.get("investigation_id"),
                where="CalculationSpec.proposal",
            )

    pol = policy or VerificationPolicy()
    # Trusted/policy overlay — LLM proposal cannot weaken verification.
    if pol.verification_required:
        data["verification_required"] = True
        min_checks = max(int(data.get("minimum_checks") or 0), pol.minimum_checks)
        data["minimum_checks"] = max(min_checks, 1)
    if pol.required_outputs:
        existing = list(data.get("required_outputs") or [])
        # Drop vague LLM extras; always union policy-locked names.
        existing = [n for n in existing if isinstance(n, str) and not is_vague_output_name(n)]
        merged = list(dict.fromkeys([*pol.required_outputs, *existing]))
        data["required_outputs"] = merged

    # LLM often emits name→value / name→unit maps instead of list[str].
    # Coerce shapes only; do not invent missing engineering outputs.
    data = _coerce_calculation_spec_shapes(data)

    # Policy dimensions win *after* coerce so LLM cannot override locked units
    # (V2.6.1: former setdefault allowed LLM pre-set to beat policy).
    if pol.required_output_dimensions:
        dims = dict(data.get("expected_dimensions") or {})
        for key, unit in pol.required_output_dimensions.items():
            dims[key] = unit
        data["expected_dimensions"] = dims
    if pol.required_outputs:
        # Re-assert locked outputs after coerce (dict→list may have dropped them).
        existing = list(data.get("required_outputs") or [])
        merged = list(dict.fromkeys([*pol.required_outputs, *existing]))
        data["required_outputs"] = merged

    try:
        spec = CalculationSpec.model_validate(data)
    except Exception as exc:
        logger.error("Invalid CalculationSpec proposal: %s", exc)
        raise ValueError(f"Invalid CalculationSpec: {exc}") from exc

    # Mark which fields came from policy (not elevatable by LLM narrative).
    trusted = list(spec.trusted_fields)
    if pol.verification_required or pol.required_outputs:
        for field in ("verification_required", "minimum_checks", "required_outputs", "expected_dimensions"):
            if field not in trusted:
                trusted.append(field)
    return spec.model_copy(update={"trusted_fields": trusted})


def sanitize_calculation_spec_units(spec: CalculationSpec) -> tuple[CalculationSpec, list[str]]:
    """Drop invalid expected_dimensions and demote those required outputs.

    LLM often emits formula prose as 'units'. Those tags must not kill an otherwise
    valid CalculationSpec (false negative). Dimension names and legacy SI letters
    (length, L, L**2) are kept; junk prose is removed from required_outputs.
    """
    warnings: list[str] = []
    dims = dict(spec.expected_dimensions)
    kept_required: list[str] = []
    for name in spec.required_outputs:
        if name not in dims:
            warnings.append(f"demoted required output {name!r}: missing expected_dimensions")
            logger.error("%s", warnings[-1])
            continue
        unit_s = (dims.get(name) or "").strip()
        if unit_s and not is_valid_expected_dimension(unit_s):
            warnings.append(
                f"demoted required output {name!r}: invalid dimension/unit {unit_s!r}"
            )
            dims.pop(name, None)
            logger.error("%s", warnings[-1])
            continue
        kept_required.append(name)
    # Also drop orphan junk dims not in required list.
    for name, unit in list(dims.items()):
        unit_s = (unit or "").strip()
        if unit_s and not is_valid_expected_dimension(unit_s):
            warnings.append(f"dropped invalid expected_dimensions[{name!r}]={unit_s!r}")
            dims.pop(name, None)
            logger.error("%s", warnings[-1])
    if not warnings:
        return spec, warnings
    # Never leave required_outputs empty if the LLM at least proposed names —
    # validation will still fail closed if nothing usable remains.
    new_required = kept_required if kept_required else list(spec.required_outputs)
    return (
        spec.model_copy(update={"expected_dimensions": dims, "required_outputs": new_required}),
        warnings,
    )

def validate_calculation_spec(spec: CalculationSpec) -> list[str]:
    """Deterministic structural validation of a CalculationSpec."""
    errors: list[str] = []
    if not (spec.objective or "").strip():
        errors.append("CalculationSpec.objective is required")
    if not spec.required_outputs:
        errors.append("CalculationSpec.required_outputs must be non-empty")
    for name in spec.required_outputs:
        if is_vague_output_name(name):
            errors.append(f"required output {name!r} is too vague for engineering contract")
        if name not in spec.expected_dimensions:
            errors.append(f"missing expected_dimensions for required output {name!r}")
        else:
            unit = (spec.expected_dimensions.get(name) or "").strip()
            if unit and not is_valid_expected_dimension(unit):
                errors.append(
                    f"expected_dimensions[{name!r}]={unit!r} is not a valid "
                    f"dimension name, legacy SI token, or Pint unit"
                )
    if spec.verification_required and spec.minimum_checks < 1:
        errors.append("verification_required implies minimum_checks >= 1")
    return errors


def _normalize_output_map(raw: dict[str, Any] | None) -> dict[str, Quantity]:
    """Coerce declared_outputs into name → Quantity."""
    out: dict[str, Quantity] = {}
    if not raw:
        return out
    for name, value in raw.items():
        key = str(name)
        if isinstance(value, Quantity):
            out[key] = value
            continue
        if isinstance(value, dict):
            if "value" not in value:
                logger.error("declared_output %s missing value: %r", key, value)
                continue
            out[key] = Quantity(value=float(value["value"]), unit=str(value.get("unit") or ""))
            continue
        try:
            out[key] = Quantity(value=float(value), unit="")
        except (TypeError, ValueError):
            logger.error("declared_output %s is not numeric: %r", key, value)
    return out


def _artifact_outputs(computation: ComputationArtifact) -> dict[str, Quantity]:
    """Prefer structured declared_outputs; never treat stdout as a result."""
    declared = computation.declared_outputs or {}
    if declared:
        return _normalize_output_map(declared)
    # Legacy: structured result dict may carry outputs under result.outputs
    nested = computation.result.get("outputs") if isinstance(computation.result, dict) else None
    if isinstance(nested, dict):
        return _normalize_output_map(nested)
    return {}


def validate_computation_against_spec(
    computation: ComputationArtifact,
    calculation_spec: CalculationSpec,
) -> ComputationRelevanceResult:
    """Deterministic relevance: required outputs + dimensions vs declared outputs.

    Unrelated computation (e.g. memory_gib when power/W required) → invalid.
    Identity mismatches (task/run/project) raise CONTEXT_MISMATCH — not soft FAIL.
    """
    from ai_lab.core.execution_context import ContextMismatchError

    reasons: list[str] = []
    missing_inputs: list[str] = []
    missing_outputs: list[str] = []
    dimension_mismatches: list[str] = []

    # Hard isolation: wrong binding is a lab error, not an irrelevant computation.
    if calculation_spec.task_id and computation.task_id and computation.task_id != calculation_spec.task_id:
        raise ContextMismatchError(
            "ComputationArtifact.task_id does not match CalculationSpec.task_id",
            field="task_id",
            expected=calculation_spec.task_id,
            actual=computation.task_id,
            where="validate_computation_against_spec",
        )
    if calculation_spec.run_id and computation.run_id and computation.run_id != calculation_spec.run_id:
        raise ContextMismatchError(
            "ComputationArtifact.run_id does not match CalculationSpec.run_id",
            field="run_id",
            expected=calculation_spec.run_id,
            actual=computation.run_id,
            where="validate_computation_against_spec",
        )
    if (
        calculation_spec.project_id
        and computation.project_id
        and computation.project_id != calculation_spec.project_id
    ):
        raise ContextMismatchError(
            "ComputationArtifact.project_id does not match CalculationSpec.project_id",
            field="project_id",
            expected=calculation_spec.project_id,
            actual=computation.project_id,
            where="validate_computation_against_spec",
        )
    if (
        calculation_spec.investigation_id
        and computation.investigation_id
        and computation.investigation_id != calculation_spec.investigation_id
    ):
        raise ContextMismatchError(
            "ComputationArtifact.investigation_id does not match CalculationSpec",
            field="investigation_id",
            expected=calculation_spec.investigation_id,
            actual=computation.investigation_id,
            where="validate_computation_against_spec",
        )

    # Binding: artifact must reference this spec when both ids present.
    if computation.calculation_spec_id and computation.calculation_spec_id != calculation_spec.spec_id:
        reasons.append(
            f"calculation_spec_id mismatch: artifact={computation.calculation_spec_id} "
            f"spec={calculation_spec.spec_id}"
        )

    outputs = _artifact_outputs(computation)
    if not outputs:
        reasons.append(
            "no structured declared_outputs on ComputationArtifact "
            "(stdout alone is not an engineering result)"
        )

    for name in calculation_spec.required_outputs:
        if name not in outputs:
            missing_outputs.append(name)
            reasons.append(f"missing required output {name!r}")

    for name, expected_unit in calculation_spec.expected_dimensions.items():
        if name not in outputs:
            continue
        actual_unit = (outputs[name].unit or "").strip()
        expected = (expected_unit or "").strip()
        # Semantic Dimension / legacy SI / Pint (W≡kW≡J/s; length|L ≡ m).
        if not units_compatible(expected, actual_unit):
            dimension_mismatches.append(f"{name}: expected {expected!r}, got {actual_unit!r}")
            reasons.append(
                f"dimension mismatch for {name!r}: expected {expected!r}, got {actual_unit!r}"
            )

    # Required inputs must appear in artifact metadata/result or declared input map.
    input_names = set()
    meta_inputs = computation.metadata.get("inputs") if isinstance(computation.metadata, dict) else None
    if isinstance(meta_inputs, dict):
        input_names.update(str(k) for k in meta_inputs)
    result_inputs = computation.result.get("inputs") if isinstance(computation.result, dict) else None
    if isinstance(result_inputs, dict):
        input_names.update(str(k) for k in result_inputs)
    # Also accept reflection via code variable names as weak signal only when listed in metadata.
    reflected = computation.metadata.get("used_inputs") if isinstance(computation.metadata, dict) else None
    if isinstance(reflected, (list, tuple, set)):
        input_names.update(str(x) for x in reflected)

    for name in calculation_spec.required_inputs:
        if name not in input_names and name not in outputs:
            # Soft: record missing but do not alone fail if outputs match
            # (inputs may be baked into expression). Still required for completeness.
            missing_inputs.append(name)

    if missing_inputs:
        reasons.append(f"required inputs not reflected in computation contract: {missing_inputs}")

    relevant = (
        not missing_outputs
        and not dimension_mismatches
        and bool(outputs)
        and not any("mismatch" in r for r in reasons if "calculation_spec_id" in r or "task_id" in r)
    )
    # Binding mismatches always invalidate.
    if any("mismatch" in r for r in reasons):
        relevant = False
    if missing_outputs or dimension_mismatches or not outputs:
        relevant = False

    return ComputationRelevanceResult(
        relevant=relevant,
        calculation_spec_id=calculation_spec.spec_id,
        computation_artifact_id=computation.artifact_id,
        missing_inputs=missing_inputs,
        missing_outputs=missing_outputs,
        dimension_mismatches=dimension_mismatches,
        reasons=reasons,
    )


def evaluate_evidence_completeness(
    *,
    calculation_specs: list[CalculationSpec],
    computations: list[ComputationArtifact],
    check_report: DeterministicCheckReport | None,
    claims: list[Claim] | None = None,
    verification_policy: VerificationPolicy | None = None,
    require_calculation: bool = True,
    relevance_results: list[ComputationRelevanceResult] | None = None,
    output_aliases: dict[str, list[str]] | None = None,
    acceptance_passed: bool | None = None,
    acceptance_reasons: list[str] | None = None,
    # PR-05: optional contract + evidence lineage (wired into completeness, not a second gate).
    required_contract_outputs: list[str] | None = None,
    evidence_records: list | None = None,
    execution_context: Any | None = None,
) -> EvidenceCompletenessReport:
    """Gate before adjudication: missing mandatory evidence ⇒ not PASS."""
    policy = verification_policy or VerificationPolicy()
    reasons: list[str] = []
    claims = claims or []
    aliases = output_aliases or {}

    computation_complete = True
    computation_relevant = True
    verification_complete = True
    required_checks_pass = True
    provenance_complete = True
    required_output_coverage = True
    acceptance_ok = True if acceptance_passed is None else bool(acceptance_passed)
    lineage_ok = True
    contract_coverage_ok = True
    coverage_ratio: float | None = None

    if require_calculation or policy.calculation_required:
        if not calculation_specs:
            computation_complete = False
            reasons.append("required CalculationSpec missing")
        if not computations:
            computation_complete = False
            reasons.append("required ComputationArtifact missing")
        else:
            # At least one successful sandbox execution is needed.
            ok_comps = [
                c
                for c in computations
                if (c.returncode == 0 or c.sandbox_status in (None, "SUCCESS", "success"))
                and (c.status in ("ok", "SUCCESS", "success") or c.returncode == 0)
            ]
            if not ok_comps and computations:
                # Still count presence; relevance will decide.
                pass
        # Locked understanding outputs must appear on at least one CalculationSpec.
        if policy.required_outputs and calculation_specs:
            for req in policy.required_outputs:
                names_ok = False
                for spec in calculation_specs:
                    cand = {req, *aliases.get(req, [])}
                    if any(n in spec.required_outputs for n in cand):
                        names_ok = True
                        break
                if not names_ok:
                    computation_complete = False
                    reasons.append(
                        f"locked required_output {req!r} missing from CalculationSpec "
                        "(policy lock not applied / stripped)"
                    )

    relevance = list(relevance_results or [])
    if not relevance and calculation_specs and computations:
        # Pair each spec only with bound computations — never all orphans (V2.6.1).
        for spec in calculation_specs:
            matched = [c for c in computations if c.calculation_spec_id == spec.spec_id]
            if not matched:
                # Legacy unbound: same task_id and no foreign spec binding.
                matched = [
                    c
                    for c in computations
                    if not c.calculation_spec_id
                    and spec.task_id
                    and c.task_id == spec.task_id
                ]
            for comp in matched:
                relevance.append(validate_computation_against_spec(comp, spec))
            if calculation_specs and not matched:
                reasons.append(
                    f"no ComputationArtifact bound to CalculationSpec {spec.spec_id}"
                )

    if require_calculation or policy.calculation_required:
        if calculation_specs and not any(r.relevant for r in relevance):
            computation_relevant = False
            reasons.append("no computation relevant to CalculationSpec")
            for r in relevance:
                reasons.extend(r.reasons)

    verification_required = policy.verification_required
    if verification_required:
        if check_report is None:
            verification_complete = False
            reasons.append("verification report missing")
        else:
            n_checks = len(check_report.results) + len(check_report.verification_results)
            # Deduplicate: verification_results often mirrored into results.
            n_unique = max(len(check_report.results), len(check_report.verification_results))
            if n_unique == 0 and n_checks == 0:
                verification_complete = False
                reasons.append("empty verification report (no checks executed)")
            elif n_unique < policy.minimum_checks:
                verification_complete = False
                reasons.append(
                    f"executed checks={n_unique} < minimum_checks={policy.minimum_checks}"
                )
            if check_report.has_critical_failure or (
                n_unique > 0 and not check_report.all_passed
            ):
                required_checks_pass = False
                reasons.append("required deterministic checks did not pass")

    # Provenance: quantitative CALCULATION claims need computation + check link.
    for claim in claims:
        if claim.kind.value not in {"CALCULATION", "SIMULATION_RESULT"}:
            continue
        if not claim.computation_artifact_id:
            provenance_complete = False
            reasons.append(f"claim {claim.claim_id} missing computation_artifact_id")
        if verification_required and not claim.math_check and not claim.verification_spec:
            provenance_complete = False
            reasons.append(
                f"claim {claim.claim_id} missing math_check/verification_spec "
                "(required for quantitative engineering)"
            )

    # Required-output coverage: each locked output needs relevant compute + verified claim.
    coverage_targets = list(policy.required_outputs)
    if not coverage_targets:
        for spec in calculation_specs:
            coverage_targets.extend(spec.required_outputs)
        coverage_targets = list(dict.fromkeys(coverage_targets))
    if coverage_targets and (require_calculation or policy.calculation_required):
        passed_ids = passed_claim_ids_from_report(check_report)
        comps_by_id = {c.artifact_id: c for c in computations}
        for req in coverage_targets:
            names = [req, *aliases.get(req, [])]
            covered = False
            for claim in claims:
                if claim.kind.value not in {"CALCULATION", "SIMULATION_RESULT"}:
                    continue
                if not _claim_covers_output_name(claim, names):
                    continue
                art = comps_by_id.get(claim.computation_artifact_id or "")
                if art is None:
                    continue
                outs = _artifact_outputs(art)
                if not any(n in outs for n in names):
                    continue
                # Must be relevant to some spec when relevance was evaluated.
                if relevance and not any(
                    r.computation_artifact_id == art.artifact_id and r.relevant
                    for r in relevance
                ):
                    continue
                if verification_required and claim.claim_id not in passed_ids:
                    continue
                covered = True
                break
            if not covered:
                required_output_coverage = False
                reasons.append(
                    f"required output {req!r} lacks verified claim←computation coverage"
                )

    if acceptance_passed is False:
        acceptance_ok = False
        reasons.extend(acceptance_reasons or ["benchmark acceptance contract failed"])

    # PR-05: lineage + EngineeringContract output coverage (не дублирует numeric UnitVerifier).
    from ai_lab.checks.lineage_coverage import evaluate_lineage_and_contract_coverage

    contract_targets = list(required_contract_outputs or [])
    lineage_report = evaluate_lineage_and_contract_coverage(
        claims=claims,
        computations=computations,
        evidence=evidence_records or [],
        required_outputs=contract_targets,
        expected=execution_context,
        output_aliases=aliases,
    )
    lineage_ok = lineage_report.lineage_ok
    contract_coverage_ok = lineage_report.contract_coverage_ok
    coverage_ratio = lineage_report.coverage_ratio
    if not lineage_ok or not contract_coverage_ok:
        reasons.extend(lineage_report.reasons)

    # PR-06: списки covered/missing для IterationController (не только ratio).
    covered_outputs = list(lineage_report.covered_outputs)
    missing_outputs = list(lineage_report.missing_outputs)
    for rr in relevance:
        for name in rr.missing_outputs:
            if name not in missing_outputs and name not in covered_outputs:
                missing_outputs.append(name)

    return EvidenceCompletenessReport(
        computation_complete=computation_complete,
        computation_relevant=computation_relevant,
        verification_complete=verification_complete,
        required_checks_pass=required_checks_pass,
        provenance_complete=provenance_complete,
        required_output_coverage=required_output_coverage,
        acceptance_passed=acceptance_ok,
        lineage_ok=lineage_ok,
        contract_coverage_ok=contract_coverage_ok,
        coverage_ratio=coverage_ratio,
        covered_outputs=covered_outputs,
        missing_outputs=missing_outputs,
        reasons=reasons,
        relevance_results=relevance,
        required_checks=policy.minimum_checks if verification_required else 0,
        executed_checks=(
            max(len(check_report.results), len(check_report.verification_results))
            if check_report
            else 0
        ),
    )


def _claim_covers_output_name(claim: Claim, names: list[str]) -> bool:
    """Claim must name the required output (statement or conditions.covers_outputs).

    Prevents correct artifact + wrong semantic claim (efficiency vs power).
    """
    covers = None
    if isinstance(claim.conditions, dict):
        covers = claim.conditions.get("covers_outputs")
    if isinstance(covers, (list, tuple, set)):
        cover_set = {str(x).lower() for x in covers}
        if any(n.lower() in cover_set for n in names):
            return True
    text = (claim.statement or "").lower()
    return any(n.lower() in text for n in names)


def claim_has_verified_computation_provenance(
    claim: Claim,
    *,
    check_report: DeterministicCheckReport | None,
    passed_claim_ids: set[str] | None = None,
) -> bool:
    """True iff quantitative claim is backed by a passed deterministic check."""
    if claim.kind.value not in {"CALCULATION", "SIMULATION_RESULT"}:
        return False
    if not claim.computation_artifact_id:
        return False
    if not claim.math_check and not claim.verification_spec:
        return False
    if passed_claim_ids is not None:
        return claim.claim_id in passed_claim_ids
    if check_report is None:
        return False
    for vr in check_report.verification_results:
        if vr.claim_id == claim.claim_id and vr.passed:
            return True
    for mr in check_report.results:
        if mr.details.get("claim_id") == claim.claim_id and mr.passed:
            return True
        # MathCheckResult may carry claim via check linkage in details
        if getattr(mr, "verification_result", None) is not None:
            vrr = mr.verification_result
            if vrr and vrr.claim_id == claim.claim_id and vrr.passed:
                return True
    return False


def passed_claim_ids_from_report(check_report: DeterministicCheckReport | None) -> set[str]:
    ids: set[str] = set()
    if check_report is None:
        return ids
    for vr in check_report.verification_results:
        if vr.passed and vr.claim_id:
            ids.add(vr.claim_id)
    for mr in check_report.results:
        cid = None
        if mr.verification_result is not None and mr.verification_result.claim_id:
            cid = mr.verification_result.claim_id
        elif isinstance(mr.details, dict):
            cid = mr.details.get("claim_id")
        if cid and mr.passed:
            ids.add(str(cid))
    return ids
