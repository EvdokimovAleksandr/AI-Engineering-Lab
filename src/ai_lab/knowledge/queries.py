"""Evidence path query API — provenance as first-class queries."""

from __future__ import annotations

from ai_lab.core.enums import GraphEdgeType, GraphNodeType
from ai_lab.core.models import GraphEdge, GraphNode
from ai_lab.knowledge.graph import JsonEvidenceRepository
from ai_lab.knowledge.json_knowledge import JsonKnowledgeRepository
from ai_lab.knowledge.models import TimelineEvent
from ai_lab.memory.project_store import ProjectStore


class EvidenceQueryService:
    def __init__(
        self,
        store: ProjectStore,
        graph: JsonEvidenceRepository,
        knowledge: JsonKnowledgeRepository,
    ) -> None:
        self.store = store
        self.graph = graph
        self.knowledge = knowledge

    def _nodes_by_ref(self, ref_id: str) -> list[GraphNode]:
        return self.graph.find_by_ref(ref_id)

    def _neighbors(
        self,
        node_id: str,
        *,
        edge_types: set[GraphEdgeType] | None = None,
        outbound: bool = True,
    ) -> list[tuple[GraphEdge, GraphNode]]:
        edges = self.graph.list_edges()
        nodes = {n.node_id: n for n in self.graph.list_nodes()}
        out: list[tuple[GraphEdge, GraphNode]] = []
        for e in edges:
            if edge_types and e.edge_type not in edge_types:
                continue
            if outbound and e.source_id == node_id and e.target_id in nodes:
                out.append((e, nodes[e.target_id]))
            if not outbound and e.target_id == node_id and e.source_id in nodes:
                out.append((e, nodes[e.source_id]))
        return out

    def find_supporting_evidence(self, claim_id: str) -> list[dict]:
        results = []
        for node in self._nodes_by_ref(claim_id):
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.SUPPORTS, GraphEdgeType.DERIVED_FROM, GraphEdgeType.CITES},
                outbound=True,
            ):
                results.append({"edge": edge.edge_type.value, "node": other.model_dump(mode="json")})
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.SUPPORTS},
                outbound=False,
            ):
                results.append({"edge": f"inbound_{edge.edge_type.value}", "node": other.model_dump(mode="json")})
        return results

    def find_contradicting_evidence(self, claim_id: str) -> list[dict]:
        results = []
        for node in self._nodes_by_ref(claim_id):
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.CONTRADICTS, GraphEdgeType.REFUTES, GraphEdgeType.REJECTED_BY},
                outbound=True,
            ):
                results.append({"edge": edge.edge_type.value, "node": other.model_dump(mode="json")})
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.CONTRADICTS, GraphEdgeType.REFUTES},
                outbound=False,
            ):
                results.append({"edge": f"inbound_{edge.edge_type.value}", "node": other.model_dump(mode="json")})
        return results

    def find_verification_chain(self, claim_id: str) -> list[dict]:
        chain: list[dict] = [{"step": "claim", "ref_id": claim_id}]
        for node in self._nodes_by_ref(claim_id):
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.VERIFIED_BY, GraphEdgeType.TESTS},
                outbound=True,
            ):
                chain.append({"step": edge.edge_type.value, "node": other.model_dump(mode="json")})
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.TESTS, GraphEdgeType.VERIFIED_BY},
                outbound=False,
            ):
                chain.append({"step": f"inbound_{edge.edge_type.value}", "node": other.model_dump(mode="json")})
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.DERIVED_FROM, GraphEdgeType.USES},
                outbound=True,
            ):
                chain.append({"step": edge.edge_type.value, "node": other.model_dump(mode="json")})
        return chain

    def find_decision_path(self, decision_id: str) -> list[dict]:
        path: list[dict] = [{"step": "decision", "ref_id": decision_id}]
        for node in self._nodes_by_ref(decision_id):
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={
                    GraphEdgeType.ACCEPTS,
                    GraphEdgeType.SUPPORTS,
                    GraphEdgeType.DEPENDS_ON,
                    GraphEdgeType.USES,
                },
                outbound=True,
            ):
                path.append({"step": edge.edge_type.value, "node": other.model_dump(mode="json")})
                if other.ref_id:
                    path.extend(self.find_verification_chain(other.ref_id))
        return path

    def find_conclusion_dependencies(self, conclusion_id: str) -> list[dict]:
        deps: list[dict] = [{"step": "conclusion", "ref_id": conclusion_id}]
        for node in self._nodes_by_ref(conclusion_id):
            for edge, other in self._neighbors(
                node.node_id,
                edge_types={GraphEdgeType.DEPENDS_ON, GraphEdgeType.DERIVED_FROM, GraphEdgeType.SUPPORTS},
                outbound=True,
            ):
                deps.append({"step": edge.edge_type.value, "node": other.model_dump(mode="json")})
                if other.node_type == GraphNodeType.DECISION and other.ref_id:
                    deps.extend(self.find_decision_path(other.ref_id))
        return deps

    def find_claim_history(self, claim_id: str) -> list[dict]:
        history: list[dict] = []
        try:
            claim = self.knowledge.get_claim(claim_id)
        except KeyError:
            return history
        # Walk to root via supersedes
        guard = 0
        while claim.supersedes and guard < 100:
            guard += 1
            try:
                claim = self.knowledge.get_claim(claim.supersedes)
            except KeyError:
                break
        # Walk forward via superseded_by
        guard = 0
        while guard < 100:
            guard += 1
            history.append(
                {
                    "claim_id": claim.claim_id,
                    "version": claim.version,
                    "lifecycle": claim.lifecycle.value,
                    "canonical_id": claim.canonical_id,
                    "run_id": claim.run_id,
                    "supersedes": claim.supersedes,
                    "superseded_by": claim.superseded_by,
                    "statement": claim.statement,
                }
            )
            if not claim.superseded_by:
                break
            try:
                claim = self.knowledge.get_claim(claim.superseded_by)
            except KeyError:
                break
        return history

    def find_run_evidence(self, run_id: str) -> dict:
        return {
            "run_id": run_id,
            "nodes": [n.model_dump(mode="json") for n in self.graph.list_nodes(run_id=run_id)],
            "edges": [e.model_dump(mode="json") for e in self.graph.list_edges(run_id=run_id)],
            "claims": [
                c.model_dump(mode="json")
                for c in self.knowledge.list_claims(
                    visibility=__import__(
                        "ai_lab.core.enums", fromlist=["ClaimVisibility"]
                    ).ClaimVisibility.CURRENT_RUN,
                    run_id=run_id,
                    include_superseded=True,
                )
            ],
        }


def get_project_timeline(store: ProjectStore, knowledge: JsonKnowledgeRepository) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    runs_root = store.root / ".runs"
    if not runs_root.is_dir():
        return events
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir():
            continue
        run_id = run_dir.name
        manifest_path = run_dir / "manifest.json"
        ts = None
        final = "unknown"
        if manifest_path.exists():
            import json

            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            ts = raw.get("started_at")
            final = raw.get("final_state") or "unknown"
        claims = knowledge.list_claims(
            visibility=__import__("ai_lab.core.enums", fromlist=["ClaimVisibility"]).ClaimVisibility.CURRENT_RUN,
            run_id=run_id,
            include_superseded=True,
        )
        events.append(
            TimelineEvent(
                run_id=run_id,
                kind="run",
                summary=f"Run {run_id} final_state={final} claims={len(claims)}",
                refs=[c.claim_id for c in claims[:20]],
            )
        )
        for c in claims:
            if c.supersedes:
                events.append(
                    TimelineEvent(
                        run_id=run_id,
                        kind="supersede",
                        summary=f"{c.claim_id} supersedes {c.supersedes}",
                        refs=[c.claim_id, c.supersedes],
                    )
                )
    # Approved knowledge events
    from ai_lab.knowledge.approved import list_approved

    for e in list_approved(store):
        events.append(
            TimelineEvent(
                run_id=e.promoted_from_run_id,
                kind="approved" if e.status == "ACTIVE" else "demoted",
                summary=f"ApprovedKnowledge {e.claim_id} status={e.status}",
                refs=[e.claim_id, e.entry_id],
            )
        )
    return events
