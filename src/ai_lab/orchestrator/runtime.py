"""Orchestrator runtime — wires providers, tools, agents, and workflow loop."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import uuid4

from ai_lab.agents import build_agents
from ai_lab.agents.base import AgentContext
from ai_lab.checks import run_deterministic_checks
from ai_lab.config_loader import load_config
from ai_lab.core.enums import AdjudicationStatus, AgentRole, GraphEdgeType, GraphNodeType, ProjectState
from ai_lab.core.models import (
    AgentResult,
    HitlRequest,
    LabConfig,
    ProjectSnapshot,
    RunEvent,
    TaskSpec,
    VerificationReport,
)
from ai_lab.llm.registry import create_llm_provider
from ai_lab.knowledge import KnowledgeService
from ai_lab.knowledge.graph import JsonEvidenceRepository
from ai_lab.core.models import GraphEdge
from ai_lab.memory.decision_log import DecisionLog
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.review_bundle import build_review_bundle
from ai_lab.memory.run_store import RunStore
from ai_lab.observability.logger import get_logger
from ai_lab.observability.tracing import RunEventSink
from ai_lab.orchestrator.adjudication import adjudicate
from ai_lab.orchestrator.budget import BudgetExceeded, budget_from_config, check_budget
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.iteration_policy import next_iteration_state
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
        resume_run_id: str | None = None,
    ) -> None:
        self.project = project
        self.config = config
        self.repo_root = repo_root
        self.hitl = hitl or HitlGate(auto_approve=False)
        # Resume only when explicitly requested — never auto-continue terminal/failed runs
        self._is_resume = resume_run_id is not None
        if resume_run_id:
            self.run_id = resume_run_id
        else:
            self.run_id = f"run_{uuid4().hex[:12]}"
        events_dir = repo_root / str(config.observability.get("run_events_dir", ".runs"))
        self.sink = RunEventSink(events_dir / f"{self.run_id}.jsonl")
        self.budget = budget_from_config(config)
        self.llm = create_llm_provider(
            config,
            cwd=None,  # never bind Cursor to project root
            force_verification_fail=force_verification_fail,
        )
        self.tools = build_tool_registry(
            project, config, run_id=self.run_id, sink=self.sink, budget=self.budget
        )
        self.knowledge = KnowledgeService(project, run_id=self.run_id)
        self.evidence = EvidenceStore(project, run_id=self.run_id)
        self.decisions = DecisionLog(project.root / "decisions" / "decision_log.jsonl")
        self.graph = self.knowledge.graph  # JsonEvidenceRepository
        self.run_store = RunStore(project, self.run_id)
        self.agents = build_agents()
        self.project.ensure_layout()
        self._last_verification: VerificationReport | None = None
        self._last_red_team = None
        self._last_adjudication = None
        self._last_check_report = None

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
            run_store=self.run_store,
            graph=self.graph,
            knowledge=self.knowledge,
            budget=self.budget,
            extra={
                "adjudication": self._last_adjudication,
                "verification_report": self._last_verification,
                "red_team_report": self._last_red_team,
            },
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
        check_budget(self.budget)
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
        # Canonical DecisionLog persistence (agents must not also append)
        for decision in result.decisions:
            self.decisions.append(decision)
        # HITL from agents
        if result.hitl_request and result.hitl_request.blocking:
            raise _HitlInterrupt(result.hitl_request)
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

    async def _run_independent_review(self) -> AdjudicationStatus:
        """Deterministic checks → frozen ReviewBundle → V ∥ RT → adjudication.

        Uses CURRENT_RUN claims only — no stale leakage from prior runs.
        """
        claims = self.evidence.list_claims(include_superseded=False)
        comps = [a.model_dump(mode="json") for a in self.run_store.list_computations()]

        async def _exec(code: str) -> dict:
            return await self.tools.call(
                "python.execute",
                allowed=["python.execute"],
                code=code,
            )

        from ai_lab.memory.review_bundle import claim_to_blind_view

        blind = [claim_to_blind_view(c) for c in claims]
        check_report = await run_deterministic_checks(blind, execute_code=_exec)
        self._last_check_report = check_report

        bundle = build_review_bundle(
            run_id=self.run_id,
            claims=claims,
            computation_artifacts=comps,
            check_report=check_report,
        )
        bundle_path = "reviews/review_bundle.json"
        self.project.write_json(bundle_path, bundle.model_dump(mode="json"))
        self.run_store.save_review_json("review_bundle.json", bundle.model_dump(mode="json"))

        v_task = TaskSpec(
            role=AgentRole.VERIFICATION,
            objective="Independent verification of ReviewBundle",
            state_context=ProjectState.VERIFICATION,
            independence_group="independent_review",
            review_bundle_path=bundle_path,
        )
        rt_task = TaskSpec(
            role=AgentRole.RED_TEAM,
            objective="Independent red-team attack on ReviewBundle",
            state_context=ProjectState.VERIFICATION,
            independence_group="independent_review",
            review_bundle_path=bundle_path,
        )
        # Parallel — neither sees the other's report (only shared frozen bundle)
        v_result, rt_result = await asyncio.gather(self._run_task(v_task), self._run_task(rt_task))
        self._last_verification = v_result.verification
        self._last_red_team = rt_result.red_team

        # Ensure isolation artifact: red team raw must not contain verification report id
        if rt_result.raw and self._last_verification:
            dumped = str(rt_result.raw)
            if self._last_verification.report_id in dumped:
                logger.error("Red team output appears to reference verification report id")
                raise RuntimeError("Review isolation violated: red team saw verification id")

        adj = adjudicate(
            check_report=check_report,
            verification=self._last_verification,
            red_team=self._last_red_team,
        )
        self._last_adjudication = adj
        self.project.write_json(
            "reviews/last_adjudication.json", adj.model_dump(mode="json")
        )
        self.run_store.save_review_json("last_adjudication.json", adj.model_dump(mode="json"))

        # Evidence graph links (CURRENT_RUN claims only)
        if self._last_verification and claims:
            v_node = self.graph.ensure_node(
                node_type=GraphNodeType.VERIFICATION,
                ref_id=self._last_verification.report_id,
                run_id=self.run_id,
                label="verification",
                created_by="verification",
            )
            c_node = self.graph.ensure_node(
                node_type=GraphNodeType.CLAIM,
                ref_id=claims[0].claim_id,
                run_id=self.run_id,
                label=claims[0].claim_id,
            )
            self.graph.add_edge(
                GraphEdge(
                    edge_type=GraphEdgeType.TESTS,
                    source_id=v_node.node_id,
                    target_id=c_node.node_id,
                    project_id=self.project.name,
                    run_id=self.run_id,
                )
            )
        return adj.status

    async def run(self) -> ProjectSnapshot:
        snapshot = self.project.load_snapshot()
        snapshot.run_id = self.run_id
        terminal = {
            ProjectState.COMPLETED,
            ProjectState.BUDGET_EXCEEDED,
            ProjectState.DISPUTED,
        }
        if snapshot.state == ProjectState.CREATED:
            snapshot.state = ProjectState.UNDERSTANDING
        elif snapshot.state in terminal and not self._is_resume:
            snapshot.state = ProjectState.UNDERSTANDING
            snapshot.iteration = 0
            snapshot.adjudication_status = None
            snapshot.pending_hitl = None
            logger.info("Starting fresh run (previous state was terminal)")
        elif snapshot.state == ProjectState.AWAITING_HUMAN and not self._is_resume:
            # Without --resume, start a new investigation rather than sticking on HITL
            snapshot.state = ProjectState.UNDERSTANDING
            snapshot.iteration = 0
            snapshot.adjudication_status = None
            snapshot.pending_hitl = None
            logger.info("Starting fresh run (previous state AWAITING_HUMAN; pass --resume to continue)")

        self.run_store.build_manifest(
            config=self.config,
            repo_root=self.repo_root,
            budget=self.budget,
            model_id=next(iter(self.config.models.values()), "unknown"),
        )

        engine = WorkflowEngine(
            snapshot,
            hitl_on_disputed=bool(self.config.runtime.get("hitl_on_disputed", True)),
        )
        max_iter = int(self.budget.max_iterations)
        steps = 0

        try:
            while not engine.is_terminal() and steps < max_iter:
                steps += 1
                check_budget(self.budget)
                state = engine.state
                logger.info("Run %s state=%s step=%s", self.run_id, state.value, steps)

                if state == ProjectState.CREATED:
                    engine.advance()
                    self.project.save_snapshot(engine.snapshot)
                    continue

                # Resume HITL if pending
                if state == ProjectState.AWAITING_HUMAN:
                    break

                if state == ProjectState.VERIFICATION:
                    try:
                        adj_status = await self._run_independent_review()
                    except _HitlInterrupt as hitl_exc:
                        engine.snapshot.pending_hitl = hitl_exc.request.model_dump(mode="json")
                        engine.set_state(ProjectState.AWAITING_HUMAN)
                        self.project.save_snapshot(engine.snapshot)
                        break

                    engine.snapshot.adjudication_status = adj_status
                    if adj_status == AdjudicationStatus.PASS:
                        engine.set_state(ProjectState.SYNTHESIS)
                    else:
                        engine.snapshot.iteration += 1
                        # Critical red-team / disputed may need human
                        if adj_status == AdjudicationStatus.DISPUTED and self.config.runtime.get(
                            "hitl_on_disputed", True
                        ):
                            if self._last_red_team and (
                                self._last_red_team.recommended_reject
                                or (
                                    self._last_red_team.max_severity
                                    and self._last_red_team.max_severity.value
                                    in {"HIGH", "CRITICAL"}
                                )
                            ):
                                decision = self.hitl.request(
                                    HitlRequest(
                                        reason="Adjudication DISPUTED with critical red-team findings",
                                        options=["iterate", "accept_risk_and_synthesize", "abort"],
                                        context={"adjudication": adj_status.value},
                                    )
                                )
                                if decision.approved and decision.choice == "accept_risk_and_synthesize":
                                    engine.set_state(ProjectState.SYNTHESIS)
                                elif decision.approved and decision.choice == "iterate":
                                    nxt = next_iteration_state(
                                        adjudication=self._last_adjudication,
                                        verification=self._last_verification,
                                        check_report=self._last_check_report,
                                    )
                                    engine.set_state(nxt)
                                else:
                                    engine.set_state(ProjectState.AWAITING_HUMAN)
                                    engine.snapshot.pending_hitl = {
                                        "reason": "critical red team",
                                        "adjudication": adj_status.value,
                                    }
                                self.project.save_snapshot(engine.snapshot)
                                continue

                        nxt = next_iteration_state(
                            adjudication=self._last_adjudication,
                            verification=self._last_verification,
                            check_report=self._last_check_report,
                        )
                        engine.set_state(nxt)
                    self.project.save_snapshot(engine.snapshot)
                    continue

                tasks = self._tasks_for_state(state)
                if not tasks:
                    logger.error("No stage roles for state %s — awaiting human", state.value)
                    engine.set_state(ProjectState.AWAITING_HUMAN)
                    self.project.save_snapshot(engine.snapshot)
                    break

                try:
                    await self._run_tasks(tasks)
                except _HitlInterrupt as hitl_exc:
                    engine.snapshot.pending_hitl = hitl_exc.request.model_dump(mode="json")
                    engine.set_state(ProjectState.AWAITING_HUMAN)
                    self.project.save_snapshot(engine.snapshot)
                    break
                except BudgetExceeded as exc:
                    logger.error("Budget exceeded: %s", exc)
                    engine.set_state(ProjectState.BUDGET_EXCEEDED)
                    self.project.save_snapshot(engine.snapshot)
                    break

                if state == ProjectState.ITERATION_REQUIRED:
                    engine.set_state(ProjectState.VERIFICATION)
                elif state == ProjectState.SYNTHESIS:
                    engine.set_state(ProjectState.COMPLETED)
                elif state in {
                    ProjectState.CALCULATION,
                    ProjectState.SIMULATION,
                    ProjectState.ANALYSIS,
                    ProjectState.RESEARCH,
                    ProjectState.HYPOTHESIS,
                } and engine.snapshot.iteration > 0:
                    # After iteration re-entry stages, go back to independent review
                    # once we complete the targeted stage (advance within iteration path)
                    if state in {ProjectState.CALCULATION, ProjectState.SIMULATION}:
                        engine.set_state(ProjectState.VERIFICATION)
                    else:
                        engine.advance()
                else:
                    engine.advance()

                self.project.save_snapshot(engine.snapshot)

        except BudgetExceeded as exc:
            logger.error("Budget exceeded: %s", exc)
            engine.set_state(ProjectState.BUDGET_EXCEEDED)
            self.project.save_snapshot(engine.snapshot)

        if steps >= max_iter and not engine.is_terminal():
            logger.error("Max iterations reached (%s); stopping", max_iter)
            engine.set_state(ProjectState.BUDGET_EXCEEDED)
            self.project.save_snapshot(engine.snapshot)

        self.run_store.finish_manifest(final_state=engine.snapshot.state.value)
        if engine.snapshot.state == ProjectState.COMPLETED:
            try:
                self.knowledge.runs.freeze_run(self.run_id)
            except Exception as exc:
                logger.error("Failed to freeze completed run: %s", exc)
                raise
        return engine.snapshot


class _HitlInterrupt(Exception):
    def __init__(self, request: HitlRequest) -> None:
        super().__init__(request.reason)
        self.request = request


def repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[3]


async def run_project(
    project_name: str,
    *,
    provider: str | None = None,
    projects_dir: Path | None = None,
    config_path: Path | None = None,
    auto_approve_hitl: bool = False,
    resume: bool = False,
) -> ProjectSnapshot:
    root = repo_root_from_here()
    config = load_config(config_path or (root / "config" / "default.yaml"))
    if provider:
        config.provider = provider
    projects_dir = projects_dir or (root / "projects")
    store = ProjectStore.open(projects_dir, project_name)
    snap = store.load_snapshot()
    resume_id = snap.run_id if resume else None
    runtime = LabRuntime(
        store,
        config,
        repo_root=root,
        hitl=HitlGate(auto_approve=auto_approve_hitl),
        resume_run_id=resume_id,
    )
    return await runtime.run()
