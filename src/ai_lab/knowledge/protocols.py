"""Repository protocols — agents must not know physical storage layout."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai_lab.core.enums import ClaimVisibility
from ai_lab.core.models import Claim, DecisionRecord, GraphEdge, GraphNode, RunManifest
from ai_lab.knowledge.models import (
    ApprovedKnowledgeEntry,
    ConclusionRecord,
    ConflictRecord,
)


@runtime_checkable
class KnowledgeRepository(Protocol):
    def save_claim(self, claim: Claim) -> Claim: ...

    def get_claim(self, claim_id: str, *, run_id: str | None = None) -> Claim: ...

    def list_claims(
        self,
        *,
        visibility: ClaimVisibility,
        run_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[Claim]: ...

    def supersede_claim(self, old_claim_id: str, new_claim: Claim) -> Claim: ...

    def set_lifecycle(self, claim_id: str, lifecycle: str, *, run_id: str | None = None) -> Claim: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    def add_node(self, node: GraphNode) -> GraphNode: ...

    def add_edge(self, edge: GraphEdge) -> GraphEdge: ...

    def get_node(self, node_id: str) -> GraphNode: ...

    def list_nodes(self, *, run_id: str | None = None) -> list[GraphNode]: ...

    def list_edges(self, *, run_id: str | None = None) -> list[GraphEdge]: ...

    def validate_integrity(self) -> list[str]: ...


@runtime_checkable
class RunRepository(Protocol):
    def list_run_ids(self) -> list[str]: ...

    def load_manifest(self, run_id: str) -> RunManifest: ...

    def freeze_run(self, run_id: str) -> RunManifest: ...

    def verify_run_immutable(self, run_id: str) -> None: ...
