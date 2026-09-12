"""Simulation / Computational Engineer — CalculationSpec + immutable artifacts."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, coerce_str_list, llm_json
from ai_lab.checks.calculation_contract import (
    extract_outputs_from_understanding,
    parse_calculation_spec,
    sanitize_calculation_spec_units,
    validate_calculation_spec,
    validate_computation_against_spec,
)
from ai_lab.checks.math_check import normalize_math_check_payload
from ai_lab.core.enums import AgentRole, EvidenceKind, ProjectState
from ai_lab.core.models import (
    AgentResult,
    Claim,
    ComputationArtifact,
    ConfidenceBreakdown,
    TaskSpec,
    VerificationPolicy,
)
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


class SimulationAgent(BaseAgent):
    role = AgentRole.SIMULATION
    system_prompt = (
        "You are the Simulation Engineer. Propose a CalculationSpec, short Python "
        "calculations, structured declared_outputs, and claims with math_check. "
        "Do not claim experimental FACT. JSON only with keys: calculation_spec, "
        "code, declared_outputs, claims "
        "(claims may include math_check with expression/expected/inputs/units). "
        "declared_outputs must be {name: {value, unit}} matching calculation_spec."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        is_calculation = (
            task.state_context == ProjectState.CALCULATION
            or task.task_id == "calculation"
            or (task.output_schema or "") == "computation"
        )
        # Problem-bound outputs from understanding (trusted policy overlay).
        understanding_outputs: list[str] = []
        understanding_dims: dict[str, str] = {}
        try:
            understanding = ctx.store.read_json("reviews/chief_understanding.json")
            understanding_outputs, understanding_dims = extract_outputs_from_understanding(
                understanding if isinstance(understanding, dict) else None
            )
        except FileNotFoundError:
            logger.warning("No chief_understanding.json yet; CalculationSpec not problem-bound")
        except Exception as exc:
            logger.error("Failed reading chief_understanding.json: %s", exc)

        policy = VerificationPolicy(
            verification_required=bool(ctx.extra.get("require_verification", is_calculation)),
            calculation_required=bool(ctx.extra.get("require_calculation", is_calculation)),
            minimum_checks=1 if is_calculation else 0,
            required_outputs=list(understanding_outputs),
            required_output_dimensions=dict(understanding_dims),
        )
        if understanding_outputs:
            logger.info(
                "VerificationPolicy locked required_outputs from understanding: %s",
                understanding_outputs,
            )
        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                f"Objective: {task.objective}\n"
                "Return JSON: {\n"
                "  calculation_spec: {objective, required_inputs, required_outputs, "
                "expected_dimensions, expected_relations, domain},\n"
                "  code: python snippet using only whitelisted imports,\n"
                "  declared_outputs: {name: {value: number, unit: string}},\n"
                "  claims: [...] with math_check for quantitative results\n"
                "}\n"
                "Stdout alone is not a result — declared_outputs are required."
            ),
            schema_name="SimulationOutput",
        )

        code = str(payload.get("code") or "").strip()
        if not code:
            logger.error("Simulation agent returned empty code")
            raise ValueError("Simulation agent must return non-empty code")

        calc_spec = None
        relevance = None
        contract_error: str | None = None
        if is_calculation or payload.get("calculation_spec"):
            raw_spec = (
                payload.get("calculation_spec")
                if isinstance(payload.get("calculation_spec"), dict)
                else None
            )
            try:
                calc_spec = parse_calculation_spec(
                    raw_spec,
                    task_id=task.task_id,
                    run_id=ctx.run_id,
                    policy=policy,
                )
            except ValueError as exc:
                # Invalid LLM shape must not abort the whole lab run.
                contract_error = str(exc)
                logger.error("CalculationSpec parse failed (soft-fail): %s", exc)
                calc_spec = None
            if calc_spec is None and policy.calculation_required and not contract_error:
                contract_error = "Calculation task requires calculation_spec in LLM output"
                logger.error("%s", contract_error)
            if calc_spec is not None:
                calc_spec, unit_warnings = sanitize_calculation_spec_units(calc_spec)
                if unit_warnings:
                    logger.error(
                        "CalculationSpec unit sanitize: %s", unit_warnings
                    )
                spec_errors = validate_calculation_spec(calc_spec)
                if spec_errors:
                    contract_error = f"Invalid CalculationSpec: {spec_errors}"
                    logger.error("CalculationSpec validation failed (soft-fail): %s", spec_errors)
                    # Keep audit copy, then drop contract so engineering cannot PASS.
                    if ctx.run_store is not None:
                        ctx.run_store.save_planner_json(
                            f"calculation_specs/invalid_{calc_spec.spec_id}.json",
                            {
                                "error": spec_errors,
                                "proposal": calc_spec.model_dump(mode="json"),
                            },
                        )
                    calc_spec = None

        try:
            exec_result = await ctx.tools.call(
                "python.execute",
                allowed=allowed,
                code=code,
                task_id=task.task_id,
            )
        except Exception as exc:
            logger.error("Simulation python.execute failed: %s", exc)
            raise

        if not exec_result.get("artifact"):
            logger.error("python.execute returned no ComputationArtifact")
            raise RuntimeError("python.execute must return a ComputationArtifact")
        artifact = ComputationArtifact.model_validate(exec_result["artifact"])
        artifact.kind = "calculation" if is_calculation else "simulation"
        artifact.task_id = task.task_id
        artifact.objective = task.objective
        declared = payload.get("declared_outputs") if isinstance(payload.get("declared_outputs"), dict) else {}
        artifact.declared_outputs = declared
        if calc_spec is not None:
            artifact.calculation_spec_id = calc_spec.spec_id
            artifact.metadata = {
                **artifact.metadata,
                "objective": calc_spec.objective or task.objective,
                "calculation_spec": calc_spec.model_dump(mode="json"),
                "inputs": {
                    name: None for name in calc_spec.required_inputs
                },
                "used_inputs": list(calc_spec.required_inputs),
            }
        else:
            artifact.metadata = {**artifact.metadata, "objective": task.objective}

        # Persist calculation spec under run planner namespace when available.
        paths: list[str] = []
        if calc_spec is not None and ctx.run_store is not None:
            rel = ctx.run_store.save_planner_json(
                f"calculation_specs/{calc_spec.spec_id}.json",
                calc_spec.model_dump(mode="json"),
            )
            paths.append(rel)
            # Keep a pointer for evidence completeness.
            specs = list(ctx.extra.get("calculation_specs") or [])
            specs.append(calc_spec)
            ctx.extra["calculation_specs"] = specs

        if calc_spec is not None:
            relevance = validate_computation_against_spec(artifact, calc_spec)
            artifact.metadata = {
                **artifact.metadata,
                "relevance": relevance.model_dump(mode="json"),
            }
            if not relevance.relevant:
                logger.error(
                    "Computation %s irrelevant to CalculationSpec %s: %s",
                    artifact.artifact_id,
                    calc_spec.spec_id,
                    relevance.reasons,
                )
            rel_list = list(ctx.extra.get("relevance_results") or [])
            rel_list.append(relevance)
            ctx.extra["relevance_results"] = rel_list

        if exec_result.get("artifact_saved"):
            if ctx.run_store is not None:
                paths.append(ctx.run_store.rel("computations", f"{artifact.artifact_id}.json"))
        elif ctx.run_store is not None:
            paths.append(ctx.run_store.save_computation(artifact))

        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="simulations/last_run.json",
            data={
                "artifact_id": artifact.artifact_id,
                "run_path": paths[-1] if paths else None,
                "calculation_spec_id": artifact.calculation_spec_id,
                "relevant": None if relevance is None else relevance.relevant,
            },
        )
        paths.append("simulations/last_run.json")

        claims: list[Claim] = []
        # Soft-fail / missing CalculationSpec cannot look "relevant by absence".
        if calc_spec is None and (is_calculation or policy.calculation_required):
            relevant_ok = False
        elif relevance is not None:
            relevant_ok = relevance.relevant
        else:
            relevant_ok = not (is_calculation or policy.calculation_required)
        # Declared output names this artifact actually produced (for coverage binding).
        declared_names = [str(k) for k in (declared or {}).keys()]
        for item in payload.get("claims") or []:
            math_check = item.get("math_check")
            if math_check and code and isinstance(math_check, dict) and "code" not in math_check:
                math_check = {**math_check, "code": code}
            if math_check is not None:
                math_check = normalize_math_check_payload(math_check)
            # Irrelevant / failed sandbox → OPINION, not CALCULATION evidence.
            kind = EvidenceKind.OPINION
            if exec_result.get("returncode") == 0 and relevant_ok:
                kind = EvidenceKind.CALCULATION
            # Explicit covers_outputs must refer to declared keys (cannot invent coverage).
            raw_covers = item.get("covers_outputs")
            if isinstance(raw_covers, list) and raw_covers:
                covers = [str(x) for x in raw_covers if str(x) in declared_names]
            else:
                covers = list(declared_names)
            claim = Claim(
                statement=str(item.get("statement") or ""),
                kind=kind,
                evidence=f"computation_artifact={artifact.artifact_id}",
                assumptions=coerce_str_list(item.get("assumptions")),
                falsifiers=coerce_str_list(item.get("falsifiers")),
                conditions={
                    "sandbox_returncode": exec_result.get("returncode"),
                    "computation_relevant": relevant_ok,
                    "calculation_spec_id": artifact.calculation_spec_id,
                    "covers_outputs": covers,
                },
                agent_id=self.role.value,
                math_check=math_check if relevant_ok else None,
                computation_artifact_id=artifact.artifact_id,
                confidence=ConfidenceBreakdown(
                    compute_check=0.8 if kind == EvidenceKind.CALCULATION else 0.1,
                    assumption_quality=0.4,
                ),
            )
            paths.append(ctx.evidence.save_claim(claim, subdirectory="calculations"))
            claims.append(claim)
            artifact.claim_ids.append(claim.claim_id)
            artifact.output_claim_ids.append(claim.claim_id)

        # Sidecar after claims so claim_ids are included.
        if ctx.run_store is not None and (
            artifact.calculation_spec_id or artifact.declared_outputs or calc_spec is not None
        ):
            paths.append(
                ctx.run_store.attach_computation_contract(
                    artifact.artifact_id,
                    task_id=artifact.task_id,
                    calculation_spec_id=artifact.calculation_spec_id,
                    objective=artifact.objective,
                    declared_outputs=artifact.declared_outputs,
                    metadata=artifact.metadata,
                    claim_ids=list(artifact.claim_ids),
                    output_claim_ids=list(artifact.output_claim_ids),
                    kind=artifact.kind,
                )
            )

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=(
                f"Simulation artifact={artifact.artifact_id} rc={exec_result.get('returncode')}"
                + (f" relevant={relevant_ok}" if relevance is not None else "")
            ),
            claims=claims,
            artifact_paths=paths,
            raw={
                "llm": payload,
                "exec": exec_result,
                "artifact_id": artifact.artifact_id,
                "calculation_spec": calc_spec.model_dump(mode="json") if calc_spec else None,
                "relevance": relevance.model_dump(mode="json") if relevance else None,
                "contract_error": contract_error,
            },
        )
