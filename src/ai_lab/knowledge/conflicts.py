"""Conflict model — contradictions cannot be silently overwritten."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_lab.core.enums import ConflictStatus, GraphEdgeType, GraphNodeType
from ai_lab.core.models import GraphEdge
from ai_lab.knowledge.graph import JsonEvidenceRepository
from ai_lab.knowledge.models import ConflictRecord
from ai_lab.memory.project_store import ProjectStore


def _conflict_rel(conflict_id: str) -> str:
    return f"knowledge/conflicts/{conflict_id}.json"


def create_conflict(
    store: ProjectStore,
    graph: JsonEvidenceRepository,
    *,
    claim_a: str,
    claim_b: str,
    run_id: str | None,
    evidence: list[str] | None = None,
) -> ConflictRecord:
    if claim_a == claim_b:
        raise ValueError("Conflict requires two distinct claims")
    rec = ConflictRecord(
        project_id=store.name,
        run_id=run_id,
        claim_a=claim_a,
        claim_b=claim_b,
        evidence=list(evidence or []),
        status=ConflictStatus.OPEN,
    )
    store.write_json(_conflict_rel(rec.conflict_id), rec.model_dump(mode="json"))

    na = graph.ensure_node(
        node_type=GraphNodeType.CLAIM, ref_id=claim_a, run_id=run_id, label=claim_a
    )
    nb = graph.ensure_node(
        node_type=GraphNodeType.CLAIM, ref_id=claim_b, run_id=run_id, label=claim_b
    )
    nc = graph.ensure_node(
        node_type=GraphNodeType.CONFLICT,
        ref_id=rec.conflict_id,
        run_id=run_id,
        label=f"conflict {claim_a} vs {claim_b}",
    )
    graph.add_edge(
        GraphEdge(
            edge_type=GraphEdgeType.CONTRADICTS,
            source_id=na.node_id,
            target_id=nb.node_id,
            project_id=store.name,
            run_id=run_id,
            metadata={"conflict_id": rec.conflict_id},
        )
    )
    graph.add_edge(
        GraphEdge(
            edge_type=GraphEdgeType.PART_OF,
            source_id=nc.node_id,
            target_id=na.node_id,
            project_id=store.name,
            run_id=run_id,
        )
    )
    return rec


def resolve_conflict(
    store: ProjectStore,
    conflict_id: str,
    *,
    status: ConflictStatus,
    reason: str,
) -> ConflictRecord:
    if status == ConflictStatus.OPEN:
        raise ValueError("resolution status cannot be OPEN")
    path = _conflict_rel(conflict_id)
    rec = ConflictRecord.model_validate(store.read_json(path))
    rec.status = status
    rec.resolution = status.value
    rec.resolution_reason = reason
    rec.updated_at = datetime.now(timezone.utc)
    store.write_json(path, rec.model_dump(mode="json"))
    return rec


def list_conflicts(store: ProjectStore) -> list[ConflictRecord]:
    folder = store.root / "knowledge" / "conflicts"
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.json")):
        out.append(ConflictRecord.model_validate(store.read_json(
            str(p.relative_to(store.root)).replace("\\", "/")
        )))
    return out


def get_open_conflicts(store: ProjectStore) -> list[ConflictRecord]:
    return [c for c in list_conflicts(store) if c.status in {
        ConflictStatus.OPEN,
        ConflictStatus.BOTH_UNRESOLVED,
        ConflictStatus.HUMAN_REVIEW,
    }]
