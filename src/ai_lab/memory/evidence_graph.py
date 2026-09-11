"""Typed evidence graph stored as JSON (no Neo4j in P1)."""

from __future__ import annotations

from pathlib import Path

from ai_lab.core.enums import GraphEdgeType, GraphNodeType
from ai_lab.core.models import GraphEdge, GraphNode
from ai_lab.memory.project_store import ProjectStore


class EvidenceGraph:
    """Append-only node/edge store under project memory."""

    def __init__(self, store: ProjectStore, *, relative: str = "decisions/evidence_graph.json") -> None:
        self.store = store
        self.relative = relative

    def _load(self) -> dict:
        try:
            data = self.store.read_json(self.relative)
            if not isinstance(data, dict):
                raise TypeError("evidence_graph must be an object")
            return data
        except FileNotFoundError:
            return {"nodes": [], "edges": []}

    def _save(self, data: dict) -> None:
        self.store.write_json(self.relative, data)

    def add_node(self, node: GraphNode) -> GraphNode:
        data = self._load()
        data["nodes"].append(node.model_dump(mode="json"))
        self._save(data)
        return node

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        data = self._load()
        node_ids = {n["node_id"] for n in data["nodes"]}
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            raise ValueError(
                f"Edge endpoints must exist: {edge.source_id} -> {edge.target_id}"
            )
        data["edges"].append(edge.model_dump(mode="json"))
        self._save(data)
        return edge

    def list_nodes(self) -> list[GraphNode]:
        return [GraphNode.model_validate(n) for n in self._load()["nodes"]]

    def list_edges(self) -> list[GraphEdge]:
        return [GraphEdge.model_validate(e) for e in self._load()["edges"]]

    def find_nodes_by_ref(self, ref_id: str) -> list[GraphNode]:
        return [n for n in self.list_nodes() if n.ref_id == ref_id]

    def path_file(self) -> Path:
        return self.store.resolve(self.relative)


def link(
    graph: EvidenceGraph,
    *,
    run_id: str,
    source_type: GraphNodeType,
    source_ref: str,
    source_label: str,
    edge_type: GraphEdgeType,
    target_type: GraphNodeType,
    target_ref: str,
    target_label: str,
) -> tuple[GraphNode, GraphNode, GraphEdge]:
    """Create or reuse nodes by ref_id and add a typed edge."""
    existing = {n.ref_id: n for n in graph.list_nodes() if n.ref_id}
    if source_ref in existing:
        src = existing[source_ref]
    else:
        src = graph.add_node(
            GraphNode(
                node_type=source_type,
                ref_id=source_ref,
                run_id=run_id,
                label=source_label,
            )
        )
    if target_ref in existing and target_ref != source_ref:
        tgt = existing[target_ref]
    elif target_ref == source_ref:
        tgt = src
    else:
        # refresh map after possible insert
        existing = {n.ref_id: n for n in graph.list_nodes() if n.ref_id}
        if target_ref in existing:
            tgt = existing[target_ref]
        else:
            tgt = graph.add_node(
                GraphNode(
                    node_type=target_type,
                    ref_id=target_ref,
                    run_id=run_id,
                    label=target_label,
                )
            )
    edge = graph.add_edge(
        GraphEdge(
            edge_type=edge_type,
            source_id=src.node_id,
            target_id=tgt.node_id,
            run_id=run_id,
        )
    )
    return src, tgt, edge
