"""Simulation / Computational Engineer — immutable computation artifacts + math_check."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_lab.agents.base import AgentContext, BaseAgent, llm_json
from ai_lab.core.enums import AgentRole, EvidenceKind
from ai_lab.core.models import AgentResult, Claim, ComputationArtifact, ConfidenceBreakdown, TaskSpec
from ai_lab.memory.run_store import hash_code
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


class SimulationAgent(BaseAgent):
    role = AgentRole.SIMULATION
    system_prompt = (
        "You are the Simulation Engineer. Propose short Python calculations and interpret results. "
        "Do not claim experimental FACT. JSON only with keys code, claims "
        "(claims may include math_check with expression/expected/inputs/units)."
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
        if not code:
            logger.error("Simulation agent returned empty code")
            raise ValueError("Simulation agent must return non-empty code")

        started = datetime.now(timezone.utc)
        try:
            exec_result = await ctx.tools.call(
                "python.execute",
                allowed=allowed,
                code=code,
            )
        except Exception as exc:
            logger.error("Simulation python.execute failed: %s", exc)
            raise
        finished = datetime.now(timezone.utc)

        artifact = ComputationArtifact(
            run_id=ctx.run_id,
            kind="simulation",
            input_hash=hash_code(task.objective),
            code_hash=hash_code(code),
            started_at=started,
            finished_at=finished,
            status="ok" if exec_result.get("returncode") == 0 else "error",
            code=code,
            stdout=str(exec_result.get("stdout") or ""),
            stderr=str(exec_result.get("stderr") or ""),
            returncode=exec_result.get("returncode"),
            result={"trust_level": exec_result.get("trust_level")},
            metadata={"objective": task.objective},
        )
        paths: list[str] = []
        if ctx.run_store is not None:
            paths.append(ctx.run_store.save_computation(artifact))
        # Convenience pointer (non-authoritative); immutable history lives in run store
        await ctx.tools.call(
            "artifacts.save",
            allowed=allowed,
            path="simulations/last_run.json",
            data={"artifact_id": artifact.artifact_id, "run_path": paths[-1] if paths else None},
        )
        paths.append("simulations/last_run.json")

        claims: list[Claim] = []
        for item in payload.get("claims") or []:
            math_check = item.get("math_check")
            if math_check and code and "code" not in math_check:
                # Attach sandbox recompute code for deterministic layer
                math_check = {**math_check, "code": code}
            claim = Claim(
                statement=str(item.get("statement") or ""),
                kind=EvidenceKind.CALCULATION
                if exec_result.get("returncode") == 0
                else EvidenceKind.OPINION,
                evidence=f"computation_artifact={artifact.artifact_id}",
                assumptions=list(item.get("assumptions") or []),
                falsifiers=list(item.get("falsifiers") or []),
                conditions={"sandbox_returncode": exec_result.get("returncode")},
                agent_id=self.role.value,
                math_check=math_check,
                computation_artifact_id=artifact.artifact_id,
                confidence=ConfidenceBreakdown(
                    compute_check=0.8 if exec_result.get("returncode") == 0 else 0.1,
                    assumption_quality=0.4,
                ),
            )
            paths.append(ctx.evidence.save_claim(claim, subdirectory="calculations"))
            claims.append(claim)
            artifact.claim_ids.append(claim.claim_id)

        return AgentResult(
            agent_role=self.role,
            task_id=task.task_id,
            summary=f"Simulation artifact={artifact.artifact_id} rc={exec_result.get('returncode')}",
            claims=claims,
            artifact_paths=paths,
            raw={"llm": payload, "exec": exec_result, "artifact_id": artifact.artifact_id},
        )
