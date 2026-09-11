"""RunRepository — freeze + immutability verification."""

from __future__ import annotations

import json
from pathlib import Path

from ai_lab.core.models import RunManifest
from ai_lab.knowledge.hashing import sha256_json, sha256_text
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.run_store import RunStore


class ImmutabilityError(RuntimeError):
    """Raised when a frozen run's artifacts were modified in place."""


class JsonRunRepository:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def list_run_ids(self) -> list[str]:
        root = self.store.root / ".runs"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def load_manifest(self, run_id: str) -> RunManifest:
        return RunStore(self.store, run_id).load_manifest()

    def freeze_run(self, run_id: str) -> RunManifest:
        rs = RunStore(self.store, run_id)
        manifest = rs.load_manifest()
        # Compute manifest_hash over stable fields excluding the hash itself
        dump = manifest.model_dump(mode="json")
        dump.pop("manifest_hash", None)
        dump.pop("frozen", None)
        manifest.manifest_hash = sha256_json(dump)
        manifest.frozen = True
        rs.save_manifest(manifest)
        # Write seal file
        self.store.write_json(
            f".runs/{run_id}/freeze_seal.json",
            {"manifest_hash": manifest.manifest_hash, "frozen": True},
        )
        return manifest

    def verify_run_immutable(self, run_id: str) -> None:
        """FAIL LOUD if frozen run content no longer matches seal / hashes."""
        rs = RunStore(self.store, run_id)
        manifest = rs.load_manifest()
        if not manifest.frozen:
            return
        dump = manifest.model_dump(mode="json")
        expected = dump.pop("manifest_hash", None)
        dump.pop("frozen", None)
        # Restore fields removed for hashing consistency with freeze_run
        actual = sha256_json(dump)
        if expected and actual != expected:
            raise ImmutabilityError(
                f"Run {run_id} manifest tampered: hash mismatch"
            )
        # Check computation artifacts still exist with same code_hash if listed
        for art in rs.list_computations():
            rel = f".runs/{run_id}/computations/{art.artifact_id}.json"
            raw = self.store.read_json(rel)
            if raw.get("code_hash") != art.code_hash:
                raise ImmutabilityError(
                    f"Computation {art.artifact_id} modified in frozen run {run_id}"
                )
            # Detect silent rewrite by rehashing code field
            if art.code and sha256_text(art.code) != art.code_hash and art.code_hash != "unknown":
                raise ImmutabilityError(
                    f"Computation {art.artifact_id} code_hash mismatch in run {run_id}"
                )

    def detect_in_place_claim_mutation(self, run_id: str, claim_path: Path, expected_hash: str) -> None:
        data = json.loads(claim_path.read_text(encoding="utf-8"))
        from ai_lab.knowledge.hashing import claim_content_hash

        actual = claim_content_hash(
            data.get("statement", ""),
            data.get("kind", ""),
            data.get("evidence"),
            data.get("math_check"),
        )
        if actual != expected_hash:
            raise ImmutabilityError(f"Claim file mutated in place: {claim_path}")
