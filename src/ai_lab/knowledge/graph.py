"""Evidence graph with integrity validation (JSON backend)."""

from __future__ import annotations

from ai_lab.core.enums import GraphEdgeType, GraphNodeType
from ai_lab.core.models import GraphEdge, GraphNode
from ai_lab.knowledge.hashing import sha256_json
from ai_lab.memory.project_store import ProjectStore

# Edges that must not form cycles within their relation class
ACYCLIC_EDGE_TYPES = frozenset(
    {
        GraphEdgeType.SUPERSEDES,
        GraphEdgeType.DERIVED_FROM,
        GraphEdgeType.DEPENDS_ON,
        GraphEdgeType.PART_OF,
        GraphEdgeType.CREATED_IN,
    }
)


class GraphIntegrityError(ValueError):
    pass


class JsonEvidenceRepository:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self.project_id = store.name
        self._nodes_rel = "knowledge/graph/nodes.json"
        self._edges_rel = "knowledge/graph/edges.json"
        (store.root / "knowledge" / "graph").mkdir(parents=True, exist_ok=True)

    def _load_nodes(self) -> list[dict]:
        try:
            data = self.store.read_json(self._nodes_rel)
            return list(data) if isinstance(data, list) else []
        except FileNotFoundError:
            return []

    def _load_edges(self) -> list[dict]:
        try:
            data = self.store.read_json(self._edges_rel)
            return list(data) if isinstance(data, list) else []
        except FileNotFoundError:
            return []

    def _save_nodes(self, nodes: list[dict]) -> None:
        self.store.write_json(self._nodes_rel, nodes)

    def _save_edges(self, edges: list[dict]) -> None:
        self.store.write_json(self._edges_rel, edges)

    def add_node(self, node: GraphNode) -> GraphNode:
        if not node.project_id:
            node.project_id = self.project_id
        if node.project_id != self.project_id:
            raise GraphIntegrityError(
                f"Cross-project node forbidden: {node.project_id} != {self.project_id}"
            )
        if not node.content_hash:
            node.content_hash = sha256_json(
                {"type": node.node_type.value, "ref": node.ref_id, "label": node.label, "payload": node.payload}
            )
        nodes = self._load_nodes()
        if any(n["node_id"] == node.node_id for n in nodes):
            raise GraphIntegrityError(f"Duplicate node_id: {node.node_id}")
        nodes.append(node.model_dump(mode="json"))
        self._save_nodes(nodes)
        return node

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        if edge.edge_type not in GraphEdgeType:
            raise GraphIntegrityError(f"Unknown edge type: {edge.edge_type}")
        if not edge.project_id:
            edge.project_id = self.project_id
        if edge.project_id != self.project_id:
            raise GraphIntegrityError("Cross-project edge forbidden without explicit SAME_AS")

        nodes = {n["node_id"]: n for n in self._load_nodes()}
        if edge.source_id not in nodes or edge.target_id not in nodes:
            raise GraphIntegrityError(
                f"Dangling edge: {edge.source_id} -> {edge.target_id}"
            )
        src_proj = nodes[edge.source_id].get("project_id") or self.project_id
        tgt_proj = nodes[edge.target_id].get("project_id") or self.project_id
        if src_proj != tgt_proj and edge.edge_type != GraphEdgeType.SAME_AS:
            raise GraphIntegrityError(
                f"Cross-project link requires SAME_AS, got {edge.edge_type}"
            )

        # CHECK or VERIFICATION may TESTS a CLAIM (deterministic result vs agent report)
        if edge.edge_type == GraphEdgeType.TESTS:
            src_type = nodes[edge.source_id].get("node_type")
            tgt_type = nodes[edge.target_id].get("node_type")
            if src_type in {
                GraphNodeType.VERIFICATION.value,
                GraphNodeType.CHECK.value,
            } and tgt_type != GraphNodeType.CLAIM.value:
                raise GraphIntegrityError("TESTS edge from VERIFICATION/CHECK must target a CLAIM")
        if edge.edge_type == GraphEdgeType.VERIFIED_BY:
            # claim --VERIFIED_BY--> verification|check  (or reversed)
            tgt_type = nodes[edge.target_id].get("node_type")
            src_type = nodes[edge.source_id].get("node_type")
            review_types = {GraphNodeType.VERIFICATION.value, GraphNodeType.CHECK.value}
            if not review_types.intersection({src_type, tgt_type}):
                raise GraphIntegrityError("VERIFIED_BY must involve a VERIFICATION or CHECK node")
            if GraphNodeType.CLAIM.value not in {src_type, tgt_type}:
                raise GraphIntegrityError("VERIFIED_BY must involve a CLAIM node")

        edges = self._load_edges()
        # Bidirectional SUPERSEDES forbidden
        if edge.edge_type == GraphEdgeType.SUPERSEDES:
            for e in edges:
                if (
                    e.get("edge_type") == GraphEdgeType.SUPERSEDES.value
                    and e.get("source_id") == edge.target_id
                    and e.get("target_id") == edge.source_id
                ):
                    raise GraphIntegrityError("Bidirectional SUPERSEDES forbidden")

        # Cycle check for acyclic types
        if edge.edge_type in ACYCLIC_EDGE_TYPES:
            if self._would_create_cycle(edges, edge.source_id, edge.target_id, edge.edge_type.value):
                raise GraphIntegrityError(
                    f"Cycle forbidden for edge type {edge.edge_type.value}"
                )

        edges.append(edge.model_dump(mode="json"))
        self._save_edges(edges)
        return edge

    def _would_create_cycle(
        self, edges: list[dict], source_id: str, target_id: str, edge_type: str
    ) -> bool:
        # If target can already reach source via same edge_type, adding source->target cycles
        adj: dict[str, list[str]] = {}
        for e in edges:
            if e.get("edge_type") != edge_type:
                continue
            adj.setdefault(e["source_id"], []).append(e["target_id"])
        stack = [target_id]
        seen = set()
        while stack:
            cur = stack.pop()
            if cur == source_id:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        return False

    def get_node(self, node_id: str) -> GraphNode:
        for n in self._load_nodes():
            if n["node_id"] == node_id:
                return GraphNode.model_validate(n)
        raise KeyError(f"Unknown node_id: {node_id}")

    def find_by_ref(self, ref_id: str) -> list[GraphNode]:
        return [GraphNode.model_validate(n) for n in self._load_nodes() if n.get("ref_id") == ref_id]

    def list_nodes(self, *, run_id: str | None = None) -> list[GraphNode]:
        nodes = [GraphNode.model_validate(n) for n in self._load_nodes()]
        if run_id is None:
            return nodes
        return [n for n in nodes if n.run_id == run_id]

    def list_edges(self, *, run_id: str | None = None) -> list[GraphEdge]:
        edges = [GraphEdge.model_validate(e) for e in self._load_edges()]
        if run_id is None:
            return edges
        return [e for e in edges if e.run_id == run_id]

    def validate_integrity(self) -> list[str]:
        errors: list[str] = []
        nodes = {n["node_id"]: n for n in self._load_nodes()}
        for e in self._load_edges():
            try:
                GraphEdgeType(e["edge_type"])
            except Exception:
                errors.append(f"Unknown edge type: {e.get('edge_type')}")
            if e["source_id"] not in nodes or e["target_id"] not in nodes:
                errors.append(f"Dangling edge {e.get('edge_id')}")
            sp = nodes.get(e["source_id"], {}).get("project_id")
            tp = nodes.get(e["target_id"], {}).get("project_id")
            if sp and tp and sp != tp and e.get("edge_type") != GraphEdgeType.SAME_AS.value:
                errors.append(f"Cross-project edge without SAME_AS: {e.get('edge_id')}")
        return errors

    def ensure_node(
        self,
        *,
        node_type: GraphNodeType,
        ref_id: str,
        run_id: str | None,
        label: str = "",
        created_by: str | None = None,
        **kwargs,
    ) -> GraphNode:
        existing = self.find_by_ref(ref_id)
        for n in existing:
            if n.node_type == node_type and (run_id is None or n.run_id == run_id):
                return n
        return self.add_node(
            GraphNode(
                node_type=node_type,
                project_id=self.project_id,
                run_id=run_id,
                ref_id=ref_id,
                label=label or ref_id,
                created_by=created_by,
                **kwargs,
            )
        )

    def ensure_edge(
        self,
        *,
        edge_type: GraphEdgeType,
        source_id: str,
        target_id: str,
        run_id: str | None = None,
        **kwargs,
    ) -> GraphEdge:
        """Reuse an existing typed edge between the same endpoints (ingest idempotency)."""
        for e in self.list_edges():
            if e.edge_type == edge_type and e.source_id == source_id and e.target_id == target_id:
                return e
        return self.add_edge(
            GraphEdge(
                edge_type=edge_type,
                source_id=source_id,
                target_id=target_id,
                project_id=self.project_id,
                run_id=run_id,
                **kwargs,
            )
        )
