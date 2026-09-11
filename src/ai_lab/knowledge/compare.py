"""Compare two lab runs for iterative research."""

from __future__ import annotations

from ai_lab.core.enums import ClaimVisibility, GraphNodeType
from ai_lab.knowledge.graph import JsonEvidenceRepository
from ai_lab.knowledge.json_knowledge import JsonKnowledgeRepository
from ai_lab.knowledge.models import RunComparison
from ai_lab.memory.project_store import ProjectStore


def compare_runs(
    store: ProjectStore,
    knowledge: JsonKnowledgeRepository,
    graph: JsonEvidenceRepository,
    run_a: str,
    run_b: str,
) -> RunComparison:
    claims_a = {
        c.claim_id: c
        for c in knowledge.list_claims(
            visibility=ClaimVisibility.CURRENT_RUN, run_id=run_a, include_superseded=True
        )
    }
    claims_b = {
        c.claim_id: c
        for c in knowledge.list_claims(
            visibility=ClaimVisibility.CURRENT_RUN, run_id=run_b, include_superseded=True
        )
    }
    new_claims = sorted(set(claims_b) - set(claims_a))
    changed = []
    superseded = []
    for cid, cb in claims_b.items():
        if cid in claims_a:
            ca = claims_a[cid]
            if (ca.content_hash or ca.statement) != (cb.content_hash or cb.statement):
                changed.append(cid)
            if cb.supersedes or ca.superseded_by:
                superseded.append(cid)
        if cb.supersedes and cb.supersedes not in claims_b:
            superseded.append(cb.claim_id)

    nodes_a = {n.ref_id or n.node_id for n in graph.list_nodes(run_id=run_a)}
    nodes_b = {n.ref_id or n.node_id for n in graph.list_nodes(run_id=run_b)}
    new_evidence = sorted(nodes_b - nodes_a)

    edges_b = graph.list_edges(run_id=run_b)
    new_contradictions = [
        e.edge_id for e in edges_b if e.edge_type.value == "CONTRADICTS"
    ]

    sims_b = [
        n.ref_id or n.node_id
        for n in graph.list_nodes(run_id=run_b)
        if n.node_type in {GraphNodeType.SIMULATION, GraphNodeType.CALCULATION}
    ]
    sims_a = {
        n.ref_id or n.node_id
        for n in graph.list_nodes(run_id=run_a)
        if n.node_type in {GraphNodeType.SIMULATION, GraphNodeType.CALCULATION}
    }
    new_sims = sorted(set(sims_b) - sims_a)

    vers_b = [
        n.ref_id or n.node_id
        for n in graph.list_nodes(run_id=run_b)
        if n.node_type == GraphNodeType.VERIFICATION
    ]
    vers_a = {
        n.ref_id or n.node_id
        for n in graph.list_nodes(run_id=run_a)
        if n.node_type == GraphNodeType.VERIFICATION
    }

    # Decisions from decision log filtered by run if present
    from ai_lab.memory.decision_log import DecisionLog

    log = DecisionLog(store.root / "decisions" / "decision_log.jsonl")
    decs = log.read_all()
    changed_decisions = [
        d.decision_id
        for d in decs
        if d.run_id == run_b and d.decision_id
    ]

    return RunComparison(
        run_a=run_a,
        run_b=run_b,
        new_claims=new_claims,
        changed_claims=sorted(set(changed)),
        superseded_claims=sorted(set(superseded)),
        new_evidence_nodes=new_evidence,
        new_contradictions=new_contradictions,
        changed_decisions=changed_decisions,
        new_simulations=new_sims,
        new_verification_results=sorted(set(vers_b) - vers_a),
    )
