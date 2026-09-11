"""Red Team Agent — blind ReviewBundle only; never sees VerificationReport."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, llm_json
from ai_lab.core.enums import AgentRole, AttackSeverity
from ai_lab.core.models import AgentResult, RedTeamAttack, RedTeamReport, ReviewBundle, TaskSpec


class RedTeamAgent(BaseAgent):
    role = AgentRole.RED_TEAM
    system_prompt = (
        "You are Red Team. Your job is to REJECT the hypothesis if possible. "
        "You receive a blind ReviewBundle only — no VerificationReport, no author confidence. "
        "Search for counterexamples, bad assumptions, physical-law violations, "
        "numerical instability, and circular reasoning. "
        "For tensile/simulation claims, attack circular cross-section, linear elasticity "
        "at the reported strain, parameter provenance, and diameter uncertainty. "
        "Answers are claims/objections, not deterministic physical truth. Do not confirm. JSON only."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        bundle = await self._load_bundle(task, ctx)
        # Explicitly strip check conclusions that might bias toward "already failed"
        # Red team should attack claims, not echo verification.
        bundle_for_rt = bundle.model_copy(update={"check_report": None})

        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                "Blind ReviewBundle under attack (no verification results):\n"
                f"{bundle_for_rt.model_dump(mode='json')}\n"
                "Return JSON: {recommended_reject, summary, "
                "attacks:[{description,severity,category,target_claim_ids}]}"
            ),
            schema_name="RedTeamReport",
        )

        attacks: list[RedTeamAttack] = []
        for item in payload.get("attacks") or []:
            sev_raw = str(item.get("severity") or AttackSeverity.MEDIUM.value)
            try:
                severity = AttackSeverity(sev_raw)
            except ValueError:
                severity = AttackSeverity.MEDIUM
            attacks.append(
                RedTeamAttack(
                    target_claim_ids=list(item.get("target_claim_ids") or []),
                    description=str(item.get("description") or ""),
                    severity=severity,
                    category=str(item.get("category") or "unspecified"),
                )
            )

        report = RedTeamReport(
            attacks=attacks,
            recommended_reject=bool(payload.get("recommended_reject")),
            summary=str(payload.get("summary") or ""),
            agent_id=self.role.value,
        )
        path = f"reviews/{report.report_id}.json"
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path=path,
            data=report.model_dump(mode="json"),
        )
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="reviews/last_red_team.json",
            data=report.model_dump(mode="json"),
        )
        if ctx.run_store is not None:
            ctx.run_store.save_review_json(
                f"{report.report_id}.json", report.model_dump(mode="json")
            )

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=report.summary or f"Red team attacks={len(attacks)}",
            red_team=report,
            artifact_paths=[path, "reviews/last_red_team.json"],
            raw=payload,
        )

    async def _load_bundle(self, task: TaskSpec, ctx: AgentContext) -> ReviewBundle:
        if task.review_bundle_path:
            data = ctx.store.read_json(task.review_bundle_path)
            return ReviewBundle.model_validate(data)
        return ReviewBundle(run_id=ctx.run_id, claims=[])
