"""Simulation / Computational Engineer — proposes and runs Python calculations."""

from __future__ import annotations

from ai_lab.agents.base import AgentContext, BaseAgent, llm_json
from ai_lab.core.enums import AgentRole, EvidenceKind
from ai_lab.core.models import AgentResult, Claim, ConfidenceBreakdown, TaskSpec
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


class SimulationAgent(BaseAgent):
    role = AgentRole.SIMULATION
    system_prompt = (
        "You are the Simulation Engineer. Propose short Python calculations and interpret results. "
        "Do not claim experimental FACT. JSON only with keys code, claims."
    )

    async def run(self, task: TaskSpec, ctx: AgentContext) -> AgentResult:
        allowed = ctx.allowed_tools_for(self.role, task)
        payload = await llm_json(
            ctx,
            role=self.role,
            system=self.system_prompt,
            user=(
                f"Objective: {task.objective}\n"
                "Return JSON: {code: python snippet using only whitelisted imports, claims:[...]}"
            ),
            schema_name="SimulationOutput",
        )

        code = str(payload.get("code") or "").strip()
        exec_result: dict = {}
        if code:
            try:
                exec_result = await ctx.tools.call(
                    "python.execute",
                    allowed=allowed,
                    code=code,
                )
            except Exception as exc:
                # Unexpected sandbox failure must surface clearly for debugging
                logger.error("Simulation python.execute failed: %s", exc)
                raise
        else:
            logger.error("Simulation agent returned empty code")
            raise ValueError("Simulation agent must return non-empty code")

        sim_path = "simulations/last_run.json"
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path=sim_path,
            data={"code": code, "result": exec_result, "objective": task.objective},
        )

        claims: list[Claim] = []
        paths: list[str] = [sim_path]
        for item in payload.get("claims") or []:
            claim = Claim(
                statement=str(item.get("statement") or ""),
                kind=EvidenceKind.CALCULATION
                if exec_result.get("returncode") == 0
                else EvidenceKind.OPINION,
                evidence=f"stdout={exec_result.get('stdout', '')!r}",
                assumptions=list(item.get("assumptions") or []),
                falsifiers=list(item.get("falsifiers") or []),
                conditions={"sandbox_returncode": exec_result.get("returncode")},
                agent_id=self.role.value,
                confidence=ConfidenceBreakdown(
                    compute_check=0.8 if exec_result.get("returncode") == 0 else 0.1,
                    assumption_quality=0.4,
                ),
            )
            paths.append(ctx.evidence.save_claim(claim, subdirectory="calculations"))
            claims.append(claim)

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=f"Simulation returncode={exec_result.get('returncode')}",
            claims=claims,
            artifact_paths=paths,
            raw={"llm": payload, "exec": exec_result},
        )
