"""Research abstraction — mock/local stub for MVP; real web later."""

from __future__ import annotations

from typing import Any

from ai_lab.core.enums import EvidenceKind, SourceTrustTier
from ai_lab.core.models import Claim, ConfidenceBreakdown, ResearchFinding
from ai_lab.tools.base import ToolSpec


class ResearchTool:
    """
    Abstraction over literature/web search.

    MVP returns structured stub findings so the workflow is exercisable offline.
    Sources are always STUB for mock:// URIs.
    """

    name = "research.query"
    description = "Query research sources (stub in MVP) and return structured findings"

    def as_spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, handler=self.run)

    async def run(self, query: str = "", **_: Any) -> dict[str, Any]:
        if not query or not query.strip():
            raise ValueError("research.query requires non-empty 'query'")

        finding = ResearchFinding(
            claim=Claim(
                statement=(
                    f"Stub research hit for query={query!r}: "
                    "industrial spider-silk programs typically struggle with "
                    "fiber spinning more than with protein expression."
                ),
                kind=EvidenceKind.INFERENCE,
                source="mock://research-stub",
                source_trust=SourceTrustTier.STUB,
                evidence="Deterministic stub — replace with real search backend",
                conditions={"backend": "stub"},
                assumptions=["Stub corpus is illustrative only"],
                falsifiers=["Primary source showing spinning is not the bottleneck"],
                confidence=ConfidenceBreakdown(
                    source_quality=0.2,
                    independent_confirmations=0.0,
                    assumption_quality=0.3,
                ),
            ),
            relevance=0.7,
            potential_contradiction="Some hosts may be expression-limited instead",
        )
        return {
            "query": query,
            "findings": [finding.model_dump(mode="json")],
            # Explicit taint (also set by ToolRegistry)
            "trust_level": "EXTERNAL",
            "data_not_instructions": True,
        }
