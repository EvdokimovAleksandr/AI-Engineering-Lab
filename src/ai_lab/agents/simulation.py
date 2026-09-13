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
from ai_lab.tools.python_exec import SandboxSyntaxError, coerce_sandbox_code

logger = get_logger(__name__)


class SimulationAgent(BaseAgent):
    role = AgentRole.SIMULATION
    system_prompt = (
        "You are the Simulation Engineer. Propose a CalculationSpec, short Python "
        "calculations, structured declared_outputs, and claims with math_check. "
        "Do not claim experimental FACT. JSON only with keys: calculation_spec, "
        "code, declared_outputs, claims "
        "(claims may include math_check with expression/expected/inputs/units). "
        "declared_outputs must be {name: {value, unit}} matching calculation_spec. "
        "Formulas in JSON must not use raw LaTeX backslashes; write 4*F/(pi*d**2)."
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

        code = coerce_sandbox_code(payload.get("code"))
        if not code:
            logger.error("Simulation agent returned empty code")
            raise ValueError("Simulation agent must return non-empty code")

        calc_spec = None
        relevance = None
        contract_error: str | None = None
        from ai_lab.core.execution_context import (
            ContextMismatchError,
            ExecutionContext,
            context_binding_dict,
            require_artifact_context,
        )

        exec_ctx = ctx.execution_context
        if exec_ctx is None:
            exec_ctx = ExecutionContext.for_project_run(
                project_id=ctx.store.name,
                investigation_id=ctx.store.name,
                task_id=task.task_id,
                run_id=ctx.run_id,
            )
        elif not isinstance(exec_ctx, ExecutionContext):
            exec_ctx = ExecutionContext.model_validate(exec_ctx)

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
                    project_id=ctx.store.name,
                    investigation_id=ctx.store.name,
                    policy=policy,
                    execution_context=exec_ctx,
                )
            except ContextMismatchError:
                # Isolation errors must abort — never soft-fail into a foreign domain.
                raise
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

            # PR-B: MethodCompatibilityGate до sandbox — чужой метод не исполняем как валидный контракт.
            if calc_spec is not None:
                from ai_lab.checks.method_compatibility import (
                    check_method_compatibility,
                    problem_frame_from_agent_context,
                )

                frame = problem_frame_from_agent_context(ctx)
                compat = check_method_compatibility(calc_spec, frame)
                if not compat.compatible:
                    contract_error = (
                        f"MethodCompatibilityGate: {compat.codes}: {compat.reasons}"
                    )
                    logger.error("%s", contract_error)
                    if ctx.run_store is not None:
                        ctx.run_store.save_planner_json(
                            f"calculation_specs/incompatible_{calc_spec.spec_id}.json",
                            {
                                "error": contract_error,
                                "compatibility": compat.model_dump(mode="json"),
                                "proposal": calc_spec.model_dump(mode="json"),
                                "problem_frame": frame.model_dump(mode="json"),
                            },
                        )
                    # Контракт снят — execution без binding не даёт PASS (completeness).
                    calc_spec = None

        # Hard gate before sandbox: spec must belong to this task/run/investigation.
        if calc_spec is not None:
            require_artifact_context(exec_ctx, calc_spec, where="CalculationSpec.pre_execute")

        # Stamp identity before execute so ComputationArtifact is complete on first disk write.
        binding = context_binding_dict(exec_ctx)

        try:
            exec_result = await ctx.tools.call(
                "python.execute",
                allowed=allowed,
                code=code,
                task_id=binding["task_id"],
                project_id=binding["project_id"],
                investigation_id=binding["investigation_id"],
                contract_version=binding["contract_version"],
            )
        except SandboxSyntaxError as exc:
            # Invalid Python is a failed calculation, not a lab crash.
            logger.error("Simulation python.execute syntax error: %s\n%s", exc, code)
            return await self._syntax_error_result(
                task,
                ctx,
                allowed=allowed,
                payload=payload,
                code=code,
                calc_spec=calc_spec,
                exc=exc,
            )
        except Exception as exc:
            logger.error("Simulation python.execute failed: %s", exc)
            raise

        if not exec_result.get("artifact"):
            logger.error("python.execute returned no ComputationArtifact")
            raise RuntimeError("python.execute must return a ComputationArtifact")
        artifact = ComputationArtifact.model_validate(exec_result["artifact"])
        artifact.kind = "calculation" if is_calculation else "simulation"
        # Verify sandbox stamped the same ExecutionContext (no soft remapping).
        require_artifact_context(exec_ctx, artifact, where="ComputationArtifact.post_execute")
        artifact.task_id = binding["task_id"]
        artifact.run_id = binding["run_id"]
        artifact.project_id = binding["project_id"]
        artifact.investigation_id = binding["investigation_id"]
        artifact.contract_version = binding["contract_version"]
        if artifact.created_at is None:
            artifact.created_at = artifact.started_at
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
            from ai_lab.core.execution_context import require_write_execution_context

            # CalculationSpec JSON on disk must carry full identity (PR-C).
            require_write_execution_context(
                calc_spec, where="CalculationSpec.planner_write"
            )
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
                project_id=binding["project_id"],
                investigation_id=binding["investigation_id"],
                task_id=binding["task_id"],
                run_id=binding["run_id"],
                contract_version=binding["contract_version"],
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

    async def _syntax_error_result(
        self,
        task: TaskSpec,
        ctx: AgentContext,
        *,
        allowed: list[str],
        payload: dict,
        code: str,
        calc_spec: object | None,
        exc: SandboxSyntaxError,
    ) -> AgentResult:
        """Record a parse failure as OPINION so the run can continue without invented numbers."""
        from ai_lab.core.execution_context import (
            ExecutionContext,
            context_binding_dict,
        )

        exec_ctx = ctx.execution_context
        if exec_ctx is None:
            exec_ctx = ExecutionContext.for_project_run(
                project_id=ctx.store.name,
                investigation_id=ctx.store.name,
                task_id=task.task_id,
                run_id=ctx.run_id,
            )
        elif not isinstance(exec_ctx, ExecutionContext):
            exec_ctx = ExecutionContext.model_validate(exec_ctx)
        binding = context_binding_dict(exec_ctx)

        spec_dump = calc_spec.model_dump(mode="json") if calc_spec is not None else None
        spec_id = spec_dump.get("spec_id") if isinstance(spec_dump, dict) else None
        claim = Claim(
            statement=f"Calculation script is not valid Python: {exc}",
            kind=EvidenceKind.OPINION,
            evidence="sandbox_syntax_error",
            assumptions=["Sandbox code proposed by the model must parse as Python"],
            falsifiers=["Re-run with a syntactically valid snippet"],
            conditions={"sandbox_syntax_error": True, "calculation_spec_id": spec_id},
            agent_id=self.role.value,
            confidence=ConfidenceBreakdown(compute_check=0.0, assumption_quality=0.1),
            project_id=binding["project_id"],
            investigation_id=binding["investigation_id"],
            task_id=binding["task_id"],
            run_id=binding["run_id"],
            contract_version=binding["contract_version"],
        )
        paths: list[str] = [ctx.evidence.save_claim(claim, subdirectory="calculations")]
        if calc_spec is not None and ctx.run_store is not None and spec_id:
            paths.append(
                ctx.run_store.save_planner_json(
                    f"calculation_specs/{spec_id}.json",
                    spec_dump,
                )
            )
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="simulations/last_run.json",
            data={
                "artifact_id": None,
                "run_path": None,
                "calculation_spec_id": spec_id,
                "relevant": False,
                "syntax_error": str(exc),
                "code": code,
            },
        )
        paths.append("simulations/last_run.json")
        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary="Simulation sandbox syntax error (no computation evidence)",
            claims=[claim],
            artifact_paths=paths,
            raw={
                "llm": payload,
                "exec": None,
                "artifact_id": None,
                "calculation_spec": spec_dump,
                "relevance": None,
                "contract_error": str(exc),
            },
        )
