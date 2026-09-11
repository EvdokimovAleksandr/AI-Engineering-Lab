"""Evidence / claim index stored as JSON files under research/ and reviews/."""

from __future__ import annotations

from pathlib import Path

from ai_lab.core.enums import EvidenceKind
from ai_lab.core.models import Claim
from ai_lab.memory.project_store import ProjectStore


class EvidenceStore:
    """Stores claims as individual JSON artifacts; never upgrades kind silently."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self._index_rel = "research/claims_index.json"

    def _load_index(self) -> dict[str, str]:
        try:
            data = self.store.read_json(self._index_rel)
            if not isinstance(data, dict):
                raise TypeError(f"claims_index must be a dict, got {type(data)}")
            return {str(k): str(v) for k, v in data.items()}
        except FileNotFoundError:
            return {}

    def _save_index(self, index: dict[str, str]) -> None:
        self.store.write_json(self._index_rel, index)

    def save_claim(self, claim: Claim, subdirectory: str = "research") -> str:
        """Persist claim; returns relative path."""
        if claim.kind == EvidenceKind.FACT and not claim.source and not claim.evidence:
            raise ValueError("Refusing to store FACT without source/evidence")
        rel = f"{subdirectory}/{claim.claim_id}.json"
        self.store.write_json(rel, claim.model_dump(mode="json"))
        index = self._load_index()
        index[claim.claim_id] = rel
        self._save_index(index)
        return rel

    def load_claim(self, claim_id: str) -> Claim:
        index = self._load_index()
        if claim_id not in index:
            raise KeyError(f"Unknown claim_id: {claim_id}")
        raw = self.store.read_json(index[claim_id])
        return Claim.model_validate(raw)

    def list_claims(self) -> list[Claim]:
        index = self._load_index()
        return [self.load_claim(cid) for cid in sorted(index)]

    def path_for(self, claim_id: str) -> Path:
        index = self._load_index()
        if claim_id not in index:
            raise KeyError(f"Unknown claim_id: {claim_id}")
        return self.store.resolve(index[claim_id])
