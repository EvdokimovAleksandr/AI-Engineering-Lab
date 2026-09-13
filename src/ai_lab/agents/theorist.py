"""Theorist / Physics-Mathematics Agent."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, coerce_optional_str, coerce_str_list, llm_json
from ai_lab.core.enums import AgentRole, EvidenceKind
from ai_lab.core.models import AgentResult, Claim, ConfidenceBreakdown, Hypothesis, TaskSpec


class TheoristAgent(BaseAgent):
    role = AgentRole.THEORIST
    system_prompt = (
        "You are the Theorist. Build mathematical/physical models, state assumptions, "
        "and falsifiers. Prefer dimensional reasoning. JSON only."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                f"Objective: {task.objective}\n"
                "Return JSON with claims[] and hypotheses[] "
                "(statement,prediction,falsification_criteria,assumptions)."
            ),
            schema_name="TheoristOutput",
        )

        claims: list[Claim] = []
        paths: list[str] = []
        from ai_lab.core.execution_context import ExecutionContext, context_binding_dict

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

        for item in payload.get("claims") or []:
            claim = Claim(
                statement=str(item.get("statement") or ""),
                kind=EvidenceKind(item.get("kind", EvidenceKind.INFERENCE.value))
                if item.get("kind") in EvidenceKind._value2member_map_
                else EvidenceKind.INFERENCE,
                evidence=coerce_optional_str(item.get("evidence")),
                assumptions=coerce_str_list(item.get("assumptions")),
                falsifiers=coerce_str_list(item.get("falsifiers")),
                agent_id=self.role.value,
                confidence=ConfidenceBreakdown(assumption_quality=0.5, source_quality=0.3),
                project_id=binding["project_id"],
                investigation_id=binding["investigation_id"],
                task_id=binding["task_id"],
                run_id=binding["run_id"],
                contract_version=binding["contract_version"],
            )
            paths.append(ctx.evidence.save_claim(claim, subdirectory="calculations"))
            claims.append(claim)

        hypotheses: list[Hypothesis] = []
        for item in payload.get("hypotheses") or []:
            hyp = Hypothesis(
                statement=str(item.get("statement") or ""),
                prediction=str(item.get("prediction") or ""),
                falsification_criteria=coerce_str_list(item.get("falsification_criteria")),
                assumptions=coerce_str_list(item.get("assumptions")),
                agent_id=self.role.value,
            )
            hyp_path = f"hypotheses/{hyp.hypothesis_id}.json"
            await ctx.tools.call(
                "artifacts.save",
                allowed=allowed,
                path=hyp_path,
                data=hyp.model_dump(mode="json"),
            )
            paths.append(hyp_path)
            hypotheses.append(hyp)

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=f"Theorist produced {len(claims)} claims and {len(hypotheses)} hypotheses",
            claims=claims,
            hypotheses=hypotheses,
            artifact_paths=paths,
            raw=payload,
        )
