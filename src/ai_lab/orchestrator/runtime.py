"""Orchestrator runtime — wires providers, tools, agents, and workflow loop."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ai_lab.agents import build_agents
from ai_lab.agents.base import AgentContext
from ai_lab.checks import run_deterministic_checks
from ai_lab.config_loader import load_config
from ai_lab.core.enums import (
    AdjudicationStatus,
    AgentRole,
    AgreementType,
    GraphEdgeType,
    GraphNodeType,
    ProjectState,
    ResearchOutcome,
    ScopeStatus,
    TaskKind,
    TaskStatus,
)
from ai_lab.core.investigation import InvestigationScope, ResearchSufficiencyReport
from ai_lab.core.models import (
    AgentResult,
    HitlRequest,
    LabConfig,
    ProjectSnapshot,
    RunEvent,
    TaskExecutionRecord,
    TaskGraph,
    TaskSpec,
    VerificationReport,
)
from ai_lab.llm.registry import create_llm_router
from ai_lab.llm.config import (
    KNOWN_PROVIDER_IDS,
    apply_provider_override,
    independence_policy_from_config,
    routing_policy_from_config,
)
from ai_lab.llm.independence import ArchitectureFlags
from ai_lab.llm.policy import validate_routing_policy
from ai_lab.llm.router import RoutingContext
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
from ai_lab.orchestrator.scope import (
    apply_clarification,
    hitl_request_for_scope,
    lock_scope,
    original_problem_hash,
    resolve_scope,
)
from ai_lab.planner.context import ProblemContext
from ai_lab.planner.dag import ready_task_ids
from ai_lab.planner.factory import create_planner
from ai_lab.planner.hashing import task_graph_hash
from ai_lab.planner.iteration import graph_for_iteration
from ai_lab.planner.pipeline import plan_with_recovery
from ai_lab.planner.schemas import KNOWN_TOOL_NAMES, REVIEW_ROLES
from ai_lab.planner.static import StaticPlanner
from ai_lab.planner.validator import TaskGraphValidationContext, validate_task_graph
from ai_lab.task_routing.models import RoutingDecision
from ai_lab.task_routing.policy import task_routing_policy_from_config
from ai_lab.task_routing.profiles import profile_to_pipeline_name
from ai_lab.task_routing.router import TaskRouter
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
        problem_override: str | None = None,
        simulation_fixture: str | None = None,
    ) -> None:
        self.project = project
        self.config = config
        self.repo_root = repo_root
        self.hitl = hitl or HitlGate(auto_approve=False)
        # UI/CLI problem text is UNTRUSTED_DATA — stored on the run, never as trusted config.
        self.problem_override = problem_override
        # Resume only when explicitly requested — never auto-continue terminal/failed runs
        self._is_resume = resume_run_id is not None
        if resume_run_id:
            self.run_id = resume_run_id
        else:
            self.run_id = f"run_{uuid4().hex[:12]}"
        events_dir = repo_root / str(config.observability.get("run_events_dir", ".runs"))
        self.sink = RunEventSink(events_dir / f"{self.run_id}.jsonl")
        self.budget = budget_from_config(config)
        self.routing_policy = routing_policy_from_config(config)
        self.independence_policy = independence_policy_from_config(config)
        routing_result = validate_routing_policy(
            self.routing_policy,
            KNOWN_PROVIDER_IDS,
            independence_policy=self.independence_policy,
            architecture=ArchitectureFlags(
                review_contexts_differ=True,
                frozen_blind_bundle=True,
                parallel_review=bool(config.runtime.get("parallel_independent_groups", True)),
            ),
        )
        if not routing_result.ok:
            logger.error("Routing policy invalid: %s", routing_result.errors)
            raise RuntimeError(f"Invalid routing policy: {routing_result.errors}")
        self._independence_assessment = routing_result.assessment
        self.knowledge = KnowledgeService(project, run_id=self.run_id)
        self.evidence = EvidenceStore(project, run_id=self.run_id)
        self.decisions = DecisionLog(project.root / "decisions" / "decision_log.jsonl")
        self.graph = self.knowledge.graph  # JsonEvidenceRepository
        self.run_store = RunStore(project, self.run_id)
        self.llm = create_llm_router(
            config,
            cwd=None,  # never bind Cursor to project root
            force_verification_fail=force_verification_fail,
            simulation_fixture=simulation_fixture,
            routing_context=RoutingContext(
                run_id=self.run_id,
                sink=self.sink,
                run_store=self.run_store,
                frozen_blind_bundle=True,
                parallel_review=bool(config.runtime.get("parallel_independent_groups", True)),
                review_contexts_differ=True,
            ),
            skip_policy_validation=True,
        )
        self.tools = build_tool_registry(
            project,
            config,
            run_id=self.run_id,
            sink=self.sink,
            budget=self.budget,
            knowledge=self.knowledge,
            repo_root=repo_root,
            run_store=self.run_store,
        )
        self.agents = build_agents()
        self.project.ensure_layout()
        self._last_verification: VerificationReport | None = None
        self._last_red_team = None
        self._last_adjudication = None
        self._last_check_report = None
        self._last_evidence_completeness = None
        self._calculation_specs: list = []
        self._relevance_results: list = []
        self._last_simulation_spec = None
        self._last_simulation_result = None
        self._task_graph: TaskGraph | None = None
        self._task_statuses: dict[str, TaskStatus] = {}
        self._executions: list[TaskExecutionRecord] = []
        self._task_graph_node_id: str | None = None
        self._routing_decision: RoutingDecision | None = None
        self._task_routing_policy = task_routing_policy_from_config(config)
        # Tracks which UI stages already received stage.started (real graph, not fake timer).
        self._ui_stages_started: set[str] = set()
        # Why the run stopped without an engineering result (budget / max iterations).
        self.stop_reason: str | None = None
        # V2.8 investigation scope (locked after the scope gate).
        self._investigation_scope: InvestigationScope | None = None
        self._last_research_sufficiency: ResearchSufficiencyReport | None = None

    def _stage_for_task(self, task: TaskSpec) -> str:
        """Map a TaskGraph node to a UI pipeline stage from real task metadata."""
        if task.state_context is not None:
            return task.state_context.value
        if task.task_kind == TaskKind.MODEL_BUILD:
            return ProjectState.CALCULATION.value
        if task.task_kind in {TaskKind.SIMULATION, TaskKind.SIMULATION_VERIFICATION}:
            return ProjectState.SIMULATION.value
        if task.task_kind == TaskKind.DETERMINISTIC_CHECK:
            return ProjectState.VERIFICATION.value
        if task.task_kind == TaskKind.ADJUDICATION:
            return "ADJUDICATION"
        if task.role == AgentRole.RESEARCH:
            return ProjectState.RESEARCH.value
        if task.role == AgentRole.THEORIST:
            return ProjectState.HYPOTHESIS.value
        if task.role == AgentRole.SIMULATION:
            return ProjectState.SIMULATION.value
        if task.role == AgentRole.VERIFICATION:
            return ProjectState.VERIFICATION.value
        if task.role == AgentRole.RED_TEAM:
            return ProjectState.RED_TEAM.value
        if task.role == AgentRole.CHIEF_ENGINEER:
            return ProjectState.UNDERSTANDING.value
        return "PLANNING"

    def _emit_lifecycle(self, message: str, *, status: str = "ok", **data: object) -> None:
        """Durable lifecycle event for UI/SSE — does not invent progress."""
        self.sink.emit(
            RunEvent(
                run_id=self.run_id,
                message=message,
                status=status,
                data={k: v for k, v in data.items() if v is not None},
            )
        )

    def _maybe_complete_stage(self, stage: str) -> None:
        """Emit stage.completed only when every TaskGraph node in that stage is done."""
        graph = self._task_graph
        if graph is None:
            return
        stage_tasks = [t for t in graph.tasks if self._stage_for_task(t) == stage]
        if not stage_tasks:
            return
        done = {TaskStatus.SUCCESS, TaskStatus.SKIPPED}
        if all(self._task_statuses.get(t.task_id) in done for t in stage_tasks):
            self._emit_lifecycle("stage.completed", stage=stage)

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
                "check_report": self._last_check_report,
                "independence_assessment": self._independence_assessment,
                "require_independent_review": (
                    self._routing_decision.require_independent_review
                    if self._routing_decision is not None
                    else True
                ),
                "require_red_team": (
                    self._routing_decision.require_red_team
                    if self._routing_decision is not None
                    else True
                ),
                "require_calculation": (
                    self._routing_decision.require_calculation
                    if self._routing_decision is not None
                    else False
                ),
                "require_verification": (
                    self._routing_decision.require_verification
                    if self._routing_decision is not None
                    else True
                ),
                "workflow_profile": (
                    self._routing_decision.final_workflow.value
                    if self._routing_decision is not None
                    else None
                ),
                "calculation_specs": list(getattr(self, "_calculation_specs", None) or []),
                "relevance_results": list(getattr(self, "_relevance_results", None) or []),
                "investigation_scope": self._investigation_scope,
                "research_sufficiency": self._last_research_sufficiency,
            },
        )

    def _problem_context(self) -> ProblemContext:
        def _read(name: str) -> str:
            try:
                return self.project.read_text(name)
            except FileNotFoundError:
                return ""

        problem_text = self.problem_override if self.problem_override is not None else _read("problem.md")
        extra = {}
        if self.problem_override is not None:
            extra["ui_problem.md"] = self.problem_override
        original = problem_text
        resolved = ""
        if self._investigation_scope is not None:
            original = self._investigation_scope.original_problem
            resolved = self._investigation_scope.objective
            extra["resolved_scope.json"] = self._investigation_scope.model_dump_json()
        return ProblemContext(
            project_id=self.project.name,
            run_id=self.run_id,
            problem_text=problem_text,
            requirements_text=_read("requirements.md"),
            assumptions_text=_read("assumptions.md"),
            budget=self.budget,
            allowed_tools=tuple(sorted(KNOWN_TOOL_NAMES)),
            extra_data=extra,
            original_problem=original,
            resolved_objective=resolved,
        )

    def _original_problem_text(self) -> str:
        """Immutable user prompt for this run (UI override or project problem.md)."""
        orig_rel = self.run_store.rel("inputs", "original_problem.md")
        orig_path = self.project.root / orig_rel
        if orig_path.is_file():
            return orig_path.read_text(encoding="utf-8")
        text = self.problem_override
        if text is None:
            try:
                text = self.project.read_text("problem.md")
            except FileNotFoundError:
                text = ""
        if not str(text).strip():
            logger.error("Cannot resolve scope: original problem is empty")
            raise ValueError("original_problem is empty")
        self.project.write_text(orig_rel, str(text))
        return str(text)

    def _persist_scope(self, scope: InvestigationScope) -> None:
        self._investigation_scope = scope
        payload = scope.model_dump(mode="json")
        self.run_store.save_planner_json("scope.json", payload)
        try:
            manifest = self.run_store.load_manifest()
            manifest.investigation_scope = payload
            manifest.original_problem_hash = original_problem_hash(scope.original_problem)
            self.run_store.save_manifest(manifest)
        except FileNotFoundError:
            logger.info("Scope saved as planner artifact (manifest not yet written)")

    def _load_scope(self) -> InvestigationScope | None:
        path = self.project.root / self.run_store.rel("planner", "scope.json")
        if not path.is_file():
            return None
        import json as _json

        return InvestigationScope.model_validate(_json.loads(path.read_text(encoding="utf-8")))

    def _max_clarification_rounds(self) -> int:
        return int(self.config.runtime.get("max_clarification_rounds", 2))

    async def _resolve_and_gate_scope(self) -> InvestigationScope:
        """Scope gate before TaskRouter/Planner. May raise _HitlInterrupt."""
        original = self._original_problem_text()
        prior = self._load_scope()
        if prior is not None and prior.original_problem != original:
            # Keep the first snapshot; UI/problem.md must not rewrite it mid-run.
            original = prior.original_problem
        self._emit_lifecycle("stage.started", stage="SCOPE_RESOLUTION")
        scope = resolve_scope(
            original,
            prior=prior,
            max_clarification_rounds=self._max_clarification_rounds(),
        )
        self._persist_scope(scope)
        self._emit_lifecycle(
            "scope.resolved",
            stage="SCOPE_RESOLUTION",
            status=scope.status.value,
            locked=scope.locked,
            objective=scope.objective,
        )
        while scope.status == ScopeStatus.SCOPE_NEEDS_CLARIFICATION:
            req = hitl_request_for_scope(scope)
            decision = self.hitl.request(req)
            if not decision.approved:
                self._emit_lifecycle(
                    "scope.clarification_required",
                    status="warn",
                    stage="SCOPE_RESOLUTION",
                    question=(scope.clarification.question if scope.clarification else req.reason),
                )
                raise _HitlInterrupt(req)
            scope = apply_clarification(
                scope,
                choice=decision.choice,
                note=decision.note,
                answers=decision.answers,
            )
            scope = resolve_scope(
                scope.original_problem,
                prior=scope,
                max_clarification_rounds=self._max_clarification_rounds(),
            )
            self._persist_scope(scope)
        if scope.status in {ScopeStatus.SCOPE_RESOLVED, ScopeStatus.SCOPE_ASSUMED}:
            scope = lock_scope(scope)
            self._persist_scope(scope)
            self._emit_lifecycle("stage.completed", stage="SCOPE_RESOLUTION")
            return scope
        if scope.status == ScopeStatus.SCOPE_UNRESOLVED:
            self._emit_lifecycle(
                "scope.unresolved",
                status="warn",
                stage="SCOPE_RESOLUTION",
            )
            raise _ScopeUnresolved(scope)
        logger.error("Unexpected scope status %s", scope.status.value)
        raise RuntimeError(f"Unexpected scope status {scope.status.value}")

    def _resume_from_hitl(self, pending: dict) -> bool:
        """Apply a stored HITL answer. Returns True if the run should continue.

        Unknown/legacy pending payloads keep the old pause behaviour so existing
        resume tests that seed a dummy HITL stay paused.
        """
        ctx = pending.get("context") if isinstance(pending, dict) else None
        kind = ""
        if isinstance(ctx, dict):
            kind = str(ctx.get("kind") or "")
        action = str(pending.get("requested_action") or "")
        if kind != "scope_clarification" and action != "clarify_scope":
            return False
        req = HitlRequest.model_validate(pending) if pending.get("reason") else hitl_request_for_scope(
            self._load_scope() or resolve_scope(self._original_problem_text())
        )
        decision = self.hitl.request(req)
        if not decision.approved:
            return False
        prior = self._load_scope()
        if prior is None:
            prior = resolve_scope(self._original_problem_text())
        prior = apply_clarification(
            prior, choice=decision.choice, note=decision.note, answers=decision.answers
        )
        self._persist_scope(prior)
        return True

    def _finish_unresolved_scope(self, engine: WorkflowEngine, scope: InvestigationScope) -> None:
        """Honest incomplete run: no TaskGraph, no invented FACT conclusion."""
        from ai_lab.core.models import AdjudicationResult

        reasons = [
            f"scope_status={scope.status.value}",
            scope.rationale or "Investigation scope could not be locked.",
        ]
        adj = AdjudicationResult(
            status=AdjudicationStatus.INSUFFICIENT_EVIDENCE,
            reasons=reasons,
            scope_status=scope.status.value,
        )
        adj.engineering_outcome = adj.status
        self._last_adjudication = adj
        self.run_store.save_review_json("last_adjudication.json", adj.model_dump(mode="json"))
        engine.set_state(ProjectState.COMPLETED)
        self.project.save_snapshot(engine.snapshot)
        self._write_scope_gated_report(scope, adj)

    def _write_scope_gated_report(
        self, scope: InvestigationScope, adj
    ) -> None:
        from ai_lab.orchestrator.synthesis import build_synthesis_bundle, render_final_report

        bundle = build_synthesis_bundle(
            claims=[],
            verification=None,
            red_team=None,
            decisions=[],
            adjudication=adj,
            narrative="The laboratory could not lock a sufficiently specific investigation scope.",
        )
        bundle.scope = scope.model_dump(mode="json")
        bundle.evidence_gaps = list(scope.ambiguity) + list(scope.unknown_parameters)
        report = render_final_report(bundle)
        self.project.write_text("final_report.md", report)
        self.run_store.save_text("final_report.md", report)
        self.run_store.save_review_json("synthesis_bundle.json", bundle.model_dump(mode="json"))

    def _save_planner_state(
        self,
        *,
        proposal,
        graph: TaskGraph | None,
        validation,
        rejected_proposal=None,
        rejected_validation=None,
        resolution=None,
    ) -> None:
        if proposal is not None:
            dump = proposal.model_dump(mode="json") if hasattr(proposal, "model_dump") else proposal
            self.run_store.save_planner_json("proposal.json", dump)
        if rejected_proposal is not None:
            dump = (
                rejected_proposal.model_dump(mode="json")
                if hasattr(rejected_proposal, "model_dump")
                else rejected_proposal
            )
            self.run_store.save_planner_json("rejected_proposal.json", dump)
        if rejected_validation is not None:
            self.run_store.save_planner_json(
                "rejected_validation.json", rejected_validation.model_dump(mode="json")
            )
        if resolution is not None:
            payload = resolution.model_dump(mode="json") if hasattr(resolution, "model_dump") else resolution
            self.run_store.save_planner_json("resolution.json", payload)
            try:
                self.run_store.attach_planner(payload)
            except FileNotFoundError:
                logger.info("Planner resolution saved as artifact (manifest not yet written)")
        if graph is not None:
            payload = graph.model_dump(mode="json")
            self.run_store.save_planner_json("task_graph.json", payload)
            self.run_store.save_planner_json(f"task_graph_v{graph.version}.json", payload)
        self.run_store.save_planner_json("validation.json", validation.model_dump(mode="json"))
        self._write_executions()

    def _write_executions(self) -> None:
        self.run_store.save_planner_json(
            "executions.json",
            [e.model_dump(mode="json") for e in self._executions],
        )

    def _record_task_graph_provenance(self, graph: TaskGraph, graph_hash: str) -> None:
        """RUN ← PART_OF ← TASK_GRAPH so decisions can be traced to the plan."""
        run_node = self.graph.ensure_node(
            node_type=GraphNodeType.RUN,
            ref_id=self.run_id,
            run_id=self.run_id,
            label=self.run_id,
            payload={"task_graph_hash": graph_hash, "task_graph_id": graph.graph_id},
        )
        tg_node = self.graph.ensure_node(
            node_type=GraphNodeType.TASK_GRAPH,
            ref_id=f"{graph.graph_id}@v{graph.version}",
            run_id=self.run_id,
            label=graph.graph_id,
            created_by="planner",
            payload={
                "hash": graph_hash,
                "version": graph.version,
                "task_ids": [t.task_id for t in graph.tasks],
            },
        )
        self._task_graph_node_id = tg_node.node_id
        self.graph.ensure_edge(
            edge_type=GraphEdgeType.PART_OF,
            source_id=tg_node.node_id,
            target_id=run_node.node_id,
            run_id=self.run_id,
        )

    def _record_task_outputs(self, task: TaskSpec, result: AgentResult) -> None:
        if self._task_graph_node_id is None:
            return
        for claim in result.claims:
            c_node = self.graph.ensure_node(
                node_type=GraphNodeType.CLAIM,
                ref_id=claim.claim_id,
                run_id=self.run_id,
                label=claim.claim_id,
                created_by=task.task_id,
                metadata={"task_id": task.task_id},
            )
            self.graph.ensure_edge(
                edge_type=GraphEdgeType.DERIVED_FROM,
                source_id=c_node.node_id,
                target_id=self._task_graph_node_id,
                run_id=self.run_id,
            )
        for decision in result.decisions:
            d_node = self.graph.ensure_node(
                node_type=GraphNodeType.DECISION,
                ref_id=decision.decision_id,
                run_id=self.run_id,
                label=decision.decision_id,
                created_by=task.task_id,
                metadata={"task_id": task.task_id},
            )
            self.graph.ensure_edge(
                edge_type=GraphEdgeType.DERIVED_FROM,
                source_id=d_node.node_id,
                target_id=self._task_graph_node_id,
                run_id=self.run_id,
            )

    def _resolve_pipeline_for_planner(self) -> str | None:
        """Pick TaskGraph template. Explicit uniaxial_tension config wins over router."""
        configured = str((self.config.simulation or {}).get("pipeline") or "default").strip().lower()
        if configured == "uniaxial_tension":
            return None  # factory reads simulation.pipeline
        if not self._task_routing_policy.enabled:
            return None
        if self._routing_decision is None:
            return None
        return profile_to_pipeline_name(self._routing_decision.final_workflow)

    def _route_task(self) -> RoutingDecision | None:
        """Run TaskRouter before planning. Returns None when routing is disabled."""
        if not self._task_routing_policy.enabled:
            return None
        # Trusted simulation override: do not re-route away from tensile benchmark.
        configured = str((self.config.simulation or {}).get("pipeline") or "default").strip().lower()
        if configured == "uniaxial_tension":
            return None
        router = TaskRouter(self._task_routing_policy)
        # Route on the posed problem, not scaffold requirements/assumptions —
        # those can pollute classification when UI overrides problem.md.
        full_ctx = self._problem_context()
        scope = self._investigation_scope
        # Locked scope drives routing: objective + key terms + original (immutable) keywords.
        # Objective alone can hyphenate terms ("spider-silk") and miss classifier phrases.
        parts = [
            full_ctx.resolved_objective,
            " ".join(scope.key_terms) if scope is not None else "",
            full_ctx.original_problem or full_ctx.problem_text,
        ]
        extra = {}
        if scope is not None and scope.pipeline_hint:
            extra["pipeline_hint"] = scope.pipeline_hint
        route_ctx = ProblemContext(
            project_id=full_ctx.project_id,
            run_id=full_ctx.run_id,
            problem_text="\n".join(p for p in parts if p),
            requirements_text="",
            assumptions_text="",
            budget=full_ctx.budget,
            allowed_tools=full_ctx.allowed_tools,
            extra_data=extra,
            original_problem=full_ctx.original_problem,
            resolved_objective=full_ctx.resolved_objective,
        )
        decision = router.route(route_ctx)
        self._routing_decision = decision
        self.run_store.save_planner_json("task_routing.json", decision.public_dump())
        try:
            manifest = self.run_store.load_manifest()
            manifest.task_routing_decision = decision.public_dump()
            manifest.workflow_profile = decision.final_workflow.value
            self.run_store.save_manifest(manifest)
        except FileNotFoundError:
            # Manifest is created at run() start; plan_project may route before that.
            logger.info("Task routing saved to planner artifact (manifest not yet written)")
        if decision.require_hitl:
            # Policy-mandated HITL before execution — same interrupt path as plan HITL.
            raise _HitlInterrupt(
                HitlRequest(
                    reason="TaskRoutingPolicy requires human review before execution",
                    options=["approve_routing", "reject_routing"],
                    context={
                        "workflow": decision.final_workflow.value,
                        "evidence": [e.value for e in decision.final_evidence],
                        "risk": decision.classification.risk,
                    },
                )
            )
        return decision

    async def _prepare_task_graph(self) -> TaskGraph:
        """Scope gate → Router (optional) → planner proposes; only a validated DAG is executed."""
        await self._resolve_and_gate_scope()
        self._route_task()
        pipeline_override = self._resolve_pipeline_for_planner()
        planner = create_planner(
            self.config, llm=self.llm, pipeline_override=pipeline_override
        )
        context = self._problem_context()
        vctx = TaskGraphValidationContext(
            budget=self.budget,
            routing_policy=self.routing_policy,
            independence_policy=self.independence_policy,
            available_providers=KNOWN_PROVIDER_IDS,
        )
        # Fallback profile follows TaskRouter, not a hardcoded STANDARD graph.
        fallback_pipeline = pipeline_override or str(
            (self.config.simulation or {}).get("pipeline") or "default"
        ).strip().lower()
        fallback_planner = None
        if getattr(planner, "name", None) == "llm":
            fallback_planner = StaticPlanner(pipeline=fallback_pipeline)
        outcome = await plan_with_recovery(
            planner,
            context,
            validation_context=vctx,
            fallback_planner=fallback_planner,
            fallback_profile=fallback_pipeline,
        )
        resolution = outcome.resolution
        if outcome.rejected_validation is not None:
            self._emit_lifecycle(
                "planner.proposal_rejected",
                status="warn",
                planner=resolution.requested,
                reason=resolution.rejection_reason,
                failure_class=resolution.failure_class,
                validation_errors=list(resolution.validation_errors),
                fallback=resolution.fallback,
                fallback_profile=resolution.fallback_profile,
                retry_count=resolution.retry_count,
            )
        if resolution.recovered:
            self._emit_lifecycle(
                "planner.recovered",
                status="ok",
                fallback="static",
                fallback_profile=resolution.fallback_profile,
                final_planner=resolution.final_planner_type,
            )
        self._save_planner_state(
            proposal=outcome.proposal,
            graph=outcome.graph,
            validation=outcome.validation,
            rejected_proposal=outcome.rejected_proposal,
            rejected_validation=outcome.rejected_validation,
            resolution=resolution,
        )
        graph = outcome.graph
        validation = outcome.validation
        proposal = outcome.proposal
        if not validation.ok or graph is None:
            logger.error(
                "TaskGraph rejected: %s %s",
                validation.reason.value,
                validation.errors,
            )
            raise RuntimeError(
                f"TaskGraph invalid: {validation.reason.value}: {validation.errors}"
            )

        hitl_plan = bool(self.config.runtime.get("hitl_on_plan", False))
        if hitl_plan or (proposal is not None and proposal.requires_human_approval):
            req = HitlRequest(
                reason="TaskGraph requires human approval before execution",
                options=["approve_plan", "reject_plan"],
                context={"graph_id": graph.graph_id, "hash": validation.graph_hash},
            )
            decision = self.hitl.request(req)
            if (not decision.approved) or decision.choice == "reject_plan":
                raise _HitlInterrupt(req)

        graph_hash = validation.graph_hash or task_graph_hash(graph)
        self._task_graph = graph
        self._task_statuses = {t.task_id: TaskStatus.PENDING for t in graph.tasks}
        self.run_store.attach_task_graph(
            graph_id=graph.graph_id,
            graph_hash=graph_hash,
            version=graph.version,
        )
        self._record_task_graph_provenance(graph, graph_hash)
        return graph

    def _install_graph(self, graph: TaskGraph) -> None:
        validation = validate_task_graph(
            graph,
            TaskGraphValidationContext(
                budget=self.budget,
                routing_policy=self.routing_policy,
                independence_policy=self.independence_policy,
                available_providers=KNOWN_PROVIDER_IDS,
            ),
        )
        self._save_planner_state(proposal=None, graph=graph, validation=validation)
        if not validation.ok:
            raise RuntimeError(
                f"Revised TaskGraph invalid: {validation.reason.value}: {validation.errors}"
            )
        graph_hash = validation.graph_hash or task_graph_hash(graph)
        self._task_graph = graph
        self._task_statuses = {t.task_id: TaskStatus.PENDING for t in graph.tasks}
        self.run_store.attach_task_graph(
            graph_id=graph.graph_id,
            graph_hash=graph_hash,
            version=graph.version,
        )
        self._record_task_graph_provenance(graph, graph_hash)

    def _tasks_for_state(self, state: ProjectState) -> list[TaskSpec]:
        """Compatibility helper: stage table is NOT the execution DAG."""
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
        ctx.extra["task_id"] = task.task_id
        ctx.extra["independence_group"] = task.independence_group
        ctx.extra["frozen_blind_bundle"] = task.role in REVIEW_ROLES
        ctx.extra["parallel_review"] = task.role in REVIEW_ROLES
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
            # Propagate calculation contract state from agent context into runtime.
            # IMPORTANT: ctx.extra may hold the same list object as self._*_ lists
            # (passed by reference from _ctx). Never append while iterating that object.
            incoming_specs = ctx.extra.get("calculation_specs") or []
            if incoming_specs is self._calculation_specs:
                incoming_specs = list(incoming_specs)
            existing_ids = {s.spec_id for s in self._calculation_specs}
            for spec in incoming_specs:
                sid = getattr(spec, "spec_id", None)
                if sid and sid not in existing_ids:
                    self._calculation_specs.append(spec)
                    existing_ids.add(sid)
            incoming_rels = ctx.extra.get("relevance_results") or []
            if incoming_rels is self._relevance_results:
                # Agent did not replace the list — nothing new to merge.
                incoming_rels = []
            for rel in incoming_rels:
                self._relevance_results.append(rel)
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

    async def _execute_graph_task(self, task: TaskSpec, engine: WorkflowEngine) -> None:
        """Run one validated node. System kinds never go through the LLM agent map."""
        self._task_statuses[task.task_id] = TaskStatus.RUNNING
        if task.state_context is not None:
            engine.set_state(task.state_context)
        stage = self._stage_for_task(task)
        # Emit stage.started once per stage from the real TaskGraph wave.
        if stage not in self._ui_stages_started:
            self._ui_stages_started.add(stage)
            self._emit_lifecycle("stage.started", stage=stage, task_id=task.task_id)
        self._emit_lifecycle(
            "task.started",
            stage=stage,
            task_id=task.task_id,
            task_kind=task.task_kind.value if task.task_kind else None,
            role=task.role.value if task.role else None,
        )
        started = datetime.now(timezone.utc)
        artifact_paths: list[str] = []
        claim_ids: list[str] = []
        pending_reentry: AdjudicationStatus | None = None
        try:
            if task.task_kind == TaskKind.MODEL_BUILD:
                paths, claims = await self._run_model_build(task)
                artifact_paths = paths
                claim_ids = claims
            elif task.task_kind == TaskKind.SIMULATION:
                paths, claims = await self._run_engineering_simulation(task)
                artifact_paths = paths
                claim_ids = claims
            elif task.task_kind == TaskKind.SIMULATION_VERIFICATION:
                artifact_paths = await self._run_simulation_verification(task)
            elif task.task_kind == TaskKind.DETERMINISTIC_CHECK:
                await self._run_deterministic_review_prep()
                artifact_paths = ["reviews/review_bundle.json"]
            elif task.task_kind == TaskKind.ADJUDICATION:
                adj_status = self._finish_adjudication()
                engine.snapshot.adjudication_status = adj_status
                artifact_paths = ["reviews/last_adjudication.json"]
                pending_reentry = adj_status
            else:
                if task.role is None:
                    raise RuntimeError(f"AGENT task {task.task_id} missing role")
                exec_task = task
                if task.role in REVIEW_ROLES:
                    exec_task = task.model_copy(
                        update={"review_bundle_path": "reviews/review_bundle.json"}
                    )
                result = await self._run_task(exec_task)
                self._record_task_outputs(task, result)
                artifact_paths = list(result.artifact_paths)
                claim_ids = [c.claim_id for c in result.claims]
                if task.role == AgentRole.VERIFICATION:
                    self._last_verification = result.verification
                if task.role == AgentRole.RED_TEAM:
                    self._last_red_team = result.red_team
                if task.role == AgentRole.RESEARCH:
                    suff = (result.raw or {}).get("research_sufficiency")
                    if suff:
                        self._last_research_sufficiency = ResearchSufficiencyReport.model_validate(suff)
                        self.run_store.save_review_json(
                            "research_sufficiency.json",
                            self._last_research_sufficiency.model_dump(mode="json"),
                        )
            self._task_statuses[task.task_id] = TaskStatus.SUCCESS
            self._executions.append(
                TaskExecutionRecord(
                    task_id=task.task_id,
                    status=TaskStatus.SUCCESS,
                    role=task.role.value if task.role else None,
                    task_kind=task.task_kind,
                    artifact_paths=artifact_paths,
                    claim_ids=claim_ids,
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            self._emit_lifecycle(
                "task.completed",
                stage=stage,
                task_id=task.task_id,
                task_kind=task.task_kind.value if task.task_kind else None,
                role=task.role.value if task.role else None,
                artifact_count=len(artifact_paths),
            )
            self._maybe_complete_stage(stage)
            if pending_reentry is not None and pending_reentry != AdjudicationStatus.PASS:
                await self._after_adjudication(engine, pending_reentry)
        except _HitlInterrupt:
            self._task_statuses[task.task_id] = TaskStatus.HITL_REQUIRED
            self._executions.append(
                TaskExecutionRecord(
                    task_id=task.task_id,
                    status=TaskStatus.HITL_REQUIRED,
                    role=task.role.value if task.role else None,
                    task_kind=task.task_kind,
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                    error="HITL_REQUIRED",
                )
            )
            self._emit_lifecycle(
                "task.failed",
                status="error",
                stage=stage,
                task_id=task.task_id,
                reason="HITL_REQUIRED",
            )
            raise
        except Exception as exc:
            self._task_statuses[task.task_id] = TaskStatus.FAILED
            self._executions.append(
                TaskExecutionRecord(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    role=task.role.value if task.role else None,
                    task_kind=task.task_kind,
                    started_at=started,
                    finished_at=datetime.now(timezone.utc),
                    error=str(exc),
                )
            )
            logger.error("Task %s failed: %s", task.task_id, exc)
            self._emit_lifecycle(
                "task.failed",
                status="error",
                stage=stage,
                task_id=task.task_id,
                reason=str(exc),
            )
            self._emit_lifecycle(
                "stage.failed", status="error", stage=stage, task_id=task.task_id, reason=str(exc)
            )
            raise

    async def _execute_graph_step(self, engine: WorkflowEngine) -> bool:
        """Execute the current ready wave. Returns False when nothing is eligible."""
        graph = self._task_graph
        if graph is None:
            raise RuntimeError("No validated TaskGraph loaded")
        ready = ready_task_ids(graph.tasks, self._task_statuses)
        if not ready:
            return False
        by_id = {t.task_id: t for t in graph.tasks}
        ready_tasks = [by_id[tid] for tid in ready]
        v_ready = [t for t in ready_tasks if t.role == AgentRole.VERIFICATION]
        rt_ready = [t for t in ready_tasks if t.role == AgentRole.RED_TEAM]
        others = [
            t
            for t in ready_tasks
            if t.role not in REVIEW_ROLES
        ]
        parallel = bool(self.config.runtime.get("parallel_independent_groups", True))
        if others:
            if parallel and len(others) > 1:
                await asyncio.gather(*[self._execute_graph_task(t, engine) for t in others])
            else:
                for t in others:
                    await self._execute_graph_task(t, engine)
        if v_ready and rt_ready:
            await asyncio.gather(
                self._execute_graph_task(v_ready[0], engine),
                self._execute_graph_task(rt_ready[0], engine),
            )
            if self._last_red_team is not None and self._last_verification is not None:
                dumped = str(self._last_red_team.model_dump(mode="json"))
                if self._last_verification.report_id in dumped:
                    logger.error("Red team output appears to reference verification report id")
                    raise RuntimeError("Review isolation violated: red team saw verification id")
        elif v_ready:
            await self._execute_graph_task(v_ready[0], engine)
        elif rt_ready:
            await self._execute_graph_task(rt_ready[0], engine)
        self._write_executions()
        return True

    async def _after_adjudication(
        self, engine: WorkflowEngine, adj_status: AdjudicationStatus
    ) -> None:
        """Verdict handling. TaskStatus stays SUCCESS; IterationPolicy is unchanged.

        V2.6: SIMPLE quantitative runs still execute synthesis so the report can
        honestly show INSUFFICIENT_EVIDENCE/FAIL. Technical COMPLETED ≠ PASS.
        """
        if adj_status == AdjudicationStatus.PASS:
            return
        graph = self._task_graph
        if graph is None:
            return

        # SIMPLE / checks-only profile: do not skip synthesis or re-enter forever.
        simple_path = (
            self._routing_decision is not None
            and not self._routing_decision.require_independent_review
        )
        # V2.8: bounded research recovery already ran. Iterating the TaskGraph cannot
        # invent sources; complete with an honest INSUFFICIENT/PARTIAL report.
        research_terminal = False
        rs = self._last_research_sufficiency
        if rs is not None and rs.research_required:
            research_terminal = rs.outcome in {
                ResearchOutcome.RESEARCH_EMPTY,
                ResearchOutcome.RESEARCH_FILTERED,
                ResearchOutcome.RESEARCH_PARTIAL,
                ResearchOutcome.RESEARCH_PROVIDER_ERROR,
            }
        if simple_path or research_terminal:
            # Leave synthesis PENDING so grounded (non-PASS) report is written.
            return

        for task in graph.tasks:
            if (
                task.state_context == ProjectState.SYNTHESIS
                and self._task_statuses.get(task.task_id) == TaskStatus.PENDING
            ):
                self._task_statuses[task.task_id] = TaskStatus.SKIPPED
        engine.snapshot.iteration += 1
        if adj_status == AdjudicationStatus.DISPUTED and self.config.runtime.get(
            "hitl_on_disputed", True
        ):
            if self._last_red_team and (
                self._last_red_team.recommended_reject
                or (
                    self._last_red_team.max_severity
                    and self._last_red_team.max_severity.value in {"HIGH", "CRITICAL"}
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
                    for task in graph.tasks:
                        if task.state_context == ProjectState.SYNTHESIS:
                            self._task_statuses[task.task_id] = TaskStatus.PENDING
                    return
                if decision.approved and decision.choice == "iterate":
                    nxt = next_iteration_state(
                        adjudication=self._last_adjudication,
                        verification=self._last_verification,
                        check_report=self._last_check_report,
                    )
                    self._reenter_graph(nxt, reason=f"hitl_iterate:{adj_status.value}")
                    return
                raise _HitlInterrupt(
                    HitlRequest(
                        reason="critical red team",
                        options=["iterate", "accept_risk_and_synthesize", "abort"],
                        context={"adjudication": adj_status.value},
                    )
                )
        nxt = next_iteration_state(
            adjudication=self._last_adjudication,
            verification=self._last_verification,
            check_report=self._last_check_report,
        )
        self._reenter_graph(nxt, reason=f"adjudication:{adj_status.value}")

    def _reenter_graph(self, nxt: ProjectState, *, reason: str) -> None:
        """Extension point: IterationPolicy target becomes TaskGraph vN."""
        if self._task_graph is None:
            raise RuntimeError("Cannot revise TaskGraph: none loaded")
        revised = graph_for_iteration(self._task_graph, nxt, reason=reason)
        self._install_graph(revised)

    def _finalize_graph(self, engine: WorkflowEngine) -> None:
        graph = self._task_graph
        if graph is None:
            return
        synthesis = [t for t in graph.tasks if t.state_context == ProjectState.SYNTHESIS]
        if synthesis and all(
            self._task_statuses.get(t.task_id) == TaskStatus.SUCCESS for t in synthesis
        ):
            # Technical completion — engineering_outcome lives on adjudication/manifest.
            engine.set_state(ProjectState.COMPLETED)
            return
        if any(s == TaskStatus.HITL_REQUIRED for s in self._task_statuses.values()):
            engine.set_state(ProjectState.AWAITING_HUMAN)
            return
        if engine.snapshot.adjudication_status == AdjudicationStatus.DISPUTED:
            engine.set_state(ProjectState.DISPUTED)
            return
        # SIMPLE: synthesis after non-PASS still completes technically.
        simple_path = (
            self._routing_decision is not None
            and not self._routing_decision.require_independent_review
        )
        if (
            simple_path
            and engine.snapshot.adjudication_status is not None
            and synthesis
            and all(
                self._task_statuses.get(t.task_id) in {TaskStatus.SUCCESS, TaskStatus.SKIPPED}
                for t in synthesis
            )
        ):
            engine.set_state(ProjectState.COMPLETED)
            return
        if engine.snapshot.adjudication_status and engine.snapshot.adjudication_status != AdjudicationStatus.PASS:
            if engine.state not in {
                ProjectState.AWAITING_HUMAN,
                ProjectState.BUDGET_EXCEEDED,
            }:
                engine.set_state(ProjectState.ITERATION_REQUIRED)

    async def _run_model_build(self, task: TaskSpec) -> tuple[list[str], list[str]]:
        """Load a trusted SimulationSpec and validate it. No solver, no LLM."""
        from ai_lab.simulation.load import load_trusted_spec
        from ai_lab.simulation.validate import validate_simulation_spec

        spec_id = str((task.metadata or {}).get("spec_id") or "uniaxial_tension")
        spec = load_trusted_spec(spec_id, repo_root=self.repo_root)
        validation = validate_simulation_spec(spec)
        if not validation.ok:
            logger.error("MODEL_BUILD rejected spec %s: %s", spec_id, validation.errors)
            raise RuntimeError(f"SimulationSpec invalid: {validation.errors}")
        self._last_simulation_spec = spec
        rel = self.run_store.rel("artifacts", "simulation_spec.json")
        self.project.write_json(rel, spec.model_dump(mode="json"))
        self.project.write_json("simulations/last_spec.json", spec.model_dump(mode="json"))
        for assumption in spec.assumptions:
            node = self.graph.ensure_node(
                node_type=GraphNodeType.ASSUMPTION,
                ref_id=assumption.id,
                run_id=self.run_id,
                label=assumption.id,
                created_by="model_build",
                payload=assumption.model_dump(mode="json"),
            )
            if self._task_graph_node_id:
                self.graph.ensure_edge(
                    edge_type=GraphEdgeType.PART_OF,
                    source_id=node.node_id,
                    target_id=self._task_graph_node_id,
                    run_id=self.run_id,
                )
        return [rel, "simulations/last_spec.json"], []

    async def _run_engineering_simulation(self, task: TaskSpec) -> tuple[list[str], list[str]]:
        """Deterministic solver. Does not go through SimulationAgent."""
        from ai_lab.simulation.claims import claims_from_simulation, verification_specs_from_simulation
        from ai_lab.simulation.load import load_trusted_spec
        from ai_lab.simulation.pipeline import run_simulation
        from ai_lab.simulation.protocol import SolverContext

        spec = self._last_simulation_spec
        if spec is None:
            spec_id = str((task.metadata or {}).get("spec_id") or "uniaxial_tension")
            spec = load_trusted_spec(spec_id, repo_root=self.repo_root)
            self._last_simulation_spec = spec
        result = run_simulation(
            spec,
            SolverContext(run_id=self.run_id, task_id=task.task_id, repo_root=self.repo_root),
            run_store=self.run_store,
        )
        self._last_simulation_result = result
        rel = self.run_store.rel("artifacts", "simulation_result.json")
        self.project.write_json(rel, result.model_dump(mode="json"))
        self.project.write_json("simulations/last_result.json", result.model_dump(mode="json"))
        vspecs = verification_specs_from_simulation(spec, result)
        claims = claims_from_simulation(spec, result, verification_specs=vspecs)
        claim_ids: list[str] = []
        sim_node = self.graph.ensure_node(
            node_type=GraphNodeType.SIMULATION,
            ref_id=result.result_id,
            run_id=self.run_id,
            label=result.solver,
            created_by="engineering_solver",
            payload={
                "status": result.status.value,
                "scientific_status": result.scientific_status.model_dump(mode="json"),
                "computation_artifact_id": result.computation_artifact_id,
            },
        )
        calc_node = self.graph.ensure_node(
            node_type=GraphNodeType.CALCULATION,
            ref_id=f"calc-{result.result_id}",
            run_id=self.run_id,
            label="uniaxial_tension",
            created_by="engineering_solver",
            payload={"outputs": {k: v.model_dump(mode="json") for k, v in result.outputs.items()}},
        )
        self.graph.ensure_edge(
            edge_type=GraphEdgeType.DERIVED_FROM,
            source_id=sim_node.node_id,
            target_id=calc_node.node_id,
            run_id=self.run_id,
        )
        if self._task_graph_node_id:
            self.graph.ensure_edge(
                edge_type=GraphEdgeType.DERIVED_FROM,
                source_id=sim_node.node_id,
                target_id=self._task_graph_node_id,
                run_id=self.run_id,
            )
        for claim in claims:
            self.evidence.save_claim(claim, subdirectory="calculations")
            claim_ids.append(claim.claim_id)
            c_node = self.graph.ensure_node(
                node_type=GraphNodeType.CLAIM,
                ref_id=claim.claim_id,
                run_id=self.run_id,
                label=claim.claim_id,
                created_by="engineering_solver",
            )
            self.graph.ensure_edge(
                edge_type=GraphEdgeType.DERIVED_FROM,
                source_id=c_node.node_id,
                target_id=sim_node.node_id,
                run_id=self.run_id,
            )
        return [rel, "simulations/last_result.json"], claim_ids

    async def _run_simulation_verification(self, task: TaskSpec) -> list[str]:
        """Layer 2: DeterministicVerifier on simulation outputs. Not a scientific PASS."""
        from ai_lab.checks.verifier import DeterministicVerifier, limits_from_config
        from ai_lab.simulation.claims import verification_specs_from_simulation

        spec = self._last_simulation_spec
        result = self._last_simulation_result
        if spec is None or result is None:
            raise RuntimeError("simulation_verification requires a prior simulation task")
        engine = DeterministicVerifier(limits_from_config(self.config.verification))
        reports = []
        for vspec in verification_specs_from_simulation(spec, result):
            vr = engine.verify(vspec)
            reports.append(vr.model_dump(mode="json"))
        rel = self.run_store.rel("artifacts", "simulation_verification.json")
        self.project.write_json(rel, {"task_id": task.task_id, "results": reports})
        return [rel]

    async def _run_deterministic_review_prep(self) -> None:
        """Freeze ReviewBundle + deterministic checks. No LLM."""
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
        check_report = await run_deterministic_checks(
            blind,
            execute_code=_exec,
            verification_cfg=self.config.verification,
            require_specs_for_calculation=True,
        )
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
        self._record_check_nodes(claims, check_report)

    def _finish_adjudication(self) -> AdjudicationStatus:
        if self._last_check_report is None:
            raise RuntimeError("Adjudication requires a deterministic check report")
        require_review = True
        require_rt = True
        require_verification = True
        require_calculation = False
        if self._routing_decision is not None:
            require_review = self._routing_decision.require_independent_review
            require_rt = self._routing_decision.require_red_team
            require_verification = self._routing_decision.require_verification
            require_calculation = self._routing_decision.require_calculation
        elif self._task_graph is not None:
            # Fallback: read flags from adjudication task metadata (profile graphs).
            for task in self._task_graph.tasks:
                if task.task_kind == TaskKind.ADJUDICATION and task.metadata:
                    if "require_independent_review" in task.metadata:
                        require_review = bool(task.metadata["require_independent_review"])
                    if "require_red_team" in task.metadata:
                        require_rt = bool(task.metadata["require_red_team"])
                    if "require_verification" in task.metadata:
                        require_verification = bool(task.metadata["require_verification"])
                    if "require_calculation" in task.metadata:
                        require_calculation = bool(task.metadata["require_calculation"])
            # SIMPLE profile graphs always require deterministic verification for calc tasks.
            if any(t.task_id == "calculation" for t in self._task_graph.tasks):
                require_calculation = True
                require_verification = True

        from ai_lab.checks.calculation_contract import (
            evaluate_evidence_completeness,
            extract_outputs_from_understanding,
        )
        from ai_lab.core.models import CalculationSpec, VerificationPolicy

        # Reload specs from disk (agent may have written them) + in-memory.
        specs = list(self._calculation_specs)
        spec_dir = self.project.root / self.run_store.rel("planner", "calculation_specs")
        if spec_dir.is_dir():
            import json as _json

            for path in sorted(spec_dir.glob("*.json")):
                # Skip soft-failed invalid_* proposals — they must not count as contracts.
                if path.name.startswith("invalid_"):
                    continue
                try:
                    specs.append(CalculationSpec.model_validate(_json.loads(path.read_text(encoding="utf-8"))))
                except Exception as exc:
                    logger.error("Failed to load CalculationSpec %s: %s", path, exc)
        # Deduplicate by spec_id
        by_id = {s.spec_id: s for s in specs}
        specs = list(by_id.values())
        self._calculation_specs = specs

        comps = self.run_store.list_computations()
        claims = self.evidence.list_claims(include_superseded=False)
        understanding_outputs: list[str] = []
        understanding_dims: dict[str, str] = {}
        # Prefer run-scoped lock snapshot (synthesis must not overwrite policy).
        understanding = None
        try:
            understanding = self.run_store.load_review_json("chief_understanding.json")
        except Exception:
            understanding = None
        if understanding is None:
            try:
                understanding = self.project.read_json("reviews/chief_understanding.json")
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.error("Failed reading chief_understanding for completeness: %s", exc)
        if isinstance(understanding, dict):
            understanding_outputs, understanding_dims = extract_outputs_from_understanding(
                understanding
            )
        if self._investigation_scope is not None and self._investigation_scope.required_outputs:
            # Locked scope cannot be weakened by later LLM understanding.
            understanding_outputs = list(
                dict.fromkeys(
                    [*self._investigation_scope.required_outputs, *understanding_outputs]
                )
            )
            understanding_dims = {
                **understanding_dims,
                **self._investigation_scope.expected_dimensions,
            }
        policy = VerificationPolicy(
            verification_required=require_verification,
            calculation_required=require_calculation,
            minimum_checks=1 if require_verification else 0,
            required_outputs=list(understanding_outputs),
            required_output_dimensions=dict(understanding_dims),
        )

        # Generic benchmark acceptance when this project is a registered benchmark.
        acceptance_passed: bool | None = None
        acceptance_reasons: list[str] = []
        output_aliases: dict[str, list[str]] = {}
        try:
            from ai_lab.benchmark.acceptance import evaluate_acceptance, output_alias_map
            from ai_lab.benchmark.registry import get_benchmark

            bspec = get_benchmark(self.repo_root, self.project.name)
            output_aliases = output_alias_map(bspec.expectation)
            if bspec.expectation.acceptance_outputs or bspec.expectation.acceptance_inputs:
                arep = evaluate_acceptance(
                    bspec.expectation,
                    computations=comps,
                    claims=claims,
                    check_report=self._last_check_report,
                )
                acceptance_passed = arep.passed
                acceptance_reasons = list(arep.reasons)
                self.run_store.save_review_json(
                    "benchmark_acceptance.json", arep.model_dump(mode="json")
                )
        except KeyError:
            # Not a registered benchmark project — no numeric oracle.
            pass
        except Exception as exc:
            logger.error("Benchmark acceptance evaluation failed: %s", exc)
            # Fail closed for registered-looking projects only when get_benchmark worked;
            # unexpected errors must not invent PASS.
            if acceptance_passed is None:
                acceptance_passed = False
                acceptance_reasons = [f"acceptance evaluation error: {exc}"]

        completeness = evaluate_evidence_completeness(
            calculation_specs=specs,
            computations=comps,
            check_report=self._last_check_report,
            claims=claims,
            verification_policy=policy,
            require_calculation=require_calculation,
            relevance_results=list(self._relevance_results),
            output_aliases=output_aliases,
            acceptance_passed=acceptance_passed,
            acceptance_reasons=acceptance_reasons,
        )
        self._last_evidence_completeness = completeness
        self.run_store.save_review_json(
            "evidence_completeness.json", completeness.model_dump(mode="json")
        )

        adj = adjudicate(
            check_report=self._last_check_report,
            verification=self._last_verification,
            red_team=self._last_red_team,
            require_independent_review=require_review,
            require_red_team=require_rt,
            evidence_completeness=completeness,
            verification_required=require_verification,
            research_sufficiency=self._last_research_sufficiency,
            scope_status=(
                self._investigation_scope.status.value if self._investigation_scope else None
            ),
        )
        adj.routing_policy_version = self.routing_policy.version
        adj.model_routing = {
            "verification": self.routing_policy.for_role(AgentRole.VERIFICATION).public_dump(),
            "red_team": self.routing_policy.for_role(AgentRole.RED_TEAM).public_dump(),
        }
        if self.routing_policy.adjudication is not None:
            adj.model_routing["adjudication"] = self.routing_policy.adjudication.public_dump()
        if self._independence_assessment is not None:
            adj.independence_level = self._independence_assessment.level
        self._last_adjudication = adj
        self.project.write_json(
            "reviews/last_adjudication.json", adj.model_dump(mode="json")
        )
        self.run_store.save_review_json("last_adjudication.json", adj.model_dump(mode="json"))
        claims = self.evidence.list_claims(include_superseded=False)
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

    def _persist_finish_manifest(self, *, final_state: str) -> None:
        """Write live budget + engineering outcome into RunManifest."""
        outcome = None
        if self._last_adjudication is not None:
            outcome = (
                self._last_adjudication.engineering_outcome
                or self._last_adjudication.status
            ).value
        self.run_store.finish_manifest(
            final_state=final_state,
            budget=self.budget,
            engineering_outcome=outcome,
            calculation_spec_ids=[s.spec_id for s in self._calculation_specs],
        )

    async def _run_independent_review(self) -> AdjudicationStatus:
        """Deterministic checks → frozen ReviewBundle → V ∥ RT → adjudication.

        Uses CURRENT_RUN claims only — no stale leakage from prior runs.
        Kept as a single entry for tests; the TaskGraph executor calls the parts.
        """
        await self._run_deterministic_review_prep()
        bundle_path = "reviews/review_bundle.json"
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
        v_result, rt_result = await asyncio.gather(self._run_task(v_task), self._run_task(rt_task))
        self._last_verification = v_result.verification
        self._last_red_team = rt_result.red_team
        if rt_result.raw and self._last_verification:
            dumped = str(rt_result.raw)
            if self._last_verification.report_id in dumped:
                logger.error("Red team output appears to reference verification report id")
                raise RuntimeError("Review isolation violated: red team saw verification id")
        return self._finish_adjudication()

    def _record_check_nodes(self, claims, check_report) -> None:
        """Persist Claim → CHECK (VerificationResult) with TESTS / VERIFIED_BY.

        Reuses GraphNodeType.CHECK — no second knowledge store.
        """
        claims_by_id = {c.claim_id: c for c in claims}
        for vr in check_report.verification_results:
            self.run_store.save_review_json(
                f"checks/{vr.result_id}.json", vr.model_dump(mode="json")
            )
            check_node = self.graph.ensure_node(
                node_type=GraphNodeType.CHECK,
                ref_id=vr.result_id,
                run_id=self.run_id,
                label=f"check:{vr.status.value}",
                created_by="deterministic_verifier",
                independence=AgreementType.INDEPENDENT_EVIDENCE,
                payload=vr.model_dump(mode="json"),
            )
            claim_id = vr.claim_id
            if not claim_id or claim_id not in claims_by_id:
                continue
            c_node = self.graph.ensure_node(
                node_type=GraphNodeType.CLAIM,
                ref_id=claim_id,
                run_id=self.run_id,
                label=claim_id,
            )
            self.graph.ensure_edge(
                edge_type=GraphEdgeType.TESTS,
                source_id=check_node.node_id,
                target_id=c_node.node_id,
                run_id=self.run_id,
            )
            self.graph.ensure_edge(
                edge_type=GraphEdgeType.VERIFIED_BY,
                source_id=c_node.node_id,
                target_id=check_node.node_id,
                run_id=self.run_id,
            )

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
        if self.problem_override is not None:
            # Run-scoped copy only — does not overwrite project/problem.md.
            self.project.write_text(
                self.run_store.rel("inputs", "ui_problem.md"),
                self.problem_override,
            )
        self._emit_lifecycle("run.created", project_id=self.project.name)

        engine = WorkflowEngine(
            snapshot,
            hitl_on_disputed=bool(self.config.runtime.get("hitl_on_disputed", True)),
        )
        max_iter = int(self.budget.max_iterations)
        steps = 0

        try:
            if engine.state == ProjectState.AWAITING_HUMAN and self._is_resume:
                pending = engine.snapshot.pending_hitl or {}
                continued = self._resume_from_hitl(pending)
                if continued:
                    engine.snapshot.pending_hitl = None
                    engine.set_state(ProjectState.UNDERSTANDING)
                    self.project.save_snapshot(engine.snapshot)
                else:
                    self._persist_finish_manifest(final_state=engine.snapshot.state.value)
                    self._emit_lifecycle(
                        "run.completed",
                        final_state=engine.snapshot.state.value,
                        hitl_required=True,
                    )
                    return engine.snapshot

            if engine.state != ProjectState.AWAITING_HUMAN:
                try:
                    await self._prepare_task_graph()
                    if self._task_graph is not None:
                        self._emit_lifecycle(
                            "pipeline.ready",
                            graph_id=self._task_graph.graph_id,
                            tasks=[
                                {
                                    "task_id": t.task_id,
                                    "stage": self._stage_for_task(t),
                                    "kind": t.task_kind.value if t.task_kind else None,
                                    "role": t.role.value if t.role else None,
                                }
                                for t in self._task_graph.tasks
                            ],
                            workflow_profile=(
                                self._routing_decision.final_workflow.value
                                if self._routing_decision is not None
                                else None
                            ),
                        )
                except _ScopeUnresolved as scope_exc:
                    self._finish_unresolved_scope(engine, scope_exc.scope)
                    self._persist_finish_manifest(final_state=engine.snapshot.state.value)
                    self._emit_lifecycle(
                        "run.completed",
                        final_state=engine.snapshot.state.value,
                        engineering_outcome=AdjudicationStatus.INSUFFICIENT_EVIDENCE.value,
                    )
                    return engine.snapshot
                except _HitlInterrupt as hitl_exc:
                    engine.snapshot.pending_hitl = hitl_exc.request.model_dump(mode="json")
                    engine.set_state(ProjectState.AWAITING_HUMAN)
                    self.project.save_snapshot(engine.snapshot)
                    self._persist_finish_manifest(final_state=engine.snapshot.state.value)
                    self._emit_lifecycle(
                        "run.completed",
                        final_state=engine.snapshot.state.value,
                        hitl_required=True,
                    )
                    return engine.snapshot

            while not engine.is_terminal() and steps < max_iter:
                steps += 1
                check_budget(self.budget)
                logger.info("Run %s state=%s step=%s", self.run_id, engine.state.value, steps)

                if engine.state == ProjectState.CREATED:
                    engine.advance()
                    self.project.save_snapshot(engine.snapshot)
                    continue

                if engine.state == ProjectState.AWAITING_HUMAN:
                    break

                try:
                    progressed = await self._execute_graph_step(engine)
                except _HitlInterrupt as hitl_exc:
                    engine.snapshot.pending_hitl = hitl_exc.request.model_dump(mode="json")
                    engine.set_state(ProjectState.AWAITING_HUMAN)
                    self.project.save_snapshot(engine.snapshot)
                    break
                except BudgetExceeded as exc:
                    logger.error("Budget exceeded: %s", exc)
                    self.stop_reason = str(exc)
                    engine.set_state(ProjectState.BUDGET_EXCEEDED)
                    self.project.save_snapshot(engine.snapshot)
                    break

                if not progressed:
                    self._finalize_graph(engine)
                    self.project.save_snapshot(engine.snapshot)
                    break

                self.project.save_snapshot(engine.snapshot)

        except BudgetExceeded as exc:
            logger.error("Budget exceeded: %s", exc)
            self.stop_reason = str(exc)
            engine.set_state(ProjectState.BUDGET_EXCEEDED)
            self.project.save_snapshot(engine.snapshot)

        if steps >= max_iter and not engine.is_terminal():
            logger.error("Max iterations reached (%s); stopping", max_iter)
            self.stop_reason = f"max_iterations exceeded: {steps}>{max_iter}"
            engine.set_state(ProjectState.BUDGET_EXCEEDED)
            self.project.save_snapshot(engine.snapshot)

        self._persist_finish_manifest(final_state=engine.snapshot.state.value)
        if engine.snapshot.state == ProjectState.COMPLETED:
            try:
                self.knowledge.runs.freeze_run(self.run_id)
            except Exception as exc:
                logger.error("Failed to freeze completed run: %s", exc)
                raise
        outcome = None
        if self._last_adjudication is not None:
            outcome = (
                self._last_adjudication.engineering_outcome
                or self._last_adjudication.status
            )
            if hasattr(outcome, "value"):
                outcome = outcome.value
        self._emit_lifecycle(
            "run.completed",
            final_state=engine.snapshot.state.value,
            engineering_outcome=outcome,
            hitl_required=engine.snapshot.state == ProjectState.AWAITING_HUMAN,
            stop_reason=self.stop_reason,
        )
        return engine.snapshot


class _HitlInterrupt(Exception):
    def __init__(self, request: HitlRequest) -> None:
        super().__init__(request.reason)
        self.request = request


class _ScopeUnresolved(Exception):
    """Scope gate exhausted clarification budget — not a planner/runtime crash."""

    def __init__(self, scope: InvestigationScope) -> None:
        super().__init__(scope.status.value)
        self.scope = scope


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
    problem_override: str | None = None,
) -> ProjectSnapshot:
    root = repo_root_from_here()
    config = load_config(config_path or (root / "config" / "default.yaml"))
    if provider:
        config = apply_provider_override(config, provider)
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
        problem_override=problem_override,
    )
    return await runtime.run()


async def plan_project(
    project_name: str,
    *,
    provider: str | None = None,
    projects_dir: Path | None = None,
    config_path: Path | None = None,
    auto_approve_hitl: bool = False,
) -> tuple[TaskGraph, object]:
    """Create and validate a TaskGraph without executing research/compute."""
    root = repo_root_from_here()
    config = load_config(config_path or (root / "config" / "default.yaml"))
    if provider:
        config = apply_provider_override(config, provider)
    projects_dir = projects_dir or (root / "projects")
    store = ProjectStore.open(projects_dir, project_name)
    runtime = LabRuntime(
        store,
        config,
        repo_root=root,
        hitl=HitlGate(auto_approve=auto_approve_hitl),
    )
    runtime.run_store.build_manifest(
        config=config,
        repo_root=root,
        budget=runtime.budget,
        model_id=next(iter(config.models.values()), "unknown"),
    )
    graph = await runtime._prepare_task_graph()
    runtime.run_store.finish_manifest(final_state="PLANNED")
    validation = runtime.project.read_json(
        runtime.run_store.rel("planner", "validation.json")
    )
    return graph, validation
