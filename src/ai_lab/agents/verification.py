"""Verification Agent — independent distrust of prior results."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, llm_json
from ai_lab.core.enums import AgentRole, VerificationStatus
from ai_lab.core.models import AgentResult, TaskSpec, VerificationReport


class VerificationAgent(BaseAgent):
    role = AgentRole.VERIFICATION
    system_prompt = (
        "You are the Verification Agent. Do NOT trust other agents. "
        "Re-check assumptions, dimensions, and logic. "
        "Status must be one of PASS, FAIL, DISPUTED, INSUFFICIENT_EVIDENCE. JSON only."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        # Independent view: claims only, no author reasoning transcripts
        claims = [c.model_dump(mode="json") for c in ctx.evidence.list_claims()]
        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                f"Objective: {task.objective}\n"
                f"Claims to verify (blind of author chat):\n{claims}\n"
                "Return JSON: {status, discrepancies, recomputed, notes}"
            ),
            schema_name="VerificationReport",
        )

        status_raw = str(payload.get("status") or VerificationStatus.INSUFFICIENT_EVIDENCE.value)
        try:
            status = VerificationStatus(status_raw)
        except ValueError:
            status = VerificationStatus.INSUFFICIENT_EVIDENCE

        report = VerificationReport(
            target_claim_ids=[c["claim_id"] for c in claims],
            status=status,
            discrepancies=list(payload.get("discrepancies") or []),
            recomputed=dict(payload.get("recomputed") or {}),
            notes=str(payload.get("notes") or ""),
            agent_id=self.role.value,
        )
        path = f"reviews/{report.report_id}.json"
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path=path,
            data=report.model_dump(mode="json"),
        )
        # Stable pointer for orchestrator
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="reviews/last_verification.json",
            data=report.model_dump(mode="json"),
        )

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=f"Verification status={report.status.value}",
            verification=report,
            artifact_paths=[path, "reviews/last_verification.json"],
            raw=payload,
        )
