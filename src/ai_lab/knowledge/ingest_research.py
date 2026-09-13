"""Ingest ResearchResult into Knowledge V2.1 (existing graph + claim store).

Does not create a second knowledge store. Edges reuse SUPPORTS / DERIVED_FROM / CITES.

Chain stored:
  Claim --CITES--> Source
  Evidence --SUPPORTS--> Claim
  Evidence --DERIVED_FROM--> Source   # extracted_from
"""

from __future__ import annotations

from typing import Any

from ai_lab.core.enums import (
    EvidenceKind,
    EvidenceStrength,
    GraphEdgeType,
    GraphNodeType,
    SourceTrustTier,
    TrustLevel,
)
from ai_lab.core.models import Claim, ConfidenceBreakdown
from ai_lab.knowledge.hashing import claim_content_hash
from ai_lab.knowledge.models import EvidenceRecord, ResearchResult, SourceRecord
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


def _strength(tier: SourceTrustTier) -> EvidenceStrength:
    if tier == SourceTrustTier.PRIMARY:
        return EvidenceStrength.PRIMARY_SOURCE
    if tier == SourceTrustTier.SECONDARY:
        return EvidenceStrength.SECONDARY_SOURCE
    return EvidenceStrength.AI_CLAIM


def _source_payload(source: SourceRecord) -> dict[str, Any]:
    # Body is omitted from the graph payload: hash + URI are the identity; content is untrusted.
    return {
        "uri": source.uri,
        "title": source.title,
        "publisher": source.publisher,
        "retrieved_at": source.retrieved_at.isoformat(),
        "source_type": source.source_type.value,
        "trust_tier": source.trust_tier.value,
        "content_hash": source.content_hash,
        "truncated": source.truncated,
        "data_not_instructions": True,
        "metadata": {
            k: v
            for k, v in (source.metadata or {}).items()
            if k != "search_snippet" or v is not None
        },
    }


def ingest_research_result(
    knowledge,
    result: ResearchResult,
    *,
    run_id: str,
    created_by: str = "research",
    create_claims: bool = True,
    project_id: str | None = None,
    investigation_id: str | None = None,
    task_id: str | None = None,
    contract_version: str | None = None,
) -> dict[str, Any]:
    """Write sources, evidence, claims, and provenance edges into the existing graph."""
    if not run_id:
        raise ValueError("ingest_research_result requires run_id")
    # PR-C: explicit kwargs win; claims must carry full ExecutionContext before save.
    bind_project = project_id or result.project_id
    bind_investigation = investigation_id or result.investigation_id or bind_project
    bind_task = task_id or result.task_id
    bind_contract = contract_version or result.contract_version
    from ai_lab.core.execution_context import require_full_execution_identity

    require_full_execution_identity(
        project_id=bind_project,
        investigation_id=bind_investigation,
        task_id=bind_task,
        run_id=run_id,
        where="ingest_research_result",
    )
    graph = knowledge.graph
    evidence_by_source: dict[str, list[EvidenceRecord]] = {}
    for ev in result.evidence:
        evidence_by_source.setdefault(ev.source_id, []).append(ev)

    source_nodes: dict[str, str] = {}
    for source in result.sources:
        node = graph.ensure_node(
            node_type=GraphNodeType.SOURCE,
            ref_id=source.source_id,
            run_id=run_id,
            label=source.title or source.uri,
            created_by=created_by,
            source_tier=source.trust_tier,
            trust_level=TrustLevel.EXTERNAL,
            evidence_strength=_strength(source.trust_tier),
            content_hash=source.content_hash,
            payload=_source_payload(source),
        )
        source_nodes[source.source_id] = node.node_id

    evidence_nodes: dict[str, str] = {}
    for ev in result.evidence:
        node = graph.ensure_node(
            node_type=GraphNodeType.EVIDENCE,
            ref_id=ev.evidence_id,
            run_id=run_id,
            label=ev.evidence_id,
            created_by=created_by,
            trust_level=TrustLevel.EXTERNAL,
            content_hash=ev.content_hash,
            payload={
                "source_id": ev.source_id,
                "text": ev.text,
                "location": ev.location.model_dump(mode="json"),
                "retrieved_at": ev.retrieved_at.isoformat(),
                "data_not_instructions": True,
                "query": result.query,
            },
        )
        evidence_nodes[ev.evidence_id] = node.node_id
        src_node_id = source_nodes.get(ev.source_id)
        if src_node_id:
            graph.ensure_edge(
                edge_type=GraphEdgeType.DERIVED_FROM,
                source_id=node.node_id,
                target_id=src_node_id,
                run_id=run_id,
                metadata={"relation": "extracted_from", "query": result.query},
            )

    claim_ids: list[str] = []
    if create_claims:
        for source in result.sources:
            excerpts = evidence_by_source.get(source.source_id) or []
            statement = (excerpts[0].text if excerpts else None) or source.title or source.uri
            ev_ids = [e.evidence_id for e in excerpts]
            claim = Claim(
                statement=statement,
                kind=EvidenceKind.INFERENCE,
                source=source.uri,
                source_trust=source.trust_tier,
                evidence=excerpts[0].text if excerpts else None,
                evidence_ids=list(ev_ids),
                conditions={
                    "source_id": source.source_id,
                    "evidence_ids": ev_ids,
                    "content_hash": source.content_hash,
                    "retrieved_at": source.retrieved_at.isoformat(),
                    "query": result.query,
                    "source_type": source.source_type.value,
                    "data_not_instructions": True,
                },
                assumptions=["Web/retrieved content is untrusted data"],
                falsifiers=["Contradicting primary measurement"],
                agent_id=created_by,
                refs=[source.source_id, *ev_ids],
                evidence_strength=_strength(source.trust_tier),
                content_hash=claim_content_hash(
                    statement, EvidenceKind.INFERENCE.value, excerpts[0].text if excerpts else None, None
                ),
                confidence=ConfidenceBreakdown(
                    source_quality=0.2
                    if source.trust_tier == SourceTrustTier.STUB
                    else (0.7 if source.trust_tier == SourceTrustTier.PRIMARY else 0.45)
                ),
                run_id=run_id,
                project_id=bind_project,
                investigation_id=bind_investigation,
                task_id=bind_task,
                contract_version=bind_contract,
            )
            saved = knowledge.save_claim(claim)
            claim_ids.append(saved.claim_id)
            c_node = graph.ensure_node(
                node_type=GraphNodeType.CLAIM,
                ref_id=saved.claim_id,
                run_id=run_id,
                label=saved.claim_id,
                created_by=created_by,
                source_tier=source.trust_tier,
                content_hash=saved.content_hash,
            )
            src_node_id = source_nodes.get(source.source_id)
            if src_node_id:
                graph.ensure_edge(
                    edge_type=GraphEdgeType.CITES,
                    source_id=c_node.node_id,
                    target_id=src_node_id,
                    run_id=run_id,
                    metadata={"query": result.query},
                )
            for ev in excerpts:
                ev_node_id = evidence_nodes.get(ev.evidence_id)
                if ev_node_id:
                    graph.ensure_edge(
                        edge_type=GraphEdgeType.SUPPORTS,
                        source_id=ev_node_id,
                        target_id=c_node.node_id,
                        run_id=run_id,
                    )

    report = {
        "query": result.query,
        "source_ids": [s.source_id for s in result.sources],
        "evidence_ids": [e.evidence_id for e in result.evidence],
        "claim_ids": claim_ids,
        "rejected_hits": list(result.metadata.get("rejected_hits") or []),
    }
    logger.info(
        "Ingested research result query=%r sources=%s evidence=%s claims=%s",
        result.query,
        len(report["source_ids"]),
        len(report["evidence_ids"]),
        len(claim_ids),
    )
    return report
