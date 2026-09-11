"""Orchestrator runtime — wires providers, tools, agents, and workflow loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import uuid4

from ai_lab.agents import build_agents
from ai_lab.agents.base import AgentContext
from ai_lab.config_loader import load_config
from ai_lab.core.enums import ProjectState
from ai_lab.core.models import (
    AgentResult,
    HitlRequest,
    LabConfig,
    ProjectSnapshot,
    RunEvent,
    TaskSpec,
)
from ai_lab.llm.registry import create_llm_provider
from ai_lab.memory.decision_log import DecisionLog
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.logger import get_logger
from ai_lab.observability.tracing import RunEventSink
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.tools.factory import build_tool_registry
from ai_lab.workflows.engine import WorkflowEngine
from ai_lab.workflows.example_pipeline import STAGE_ROLES

logger = get_logger(__name__)


class LabRuntime:
    """Main entry: run a project through the multi-agent state machine."""

    def __init__(
        self,
        project: ProjectStore,
        config: LabConfig,
        *,
        repo_root: Path,
        hitl: HitlGate | None = None,
        force_verification_fail: bool = False,
    ) -> None:
        self.project = project
        self.config = config
        self.repo_root = repo_root
        self.hitl = hitl or HitlGate(auto_approve=False)
        self.run_id = f"run_{uuid4().hex[:12]}"
        events_dir = repo_root / str(config.observability.get("run_events_dir", ".runs"))
        self.sink = RunEventSink(events_dir / f"{self.run_id}.jsonl")
        self.llm = create_llm_provider(
            config,
            cwd=project.root,
            force_verification_fail=force_verification_fail,
        )
        self.tools = build_tool_registry(
            project, config, run_id=self.run_id, sink=self.sink
        )
        self.evidence = EvidenceStore(project)
        self.decisions = DecisionLog(project.root / "decisions" / "decision_log.jsonl")
        self.agents = build_agents()
        self.project.ensure_layout()

    def _ctx(self) -> AgentContext:
        return AgentContext(
            run_id=self.run_id,
            store=self.project,
            evidence=self.evidence,
            decisions=self.decisions,
            tools=self.tools,
            llm=self.llm,
            config=self.config,
            sink=self.sink,
        )

    def _tasks_for_state(self, state: ProjectState) -> list[TaskSpec]:
        roles = STAGE_ROLES.get(state, [])
        tasks: list[TaskSpec] = []
        objective_base = f"Stage {state.value} for project {self.project.name}"
        for role in roles:
            objective = objective_base
            if state == ProjectState.SYNTHESIS:
                objective = f"synthesis: consolidate verified findings for {self.project.name}"
            if state == ProjectState.UNDERSTANDING:
                objective = f"Formalize and understand: {self.project.name}"
            tasks.append(
                TaskSpec(
                    role=role,
                    objective=objective,
                    state_context=state,
                    independence_group=state.value,
                )
            )
        return tasks

    async def _run_task(self, task: TaskSpec) -> AgentResult:
        agent = self.agents.get(task.role)
        if agent is None:
            raise KeyError(f"No agent registered for role {task.role}")
        ctx = self._ctx()
        t0 = time.perf_counter()
        self.sink.emit(
            RunEvent(
                run_id=self.run_id,
                agent_role=task.role.value,
                task_id=task.task_id,
                message="agent_start",
                data={"objective": task.objective, "state": str(task.state_context)},
            )
        )
        try:
            result = await agent.run(task, ctx)
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            self.sink.emit(
                RunEvent(
                    run_id=self.run_id,
                    agent_role=task.role.value,
                    task_id=task.task_id,
                    duration_ms=duration_ms,
                    status="error",
                    message=str(exc),
                )
            )
            logger.error("Agent %s failed: %s", task.role.value, exc)
            raise
        duration_ms = (time.perf_counter() - t0) * 1000
        self.sink.emit(
            RunEvent(
                run_id=self.run_id,
                agent_role=task.role.value,
                task_id=task.task_id,
                duration_ms=duration_ms,
                status="ok",
                message=result.summary,
            )
        )
        for decision in result.decisions:
            self.decisions.append(decision)
        return result

    async def _run_tasks(self, tasks: list[TaskSpec]) -> list[AgentResult]:
        if not tasks:
            return []
        parallel = bool(self.config.runtime.get("parallel_independent_groups", True))
        if parallel and len(tasks) > 1:
            return list(await asyncio.gather(*[self._run_task(t) for t in tasks]))
        results: list[AgentResult] = []
        for t in tasks:
            results.append(await self._run_task(t))
        return results

    async def run(self) -> ProjectSnapshot:
        snapshot = self.project.load_snapshot()
        snapshot.run_id = self.run_id
        if snapshot.state == ProjectState.CREATED:
            snapshot.state = ProjectState.UNDERSTANDING
        engine = WorkflowEngine(
            snapshot,
            hitl_on_disputed=bool(self.config.runtime.get("hitl_on_disputed", True)),
        )
        max_iter = int(self.config.runtime.get("max_iterations", 12))
        steps = 0

        while not engine.is_terminal() and steps < max_iter:
            steps += 1
            state = engine.state
            logger.info("Run %s state=%s step=%s", self.run_id, state.value, steps)

            if state == ProjectState.CREATED:
                engine.advance()
                self.project.save_snapshot(engine.snapshot)
                continue

            tasks = self._tasks_for_state(state)
            if not tasks:
                # Unknown/extension state — stop for human rather than inventing work
                logger.error("No stage roles for state %s — awaiting human", state.value)
                engine.set_state(ProjectState.AWAITING_HUMAN)
                self.project.save_snapshot(engine.snapshot)
                break

            results = await self._run_tasks(tasks)

            if state == ProjectState.VERIFICATION:
                report = next((r.verification for r in results if r.verification), None)
                if report is None:
                    raise RuntimeError("Verification stage produced no VerificationReport")
                engine.apply_verification(report)
            elif state == ProjectState.RED_TEAM:
                report = next((r.red_team for r in results if r.red_team), None)
                if report is None:
                    raise RuntimeError("Red team stage produced no RedTeamReport")
                nxt = engine.apply_red_team(report)
                if nxt == ProjectState.AWAITING_HUMAN:
                    decision = self.hitl.request(
                        HitlRequest(
                            reason="Red team raised critical findings",
                            options=["iterate", "accept_risk_and_synthesize", "abort"],
                        )
                    )
                    if decision.approved and decision.choice == "accept_risk_and_synthesize":
                        engine.set_state(ProjectState.SYNTHESIS)
                    elif decision.approved and decision.choice == "iterate":
                        engine.request_iteration()
                    # else remain AWAITING_HUMAN
            elif state == ProjectState.ITERATION_REQUIRED:
                # Rework done (theorist + simulation) → re-enter independent verification,
                # without replaying the entire early pipeline.
                engine.set_state(ProjectState.VERIFICATION)
            elif state == ProjectState.SYNTHESIS:
                engine.set_state(ProjectState.COMPLETED)
            else:
                engine.advance()

            self.project.save_snapshot(engine.snapshot)

        if steps >= max_iter and not engine.is_terminal():
            logger.error("Max iterations reached (%s); stopping", max_iter)
            engine.set_state(ProjectState.AWAITING_HUMAN)
            self.project.save_snapshot(engine.snapshot)

        return engine.snapshot


def repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[3]


async def run_project(
    project_name: str,
    *,
    provider: str | None = None,
    projects_dir: Path | None = None,
    config_path: Path | None = None,
    auto_approve_hitl: bool = False,
) -> ProjectSnapshot:
    root = repo_root_from_here()
    config = load_config(config_path or (root / "config" / "default.yaml"))
    if provider:
        config.provider = provider
    projects_dir = projects_dir or (root / "projects")
    store = ProjectStore.open(projects_dir, project_name)
    runtime = LabRuntime(
        store,
        config,
        repo_root=root,
        hitl=HitlGate(auto_approve=auto_approve_hitl),
    )
    return await runtime.run()
