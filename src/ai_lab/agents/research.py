"""Research Agent — structured literature/source findings."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, llm_json
from ai_lab.core.enums import AgentRole, EvidenceKind
from ai_lab.core.models import (
    AgentResult,
    Claim,
    ConfidenceBreakdown,
    ResearchFinding,
    TaskSpec,
)


class ResearchAgent(BaseAgent):
    role = AgentRole.RESEARCH
    system_prompt = (
        "You are the Research Agent. Find existing knowledge and return structured findings. "
        "Never promote ASSUMPTION to FACT. JSON only."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        tool_result = await ctx.tools.call(
            "research.query",
            allowed=allowed,
            query=task.objective,
        )

        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                f"Objective: {task.objective}\n"
                f"Tool research stub: {tool_result}\n"
                "Return JSON: {findings:[{statement,kind,source,evidence,conditions,relevance,"
                "potential_contradiction,falsifiers,assumptions}]}"
            ),
            schema_name="ResearchFindings",
        )

        findings: list[ResearchFinding] = []
        paths: list[str] = []
        for item in payload.get("findings") or []:
            kind_raw = item.get("kind", EvidenceKind.INFERENCE.value)
            try:
                kind = EvidenceKind(kind_raw)
            except ValueError:
                kind = EvidenceKind.INFERENCE
            # Hard rule: refuse FACT without source/evidence
            if kind == EvidenceKind.FACT and not item.get("source") and not item.get("evidence"):
                kind = EvidenceKind.ASSUMPTION
            claim = Claim(
                statement=str(item.get("statement") or ""),
                kind=kind,
                source=item.get("source"),
                evidence=item.get("evidence"),
                conditions=dict(item.get("conditions") or {}),
                assumptions=list(item.get("assumptions") or []),
                falsifiers=list(item.get("falsifiers") or []),
                agent_id=self.role.value,
                confidence=ConfidenceBreakdown(
                    source_quality=0.5 if item.get("source") else 0.2,
                    assumption_quality=0.4,
                ),
            )
            rel = ctx.evidence.save_claim(claim, subdirectory="research")
            paths.append(rel)
            findings.append(
                ResearchFinding(
                    claim=claim,
                    relevance=float(item.get("relevance") or 0.5),
                    potential_contradiction=item.get("potential_contradiction"),
                )
            )

        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="research/research_batch.json",
            data={"findings": [f.model_dump(mode="json") for f in findings]},
        )
        paths.append("research/research_batch.json")

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=f"Collected {len(findings)} research findings",
            claims=[f.claim for f in findings],
            research_findings=findings,
            artifact_paths=paths,
            raw=payload,
        )
