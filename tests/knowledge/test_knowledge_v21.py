"""Knowledge V2.1 — run namespaces, graph integrity, approved knowledge, replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_lab.core.enums import (
    AdjudicationStatus,
    AgreementType,
    ClaimLifecycle,
    ClaimVisibility,
    ConflictStatus,
    EvidenceKind,
    GraphEdgeType,
    GraphNodeType,
    VerificationStatus,
)
from ai_lab.core.models import Claim, GraphEdge, GraphNode
from ai_lab.knowledge.approved import (
    demote_approved_knowledge,
    promote_to_approved_knowledge,
)
from ai_lab.knowledge.compare import compare_runs
from ai_lab.knowledge.conflicts import create_conflict, resolve_conflict
from ai_lab.knowledge.graph import GraphIntegrityError, JsonEvidenceRepository
from ai_lab.knowledge.hashing import claim_content_hash, sha256_text
from ai_lab.knowledge.json_knowledge import JsonKnowledgeRepository
from ai_lab.knowledge.migration import migrate_project_knowledge
from ai_lab.knowledge.models import ReplayRecord
from ai_lab.knowledge.queries import EvidenceQueryService, get_project_timeline
from ai_lab.knowledge.replay import ReplayProvider, ensure_fixture_dirs, load_replay, save_replay
from ai_lab.knowledge.runs import ImmutabilityError, JsonRunRepository
from ai_lab.knowledge import KnowledgeService
from ai_lab.core.enums import AgentRole
from ai_lab.core.models import LabConfig, RunBudget, RunManifest
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.run_store import RunStore


def _proj(tmp_path: Path, name: str = "silk") -> ProjectStore:
    root = tmp_path / name
    root.mkdir()
    store = ProjectStore(root)
    store.ensure_layout()
    return store


def test_claim_namespace_isolation(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    repo = JsonKnowledgeRepository(store)
    c1 = Claim(
        statement="run1",
        kind=EvidenceKind.ASSUMPTION,
        project_id=store.name,
        run_id="run_a",
        investigation_id=store.name,
        task_id="task_test",
    )
    c2 = Claim(
        statement="run2",
        kind=EvidenceKind.ASSUMPTION,
        project_id=store.name,
        run_id="run_b",
        investigation_id=store.name,
        task_id="task_test",
    )
    repo.save_claim(c1)
    repo.save_claim(c2)
    only_a = repo.list_claims(visibility=ClaimVisibility.CURRENT_RUN, run_id="run_a")
    only_b = repo.list_claims(visibility=ClaimVisibility.CURRENT_RUN, run_id="run_b")
    assert len(only_a) == 1 and only_a[0].statement == "run1"
    assert len(only_b) == 1 and only_b[0].statement == "run2"
    assert c1.canonical_id.startswith(f"{store.name}/run_a/")


def test_old_run_claims_invisible_by_default(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    ks = KnowledgeService(store, run_id="run_new", auto_migrate=False)
    JsonKnowledgeRepository(store).save_claim(
        Claim(
            statement="old",
            kind=EvidenceKind.INFERENCE,
            evidence="e",
            project_id=store.name,
            run_id="run_old",
            investigation_id=store.name,
            task_id="task_test",
        )
    )
    visible = ks.list_for_agent(AgentRole.VERIFICATION)
    assert all(c.run_id == "run_new" or c.statement != "old" for c in visible)
    assert not any(c.statement == "old" for c in visible)


def test_approved_knowledge_visibility(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    claim = Claim(
        statement="proven",
        kind=EvidenceKind.CALCULATION,
        evidence="recompute",
        project_id=store.name,
        run_id="run_ok",
        content_hash="abc",
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        investigation_id=store.name,
        task_id="task_test",
    )
    JsonKnowledgeRepository(store).save_claim(claim)
    promote_to_approved_knowledge(
        store,
        claim=claim,
        verification_status=VerificationStatus.PASS,
        adjudication_status=AdjudicationStatus.PASS,
        red_team_completed=True,
        deterministic_critical_ok=True,
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
    )
    approved = JsonKnowledgeRepository(store).list_claims(
        visibility=ClaimVisibility.APPROVED_KNOWLEDGE
    )
    assert any(c.statement == "proven" for c in approved)
    # Verification agent still CURRENT_RUN only
    ks = KnowledgeService(store, run_id="run_other", auto_migrate=False)
    assert not any(c.statement == "proven" for c in ks.list_for_agent(AgentRole.VERIFICATION))
    chief = ks.list_for_agent(AgentRole.CHIEF_ENGINEER)
    assert any(c.statement == "proven" for c in chief)


def test_supersede_chain(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    repo = JsonKnowledgeRepository(store)
    c1 = Claim(statement="v1", kind=EvidenceKind.ASSUMPTION, project_id=store.name, run_id="r1", investigation_id=store.name, task_id="task_test")
    repo.save_claim(c1)
    c2 = Claim(statement="v2", kind=EvidenceKind.ASSUMPTION, project_id=store.name, run_id="r1", investigation_id=store.name, task_id="task_test")
    repo.supersede_claim(c1.claim_id, c2)
    old = repo.get_claim(c1.claim_id)
    assert old.lifecycle == ClaimLifecycle.SUPERSEDED
    assert old.statement == "v1"
    hist = EvidenceQueryService(store, JsonEvidenceRepository(store), repo).find_claim_history(
        c2.claim_id
    )
    assert len(hist) >= 2


def test_graph_dangling_edge_rejection(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    g = JsonEvidenceRepository(store)
    with pytest.raises(GraphIntegrityError, match="Dangling"):
        g.add_edge(
            GraphEdge(
                edge_type=GraphEdgeType.SUPPORTS,
                source_id="missing_a",
                target_id="missing_b",
                project_id=store.name,
            )
        )


def test_invalid_edge_type_rejection(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    g = JsonEvidenceRepository(store)
    n = g.add_node(GraphNode(node_type=GraphNodeType.CLAIM, project_id=store.name, ref_id="c1"))
    with pytest.raises(Exception):
        GraphEdge(edge_type="NOT_A_TYPE", source_id=n.node_id, target_id=n.node_id)  # type: ignore[arg-type]


def test_cross_project_edge_rejection(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    g = JsonEvidenceRepository(store)
    a = g.add_node(GraphNode(node_type=GraphNodeType.CLAIM, project_id=store.name, ref_id="a"))
    # Manually inject foreign node
    nodes = g._load_nodes()
    nodes.append(
        GraphNode(
            node_type=GraphNodeType.CLAIM, project_id="other_project", ref_id="b"
        ).model_dump(mode="json")
    )
    g._save_nodes(nodes)
    foreign = [n for n in g.list_nodes() if n.ref_id == "b"][0]
    with pytest.raises(GraphIntegrityError, match="Cross-project"):
        g.add_edge(
            GraphEdge(
                edge_type=GraphEdgeType.SUPPORTS,
                source_id=a.node_id,
                target_id=foreign.node_id,
                project_id=store.name,
            )
        )


def test_decision_traceability(tmp_path: Path) -> None:
    from ai_lab.core.enums import DecisionStatus
    from ai_lab.core.models import DecisionRecord

    with pytest.raises(ValueError, match="ACCEPTED Decision requires"):
        DecisionRecord(question="q", status=DecisionStatus.ACCEPTED)

    ok = DecisionRecord(
        question="q",
        status=DecisionStatus.ACCEPTED,
        accepted_claims=["claim_1"],
        supporting_evidence=["node_1"],
    )
    assert ok.accepted_claims


def test_conflict_creation_and_resolution(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    g = JsonEvidenceRepository(store)
    repo = JsonKnowledgeRepository(store)
    for stmt, rid in (("A", "cA"), ("B", "cB")):
        repo.save_claim(
            Claim(
                claim_id=rid,
                statement=stmt,
                kind=EvidenceKind.ASSUMPTION,
                project_id=store.name,
                run_id="r1",
                investigation_id=store.name,
                task_id="task_test",
            )
        )
    conf = create_conflict(store, g, claim_a="cA", claim_b="cB", run_id="r1")
    assert conf.status == ConflictStatus.OPEN
    resolved = resolve_conflict(
        store, conf.conflict_id, status=ConflictStatus.ACCEPTED_A, reason="test"
    )
    assert resolved.status == ConflictStatus.ACCEPTED_A
    # Cannot hide by overwrite — both claims still exist
    assert repo.get_claim("cA").statement == "A"
    assert repo.get_claim("cB").statement == "B"


def test_promotion_rejection_when_verification_fails(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    claim = Claim(
        statement="x",
        kind=EvidenceKind.INFERENCE,
        evidence="e",
        project_id=store.name,
        run_id="r1",
        content_hash="h",
        investigation_id=store.name,
        task_id="task_test",
    )
    with pytest.raises(PermissionError, match="Promotion denied"):
        promote_to_approved_knowledge(
            store,
            claim=claim,
            verification_status=VerificationStatus.FAIL,
            adjudication_status=AdjudicationStatus.PASS,
            red_team_completed=True,
            deterministic_critical_ok=True,
            agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
        )


def test_promotion_success_when_gates_pass(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    claim = Claim(
        statement="ok",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        project_id=store.name,
        run_id="r1",
        content_hash="h",
        investigation_id=store.name,
        task_id="task_test",
    )
    entry = promote_to_approved_knowledge(
        store,
        claim=claim,
        verification_status=VerificationStatus.PASS,
        adjudication_status=AdjudicationStatus.PASS,
        red_team_completed=True,
        deterministic_critical_ok=True,
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
    )
    assert entry.status == "ACTIVE"


def test_promotion_rejects_consensus_only(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    claim = Claim(
        statement="consensus",
        kind=EvidenceKind.INFERENCE,
        evidence="e",
        project_id=store.name,
        run_id="r1",
        content_hash="h",
        investigation_id=store.name,
        task_id="task_test",
    )
    with pytest.raises(PermissionError, match="CONSENSUS"):
        promote_to_approved_knowledge(
            store,
            claim=claim,
            verification_status=VerificationStatus.PASS,
            adjudication_status=AdjudicationStatus.PASS,
            red_team_completed=True,
            deterministic_critical_ok=True,
            agreement_type=AgreementType.CONSENSUS,
        )


def test_knowledge_demotion(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    claim = Claim(
        statement="ok",
        kind=EvidenceKind.CALCULATION,
        evidence="e",
        project_id=store.name,
        run_id="r1",
        content_hash="h",
        claim_id="claim_demote",
        investigation_id=store.name,
        task_id="task_test",
    )
    promote_to_approved_knowledge(
        store,
        claim=claim,
        verification_status=VerificationStatus.PASS,
        adjudication_status=AdjudicationStatus.PASS,
        red_team_completed=True,
        deterministic_critical_ok=True,
        agreement_type=AgreementType.INDEPENDENT_EVIDENCE,
    )
    demoted = demote_approved_knowledge(
        store, claim_id="claim_demote", reason="new evidence", demoted_by_claim_id="claim_new"
    )
    assert demoted.status == "DEMOTED"
    assert demoted.demotion_reason == "new evidence"


def test_compare_runs(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    repo = JsonKnowledgeRepository(store)
    g = JsonEvidenceRepository(store)
    repo.save_claim(
        Claim(statement="a", kind=EvidenceKind.ASSUMPTION, project_id=store.name, run_id="run1", investigation_id=store.name, task_id="task_test")
    )
    repo.save_claim(
        Claim(statement="b", kind=EvidenceKind.ASSUMPTION, project_id=store.name, run_id="run2", investigation_id=store.name, task_id="task_test")
    )
    g.ensure_node(node_type=GraphNodeType.CLAIM, ref_id="x", run_id="run2", label="x")
    cmp = compare_runs(store, repo, g, "run1", "run2")
    assert cmp.new_claims
    assert isinstance(cmp.new_evidence_nodes, list)


def test_immutable_artifact_modification_detection(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    rs = RunStore(store, "run_freeze")
    config = LabConfig(provider="mock")
    rs.build_manifest(config=config, repo_root=tmp_path, budget=RunBudget())
    rs.finish_manifest(final_state="COMPLETED")
    runs = JsonRunRepository(store)
    runs.freeze_run("run_freeze")
    # Tamper manifest
    man = rs.load_manifest()
    man.notes.append("tamper")
    rs.save_manifest(man)
    with pytest.raises(ImmutabilityError):
        runs.verify_run_immutable("run_freeze")


def test_evidence_path_query(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    g = JsonEvidenceRepository(store)
    repo = JsonKnowledgeRepository(store)
    claim = Claim(
        claim_id="clm_path",
        statement="s",
        kind=EvidenceKind.ASSUMPTION,
        project_id=store.name,
        run_id="r1",
        investigation_id=store.name,
        task_id="task_test",
    )
    repo.save_claim(claim)
    cn = g.ensure_node(node_type=GraphNodeType.CLAIM, ref_id="clm_path", run_id="r1")
    vn = g.ensure_node(node_type=GraphNodeType.VERIFICATION, ref_id="ver_1", run_id="r1")
    g.add_edge(
        GraphEdge(
            edge_type=GraphEdgeType.TESTS,
            source_id=vn.node_id,
            target_id=cn.node_id,
            project_id=store.name,
            run_id="r1",
        )
    )
    q = EvidenceQueryService(store, g, repo)
    chain = q.find_verification_chain("clm_path")
    assert any("ver_1" in str(step) or step.get("node", {}).get("ref_id") == "ver_1" for step in chain)


@pytest.mark.asyncio
async def test_replay_fixture(tmp_path: Path) -> None:
    ensure_fixture_dirs(tmp_path)
    rec = ReplayRecord(
        role="verification",
        request={"messages": []},
        response={"status": "PASS"},
        model="mock",
    )
    path = save_replay(tmp_path, "verification", rec)
    loaded = load_replay(path)
    assert loaded.response["status"] == "PASS"
    provider = ReplayProvider({"verification": loaded})
    from ai_lab.core.models import LLMMessage, LLMRequest

    resp = await provider.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="x")],
            metadata={"agent_role": "verification"},
        )
    )
    assert resp.parsed["status"] == "PASS"


def test_no_accidental_stale_claim_leakage(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    repo = JsonKnowledgeRepository(store)
    repo.save_claim(
        Claim(
            statement="stale",
            kind=EvidenceKind.ASSUMPTION,
            project_id=store.name,
            run_id="run_old",
            investigation_id=store.name,
            task_id="task_test",
        )
    )
    ev = __import__("ai_lab.memory.evidence_store", fromlist=["EvidenceStore"]).EvidenceStore(
        store, run_id="run_new"
    )
    assert ev.list_claims() == []


def test_migration_non_destructive(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    # Legacy flat claim
    store.write_json(
        "research/claim_legacy.json",
        {
            "claim_id": "claim_legacy",
            "statement": "legacy",
            "kind": "ASSUMPTION",
            "version": 1,
        },
    )
    store.write_json("research/claims_index.json", {"claim_legacy": "research/claim_legacy.json"})
    report = migrate_project_knowledge(store)
    assert report["migrated_claims"] >= 1
    assert (store.root / "research" / "claim_legacy.json").exists()
    assert (store.root / ".runs" / "run_legacy_migrated" / "claims").exists()


def test_bidirectional_supersedes_forbidden(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    g = JsonEvidenceRepository(store)
    a = g.add_node(GraphNode(node_type=GraphNodeType.CLAIM, project_id=store.name, ref_id="a"))
    b = g.add_node(GraphNode(node_type=GraphNodeType.CLAIM, project_id=store.name, ref_id="b"))
    g.add_edge(
        GraphEdge(
            edge_type=GraphEdgeType.SUPERSEDES,
            source_id=a.node_id,
            target_id=b.node_id,
            project_id=store.name,
        )
    )
    with pytest.raises(GraphIntegrityError, match="Bidirectional SUPERSEDES"):
        g.add_edge(
            GraphEdge(
                edge_type=GraphEdgeType.SUPERSEDES,
                source_id=b.node_id,
                target_id=a.node_id,
                project_id=store.name,
            )
        )


def test_project_timeline(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    repo = JsonKnowledgeRepository(store)
    repo.save_claim(
        Claim(statement="t", kind=EvidenceKind.ASSUMPTION, project_id=store.name, run_id="run_tl", investigation_id=store.name, task_id="task_test")
    )
    events = get_project_timeline(store, repo)
    assert any(e.run_id == "run_tl" for e in events)
