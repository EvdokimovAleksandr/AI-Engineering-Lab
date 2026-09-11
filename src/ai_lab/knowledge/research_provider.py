"""ResearchProvider interface — stub only; real web backends later."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ai_lab.core.enums import SourceTrustTier
from ai_lab.core.models import ResearchFinding


@runtime_checkable
class ResearchProvider(Protocol):
    """Future real literature/web backend. MVP uses MockResearchProvider."""

    name: str

    async def search(self, query: str, *, limit: int = 10) -> list[ResearchFinding]: ...

    async def fetch(self, source_id: str) -> dict[str, Any]: ...

    async def resolve_source(self, source_id: str) -> dict[str, Any]: ...


class MockResearchProvider:
    """Deterministic stub — sources are always STUB tier."""

    name = "mock_research"

    async def search(self, query: str, *, limit: int = 10) -> list[ResearchFinding]:
        from ai_lab.core.enums import EvidenceKind
        from ai_lab.core.models import Claim, ConfidenceBreakdown, ResearchFinding

        finding = ResearchFinding(
            claim=Claim(
                statement=f"Stub hit for {query!r}",
                kind=EvidenceKind.INFERENCE,
                source="mock://research-stub",
                source_trust=SourceTrustTier.STUB,
                evidence="MockResearchProvider",
                confidence=ConfidenceBreakdown(source_quality=0.2),
            ),
            relevance=0.5,
        )
        return [finding][:limit]

    async def fetch(self, source_id: str) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "trust_tier": SourceTrustTier.STUB.value,
            "resolvable": False,
            "body": "stub",
        }

    async def resolve_source(self, source_id: str) -> dict[str, Any]:
        tier = SourceTrustTier.STUB if str(source_id).startswith("mock://") else SourceTrustTier.SECONDARY
        return {
            "source_id": source_id,
            "trust_tier": tier.value,
            "resolvable": not str(source_id).startswith("mock://"),
            "graph_node_type": "SOURCE",
        }
