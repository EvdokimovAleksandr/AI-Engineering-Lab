"""Knowledge facade — wiring repositories for LabRuntime / agents."""

from __future__ import annotations

from ai_lab.core.enums import AgentRole, ClaimVisibility
from ai_lab.core.models import Claim
from ai_lab.knowledge.graph import JsonEvidenceRepository
from ai_lab.knowledge.json_knowledge import JsonKnowledgeRepository
from ai_lab.knowledge.migration import migrate_project_knowledge, needs_migration
from ai_lab.knowledge.queries import EvidenceQueryService
from ai_lab.knowledge.runs import JsonRunRepository
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# Default visibility policy per role — nobody gets "everything"
ROLE_VISIBILITY: dict[AgentRole, ClaimVisibility] = {
    AgentRole.CHIEF_ENGINEER: ClaimVisibility.APPROVED_KNOWLEDGE,  # plus CURRENT_RUN via explicit merge
    AgentRole.RESEARCH: ClaimVisibility.PROJECT_HISTORY,
    AgentRole.THEORIST: ClaimVisibility.CURRENT_RUN,
    AgentRole.SIMULATION: ClaimVisibility.CURRENT_RUN,
    AgentRole.VERIFICATION: ClaimVisibility.CURRENT_RUN,
    AgentRole.RED_TEAM: ClaimVisibility.CURRENT_RUN,
}


class KnowledgeService:
    """Single entry for run-scoped knowledge + graph + queries."""

    def __init__(self, store: ProjectStore, *, run_id: str, auto_migrate: bool = True) -> None:
        self.store = store
        self.run_id = run_id
        if auto_migrate and needs_migration(store):
            logger.warning("Knowledge schema migration required — running non-destructive migrate")
            migrate_project_knowledge(store)
        self.claims = JsonKnowledgeRepository(store)
        self.graph = JsonEvidenceRepository(store)
        self.runs = JsonRunRepository(store)
        self.queries = EvidenceQueryService(store, self.graph, self.claims)

    def visibility_for(self, role: AgentRole) -> ClaimVisibility:
        return ROLE_VISIBILITY.get(role, ClaimVisibility.CURRENT_RUN)

    def list_for_agent(self, role: AgentRole) -> list[Claim]:
        """Apply visibility policy. Chief gets CURRENT_RUN ∪ APPROVED_KNOWLEDGE."""
        vis = self.visibility_for(role)
        if role == AgentRole.CHIEF_ENGINEER:
            current = self.claims.list_claims(
                visibility=ClaimVisibility.CURRENT_RUN, run_id=self.run_id
            )
            approved = self.claims.list_claims(visibility=ClaimVisibility.APPROVED_KNOWLEDGE)
            by_id = {c.claim_id: c for c in approved}
            for c in current:
                by_id[c.claim_id] = c
            return list(by_id.values())
        if vis == ClaimVisibility.CURRENT_RUN:
            return self.claims.list_claims(visibility=vis, run_id=self.run_id)
        if vis == ClaimVisibility.PROJECT_HISTORY:
            return self.claims.list_claims(visibility=vis, include_superseded=True)
        return self.claims.list_claims(visibility=vis)

    def save_claim(self, claim: Claim, *, legacy_migrate: bool = False) -> Claim:
        # PR-C: no auto-fill — caller must stamp full ExecutionContext before save.
        return self.claims.save_claim(claim, legacy_migrate=legacy_migrate)
