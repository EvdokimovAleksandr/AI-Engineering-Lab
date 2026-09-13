"""PR-C: new writes require full ExecutionContext; legacy only via explicit migrate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_lab.core.enums import EvidenceKind, LabErrorCode
from ai_lab.core.execution_context import (
    MissingExecutionContextError,
    require_write_execution_context,
)
from ai_lab.core.models import Claim, ComputationArtifact
from ai_lab.knowledge.json_knowledge import JsonKnowledgeRepository
from ai_lab.knowledge.migration import migrate_project_knowledge
from ai_lab.memory.evidence_store import EvidenceStore
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.run_store import RunStore


def _proj(tmp_path: Path, name: str = "investigation_ctx") -> ProjectStore:
    root = tmp_path / name
    root.mkdir()
    (root / "problem.md").write_text("context gate fixture", encoding="utf-8")
    store = ProjectStore(root)
    store.ensure_layout()
    return store


def _full_ids(store: ProjectStore, *, run_id: str = "run_full", task_id: str = "calculation") -> dict:
    return {
        "project_id": store.name,
        "investigation_id": store.name,
        "task_id": task_id,
        "run_id": run_id,
    }


def test_new_computation_requires_full_context(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    rs = RunStore(store, "run_full")
    incomplete = ComputationArtifact(run_id="run_full", code="print(1)")
    with pytest.raises(MissingExecutionContextError) as ei:
        rs.save_computation(incomplete)
    assert ei.value.code == LabErrorCode.MISSING_EXECUTION_CONTEXT
    assert "project_id" in ei.value.missing
    assert "task_id" in ei.value.missing

    full = ComputationArtifact(code="print(1)", **_full_ids(store))
    rel = rs.save_computation(full)
    on_disk = json.loads((store.root / rel).read_text(encoding="utf-8"))
    for key in ("project_id", "investigation_id", "task_id", "run_id"):
        assert on_disk.get(key) == _full_ids(store)[key]


def test_new_claim_requires_full_context(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    evidence = EvidenceStore(store, run_id="run_full")
    bare = Claim(
        statement="bare claim",
        kind=EvidenceKind.INFERENCE,
        run_id="run_full",
    )
    with pytest.raises(MissingExecutionContextError) as ei:
        evidence.save_claim(bare)
    assert ei.value.code == LabErrorCode.MISSING_EXECUTION_CONTEXT

    ok = Claim(
        statement="bound claim",
        kind=EvidenceKind.INFERENCE,
        **_full_ids(store),
    )
    path = evidence.save_claim(ok)
    raw = json.loads((store.root / path).read_text(encoding="utf-8"))
    for key in ("project_id", "investigation_id", "task_id", "run_id"):
        assert raw.get(key)


def test_new_evidence_requires_full_context(tmp_path: Path) -> None:
    """EvidenceStore is the evidence write path — incomplete identity hard-fails."""
    store = _proj(tmp_path)
    evidence = EvidenceStore(store, run_id="run_ev")
    # Missing task_id / investigation_id must not be soft-filled from the store.
    partial = Claim(
        statement="evidence gap",
        kind=EvidenceKind.EVIDENCE_GAP,
        project_id=store.name,
        run_id="run_ev",
    )
    with pytest.raises(MissingExecutionContextError) as ei:
        evidence.save_claim(partial)
    assert ei.value.code == LabErrorCode.MISSING_EXECUTION_CONTEXT
    assert "task_id" in ei.value.missing or "investigation_id" in ei.value.missing


def test_missing_context_cannot_be_saved(tmp_path: Path) -> None:
    store = _proj(tmp_path)
    rs = RunStore(store, "run_x")
    repo = JsonKnowledgeRepository(store)

    with pytest.raises(MissingExecutionContextError):
        require_write_execution_context(
            {"run_id": "run_x", "project_id": store.name},
            where="unit",
        )

    art = ComputationArtifact(
        run_id="run_x",
        project_id=store.name,
        investigation_id=store.name,
        # task_id intentionally omitted
        code="x=1",
    )
    with pytest.raises(MissingExecutionContextError):
        rs.save_computation(art)

    claim = Claim(
        statement="no task",
        kind=EvidenceKind.ASSUMPTION,
        project_id=store.name,
        investigation_id=store.name,
        run_id="run_x",
    )
    with pytest.raises(MissingExecutionContextError):
        repo.save_claim(claim)


def test_legacy_migration_is_explicit(tmp_path: Path) -> None:
    store = _proj(tmp_path, name="legacy_proj")
    # Legacy flat claim index (pre-run-scoped knowledge).
    research = store.root / "research"
    research.mkdir(parents=True, exist_ok=True)
    claim_id = "claim_legacy_1"
    legacy_rel = f"research/{claim_id}.json"
    (store.root / legacy_rel).write_text(
        json.dumps(
            {
                "claim_id": claim_id,
                "statement": "legacy statement",
                "kind": "INFERENCE",
                "version": 1,
            }
        ),
        encoding="utf-8",
    )
    (research / "claims_index.json").write_text(
        json.dumps({claim_id: legacy_rel}),
        encoding="utf-8",
    )

    # Normal write path still refuses incomplete identity.
    rs = RunStore(store, "run_new")
    with pytest.raises(MissingExecutionContextError):
        rs.save_computation(ComputationArtifact(run_id="run_new", code="print(0)"))

    # Explicit migration copies legacy claims without going through soft runtime fill.
    report = migrate_project_knowledge(store, legacy_run_id="run_legacy_migrated")
    assert report["migrated_claims"] >= 1
    migrated = store.root / ".runs" / "run_legacy_migrated" / "claims" / f"{claim_id}_v1.json"
    assert migrated.is_file()

    # Explicit legacy_migrate flag is the only bypass on RunStore / EvidenceStore.
    legacy_art = ComputationArtifact(run_id="run_legacy_migrated", code="legacy")
    rel = rs.save_computation(legacy_art, legacy_migrate=True)
    assert (store.root / rel).is_file()


def test_existing_legacy_artifact_can_be_read_without_relaxing_new_writes(
    tmp_path: Path,
) -> None:
    store = _proj(tmp_path)
    rs = RunStore(store, "run_legacy_read")
    folder = store.root / ".runs" / "run_legacy_read" / "computations"
    folder.mkdir(parents=True, exist_ok=True)
    # Pre-PR-C on-disk JSON: only run_id (legacy). Written outside save_computation.
    legacy_id = "comp_legacy_only_run"
    legacy_path = folder / f"{legacy_id}.json"
    legacy_path.write_text(
        json.dumps(
            {
                "artifact_id": legacy_id,
                "run_id": "run_legacy_read",
                "kind": "calculation",
                "code": "print(1)",
                "status": "ok",
            }
        ),
        encoding="utf-8",
    )

    loaded = rs.list_computations()
    assert any(a.artifact_id == legacy_id for a in loaded)
    legacy = next(a for a in loaded if a.artifact_id == legacy_id)
    assert legacy.project_id is None
    assert legacy.task_id is None

    # New write path remains strict — reading legacy must not relax saves.
    with pytest.raises(MissingExecutionContextError) as ei:
        rs.save_computation(
            ComputationArtifact(run_id="run_legacy_read", code="print(2)")
        )
    assert ei.value.code == LabErrorCode.MISSING_EXECUTION_CONTEXT

    full = ComputationArtifact(code="print(3)", **_full_ids(store, run_id="run_legacy_read"))
    new_rel = rs.save_computation(full)
    new_raw = json.loads((store.root / new_rel).read_text(encoding="utf-8"))
    assert new_raw["project_id"] == store.name
    assert new_raw["investigation_id"] == store.name
    assert new_raw["task_id"] == "calculation"
    assert new_raw["run_id"] == "run_legacy_read"
