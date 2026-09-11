"""Evidence / claim store — delegates to run-scoped KnowledgeRepository."""

from __future__ import annotations

from pathlib import Path

from ai_lab.core.enums import ClaimVisibility, EvidenceKind
from ai_lab.core.models import Claim
from ai_lab.knowledge.json_knowledge import JsonKnowledgeRepository
from ai_lab.memory.project_store import ProjectStore


class EvidenceStore:
    """
    Compatibility facade over JsonKnowledgeRepository.

    Default list_claims() returns CURRENT_RUN only (no stale leakage).
    Pass visibility=PROJECT_HISTORY explicitly for history access.
    """

    def __init__(self, store: ProjectStore, *, run_id: str | None = None) -> None:
        self.store = store
        self.run_id = run_id
        self._repo = JsonKnowledgeRepository(store)

    def set_run_id(self, run_id: str) -> None:
        self.run_id = run_id

    def save_claim(self, claim: Claim, subdirectory: str = "research") -> str:
        """Persist claim into current run namespace. `subdirectory` kept for API compat."""
        _ = subdirectory
        if claim.kind == EvidenceKind.FACT and not claim.source and not claim.evidence:
            raise ValueError("Refusing to store FACT without source/evidence")
        if not claim.run_id:
            if not self.run_id:
                raise ValueError("EvidenceStore.save_claim requires run_id on claim or store")
            claim.run_id = self.run_id
        if not claim.project_id:
            claim.project_id = self.store.name
        saved = self._repo.save_claim(claim)
        # Return run-scoped relative path
        return f".runs/{saved.run_id}/claims/{saved.claim_id}_v{saved.version}.json"

    def supersede_claim(
        self,
        old_claim_id: str,
        new_claim: Claim,
        subdirectory: str = "research",
    ) -> str:
        _ = subdirectory
        if not new_claim.run_id:
            new_claim.run_id = self.run_id
        saved = self._repo.supersede_claim(old_claim_id, new_claim)
        return f".runs/{saved.run_id}/claims/{saved.claim_id}_v{saved.version}.json"

    def load_claim(self, claim_id: str) -> Claim:
        return self._repo.get_claim(claim_id, run_id=self.run_id)

    def list_claims(
        self,
        *,
        include_superseded: bool = True,
        visibility: ClaimVisibility | None = None,
    ) -> list[Claim]:
        vis = visibility or ClaimVisibility.CURRENT_RUN
        if vis == ClaimVisibility.CURRENT_RUN:
            if not self.run_id:
                return []
            return self._repo.list_claims(
                visibility=vis, run_id=self.run_id, include_superseded=include_superseded
            )
        return self._repo.list_claims(visibility=vis, include_superseded=include_superseded)

    def path_for(self, claim_id: str) -> Path:
        claim = self.load_claim(claim_id)
        rel = f".runs/{claim.run_id}/claims/{claim.claim_id}_v{claim.version}.json"
        return self.store.resolve(rel)
