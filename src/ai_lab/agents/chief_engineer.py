"""Chief Engineer — plans + gated synthesis from SynthesisBundle (not free-text truth)."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, coerce_str_list, llm_json
from ai_lab.core.enums import AgentRole, DecisionStatus, EvidenceKind
from ai_lab.core.models import (
    AgentResult,
    Claim,
    ConfidenceBreakdown,
    DecisionRecord,
    TaskSpec,
)
from ai_lab.orchestrator.synthesis import (
    build_synthesis_bundle,
    render_final_report,
    synthesis_allowed,
)


class ChiefEngineerAgent(BaseAgent):
    role = AgentRole.CHIEF_ENGINEER
    system_prompt = (
        "You are the Chief Engineer of an AI Engineering Lab. "
        "You formalize the problem, decompose work, detect contradictions, "
        "and schedule specialists. You do NOT decide what is proven — "
        "adjudication and evidence do. Return JSON only."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        problem = ""
        try:
            problem = ctx.store.read_text("problem.md")
        except FileNotFoundError:
            problem = task.objective

        is_synthesis = (
            "synthesis" in task.objective.lower()
            or (task.state_context is not None and task.state_context.value == "SYNTHESIS")
        )

        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                f"Objective: {task.objective}\n\nProblem file:\n{problem}\n\n"
                "Return JSON with keys: summary (string), understanding (string), "
                "unknowns (array of strings), follow_up_roles (array of role name strings), "
                "required_outputs (array of output names the quantitative answer must produce), "
                "expected_dimensions (object mapping each required_output name → unit string, "
                "e.g. power→W). required_outputs must reflect the problem, not a substitute task."
            ),
            schema_name="ChiefEngineerPlan",
        )

        path = "reviews/chief_understanding.json"
        # Understanding lock is immutable for the run: synthesis must not overwrite it.
        if not is_synthesis:
            await ctx.tools.call(
                "artifacts.save",
                allowed=ctx.allowed_tools_for(self.role, task),
                path=path,
                data=payload,
            )
            # Run-scoped snapshot so adjudication reads the locked policy, not later prose.
            if ctx.run_store is not None:
                ctx.run_store.save_review_json("chief_understanding.json", payload)
        else:
            # Synthesis notes are separate — never become VerificationPolicy.
            await ctx.tools.call(
                "artifacts.save",
                allowed=ctx.allowed_tools_for(self.role, task),
                path="reviews/chief_synthesis_notes.json",
                data=payload,
            )
            if ctx.run_store is not None:
                ctx.run_store.save_review_json("chief_synthesis_notes.json", payload)
            path = "reviews/chief_synthesis_notes.json"

        claim = Claim(
            statement=str(payload.get("understanding") or payload.get("summary") or ""),
            kind=EvidenceKind.INFERENCE,
            evidence="Chief Engineer problem framing",
            agent_id=self.role.value,
            assumptions=coerce_str_list(payload.get("unknowns"), limit=5),
            confidence=ConfidenceBreakdown(assumption_quality=0.4, source_quality=0.3),
            falsifiers=["Problem restatement rejected by human owner"],
        )
        claim_path = ctx.evidence.save_claim(claim, subdirectory="reviews")

        # Canonical DecisionLog owner is LabRuntime — return decision, do not append here
        decision = DecisionRecord(
            question="How should the lab approach this problem?",
            hypothesis=str(payload.get("summary") or ""),
            evidence=[claim.claim_id],
            assumptions=coerce_str_list(payload.get("unknowns")),
            agents_involved=[self.role.value],
            status=DecisionStatus.PROPOSED,
            next_action="Dispatch specialist agents",
            confidence=claim.confidence,
        )

        follow_ups: list[TaskSpec] = []
        for role_name in payload.get("follow_up_roles") or []:
            try:
                role = AgentRole(role_name)
            except ValueError:
                continue
            if role == AgentRole.CHIEF_ENGINEER:
                continue
            # Advisory only — LabRuntime executes the validated TaskGraph, not these.
            follow_ups.append(
                TaskSpec(
                    role=role,
                    objective=f"Contribute as {role.value} to: {task.objective}",
                    inputs=[claim_path, "problem.md"],
                )
            )

        artifact_paths = [path, claim_path]

        if is_synthesis:
            adjudication = ctx.extra.get("adjudication")
            verification = ctx.extra.get("verification_report")
            red_team = ctx.extra.get("red_team_report")
            claims = ctx.evidence.list_claims(include_superseded=False)
            decisions = ctx.decisions.read_all()
            bundle = build_synthesis_bundle(
                claims=claims,
                verification=verification,
                red_team=red_team,
                decisions=decisions,
                adjudication=adjudication,
                check_report=ctx.extra.get("check_report"),
                narrative=str(payload.get("summary") or ""),
            )
            await ctx.tools.call(
                "artifacts.save",
                allowed=ctx.allowed_tools_for(self.role, task),
                path="reviews/synthesis_bundle.json",
                data=bundle.model_dump(mode="json"),
            )
            artifact_paths.append("reviews/synthesis_bundle.json")
            # Run-scoped copy for benchmark evaluate / manifest provenance.
            if ctx.run_store is not None:
                ctx.run_store.save_review_json(
                    "synthesis_bundle.json", bundle.model_dump(mode="json")
                )

            if not synthesis_allowed(
                bundle,
                require_independent_review=bool(
                    ctx.extra.get("require_independent_review", True)
                ),
                require_red_team=bool(ctx.extra.get("require_red_team", True)),
            ):
                raise RuntimeError(
                    "Final report gate blocked: missing verification and/or red-team reports"
                )

            report = render_final_report(bundle, llm_polish=payload)
            await ctx.tools.call(
                "files.write",
                allowed=ctx.allowed_tools_for(self.role, task),
                path="final_report.md",
                content=report,
            )
            artifact_paths.append("final_report.md")

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=str(payload.get("summary") or ""),
            claims=[claim],
            decisions=[decision],
            artifact_paths=artifact_paths,
            follow_up_tasks=follow_ups,
            raw=payload,
        )
