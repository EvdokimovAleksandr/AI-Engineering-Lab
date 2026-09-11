"""JSON-file KnowledgeRepository — run-scoped claims + project indexes.

Layout:
  projects/<name>/
    knowledge/
      schema_version.json
      approved/*.json + index.json
      conflicts/*.json
      conclusions/*.json
      decisions_trace/*.json
      graph/nodes.json + edges.json
    .runs/<run_id>/claims/<claim_id>_vN.json + index.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_lab.core.enums import ClaimLifecycle, ClaimVisibility, EvidenceKind
from ai_lab.core.models import Claim
from ai_lab.knowledge.hashing import claim_content_hash
from ai_lab.memory.project_store import ProjectStore

SCHEMA_VERSION = "2.1"


class JsonKnowledgeRepository:
    """Physical JSON backend behind KnowledgeRepository protocol."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self.project_id = store.name
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        for sub in (
            "knowledge",
            "knowledge/approved",
            "knowledge/conflicts",
            "knowledge/conclusions",
            "knowledge/decisions_trace",
            "knowledge/graph",
            "knowledge/timeline",
        ):
            (self.store.root / sub).mkdir(parents=True, exist_ok=True)
        ver_path = self.store.root / "knowledge" / "schema_version.json"
        if not ver_path.exists():
            self.store.write_json(
                "knowledge/schema_version.json",
                {"schema_version": SCHEMA_VERSION, "migrated_at": datetime.now(timezone.utc).isoformat()},
            )

    def _run_claims_dir(self, run_id: str) -> Path:
        path = self.store.root / ".runs" / run_id / "claims"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_index_rel(self, run_id: str) -> str:
        return f".runs/{run_id}/claims/index.json"

    def _load_run_index(self, run_id: str) -> dict[str, str]:
        try:
            data = self.store.read_json(self._run_index_rel(run_id))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}

    def _save_run_index(self, run_id: str, index: dict[str, str]) -> None:
        self.store.write_json(self._run_index_rel(run_id), index)

    def _global_index_rel(self) -> str:
        return "knowledge/claims_global_index.json"

    def _load_global_index(self) -> dict[str, dict[str, str]]:
        """claim_id -> {run_id, path, canonical_id}"""
        try:
            data = self.store.read_json(self._global_index_rel())
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}

    def _save_global_index(self, index: dict[str, dict[str, str]]) -> None:
        self.store.write_json(self._global_index_rel(), index)

    def save_claim(self, claim: Claim) -> Claim:
        if claim.kind == EvidenceKind.FACT and not claim.source and not claim.evidence:
            raise ValueError("Refusing to store FACT without source/evidence")
        if not claim.run_id:
            raise ValueError("Claim must have run_id for run-scoped namespace")
        if not claim.project_id:
            claim.project_id = self.project_id
        if claim.project_id != self.project_id:
            raise ValueError(
                f"Cross-project claim write forbidden: {claim.project_id} != {self.project_id}"
            )
        if not claim.content_hash:
            claim.content_hash = claim_content_hash(
                claim.statement,
                claim.kind.value,
                claim.evidence,
                claim.math_check,
                claim.verification_spec,
            )

        filename = f"{claim.claim_id}_v{claim.version}.json"
        rel = f".runs/{claim.run_id}/claims/{filename}"
        full = self.store.root / rel
        if full.exists():
            existing = Claim.model_validate(self.store.read_json(rel))
            if existing.content_hash and claim.content_hash and existing.content_hash != claim.content_hash:
                raise FileExistsError(
                    f"Immutable claim collision {claim.canonical_id}; use supersede_claim()"
                )
            if existing.model_dump(mode="json") != claim.model_dump(mode="json"):
                raise FileExistsError(
                    f"Claim {claim.canonical_id} exists; use supersede_claim() for versions"
                )
        self.store.write_json(rel, claim.model_dump(mode="json"))
        ridx = self._load_run_index(claim.run_id)
        ridx[claim.claim_id] = rel
        self._save_run_index(claim.run_id, ridx)
        gidx = self._load_global_index()
        gidx[claim.claim_id] = {
            "run_id": claim.run_id,
            "path": rel,
            "canonical_id": claim.canonical_id,
            "version": str(claim.version),
        }
        self._save_global_index(gidx)
        return claim

    def get_claim(self, claim_id: str, *, run_id: str | None = None) -> Claim:
        if run_id:
            ridx = self._load_run_index(run_id)
            if claim_id not in ridx:
                raise KeyError(f"Unknown claim_id={claim_id} in run={run_id}")
            return Claim.model_validate(self.store.read_json(ridx[claim_id]))
        gidx = self._load_global_index()
        if claim_id not in gidx:
            raise KeyError(f"Unknown claim_id={claim_id}")
        return Claim.model_validate(self.store.read_json(gidx[claim_id]["path"]))

    def list_claims(
        self,
        *,
        visibility: ClaimVisibility,
        run_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[Claim]:
        if visibility == ClaimVisibility.CURRENT_RUN:
            if not run_id:
                raise ValueError("CURRENT_RUN visibility requires run_id")
            claims = [
                Claim.model_validate(self.store.read_json(path))
                for path in self._load_run_index(run_id).values()
            ]
        elif visibility == ClaimVisibility.PROJECT_HISTORY:
            claims = []
            for meta in self._load_global_index().values():
                claims.append(Claim.model_validate(self.store.read_json(meta["path"])))
        elif visibility == ClaimVisibility.APPROVED_KNOWLEDGE:
            from ai_lab.knowledge.approved import list_approved_as_claims

            return list_approved_as_claims(self.store)
        else:
            raise ValueError(f"Unknown visibility: {visibility}")

        if not include_superseded:
            claims = [
                c
                for c in claims
                if not c.superseded_by and c.lifecycle not in {
                    ClaimLifecycle.SUPERSEDED,
                    ClaimLifecycle.ARCHIVED,
                }
            ]
        return sorted(claims, key=lambda c: c.claim_id)

    def supersede_claim(self, old_claim_id: str, new_claim: Claim) -> Claim:
        old = self.get_claim(old_claim_id, run_id=new_claim.run_id or None)
        if old.lifecycle == ClaimLifecycle.SUPERSEDED or old.superseded_by:
            raise ValueError(f"Claim {old_claim_id} already superseded by {old.superseded_by}")
        if not new_claim.run_id:
            new_claim.run_id = old.run_id
        if not new_claim.project_id:
            new_claim.project_id = old.project_id or self.project_id
        new_claim.supersedes = old_claim_id
        new_claim.version = int(old.version) + 1
        new_claim.lifecycle = ClaimLifecycle.ACTIVE
        saved = self.save_claim(new_claim)
        # Metadata-only update on old claim (lifecycle pointer) — allowed exception
        old.superseded_by = saved.claim_id
        old.lifecycle = ClaimLifecycle.SUPERSEDED
        old_rel = self._load_global_index()[old_claim_id]["path"]
        self.store.write_json(old_rel, old.model_dump(mode="json"))
        return saved

    def set_lifecycle(self, claim_id: str, lifecycle: str, *, run_id: str | None = None) -> Claim:
        claim = self.get_claim(claim_id, run_id=run_id)
        claim.lifecycle = ClaimLifecycle(lifecycle)
        rel = self._load_global_index()[claim_id]["path"]
        self.store.write_json(rel, claim.model_dump(mode="json"))
        return claim

    def list_all_run_ids_with_claims(self) -> list[str]:
        runs_root = self.store.root / ".runs"
        if not runs_root.is_dir():
            return []
        out = []
        for p in sorted(runs_root.iterdir()):
            if p.is_dir() and (p / "claims").is_dir():
                out.append(p.name)
        return out
