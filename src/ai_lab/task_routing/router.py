"""TaskRouter: classify → policy validate → RoutingDecision.

Distinct from LLMRouter (role → ModelConfig). This selects workflow profile.
"""

from __future__ import annotations

from typing import Any

from ai_lab.observability.logger import get_logger
from ai_lab.task_routing.classifier import TaskClassifier, create_classifier
from ai_lab.task_routing.models import RoutingDecision
from ai_lab.task_routing.policy import (
    TaskRoutingPolicy,
    validate_task_routing_policy,
)

logger = get_logger(__name__)


class TaskRouter:
    """Extension point before TaskGraph planning. Does not solve the problem."""

    def __init__(
        self,
        policy: TaskRoutingPolicy,
        *,
        classifier: TaskClassifier | None = None,
    ) -> None:
        errors = validate_task_routing_policy(policy)
        if errors:
            logger.error("Invalid task routing policy: %s", errors)
            raise ValueError(f"Invalid task routing policy: {errors}")
        self.policy = policy
        self.classifier = classifier or create_classifier(policy.classifier)

    def route(self, context: Any) -> RoutingDecision:
        """Propose classification, then apply deterministic policy floors."""
        problem_text = str(getattr(context, "problem_text", "") or "")
        project_id = str(getattr(context, "project_id", "") or "")
        if not problem_text.strip() and not project_id.strip():
            logger.error("TaskRouter called with empty problem and project_id")
            raise ValueError("TaskRouter requires non-empty problem_text or project_id")

        proposal = self.classifier.classify(context)
        decision = self.policy.apply(proposal)
        logger.info(
            "Task routed: proposed=%s final=%s escalated=%s risk=%s uncertainty=%s",
            proposal.recommended_workflow.value,
            decision.final_workflow.value,
            decision.policy_escalated,
            proposal.risk,
            proposal.uncertainty,
        )
        if decision.policy_escalated:
            logger.info(
                "Policy escalated routing above classifier proposal: %s",
                decision.notes + [o.reason for o in decision.policy_overrides],
            )
        return decision
