"""Human-in-the-loop gates — sparse, only for disputed/expensive/dangerous cases."""

from __future__ import annotations

from dataclasses import dataclass

from ai_lab.core.models import HitlRequest
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class HitlDecision:
    approved: bool
    choice: str | None = None
    note: str = ""
    # Free-form field answers for scope clarification (temperatures, duration, …).
    answers: dict[str, str] | None = None


class HitlGate:
    """
    Default implementation: non-interactive pause (AWAITING_HUMAN).

    Interactive CLI can subclass or inject a callback later.
    pending_decision is consumed once on resume (UI / --resume with a user answer).
    """

    def __init__(
        self,
        *,
        auto_approve: bool = False,
        pending_decision: HitlDecision | None = None,
    ) -> None:
        # Tests may auto-approve; production default must stop for the human owner.
        self.auto_approve = auto_approve
        self.pending_decision = pending_decision

    def request(self, req: HitlRequest) -> HitlDecision:
        logger.info("HITL requested: %s options=%s", req.reason, req.options)
        if self.pending_decision is not None:
            decision = self.pending_decision
            self.pending_decision = None
            return decision
        if self.auto_approve:
            return HitlDecision(
                approved=True,
                choice=(req.options[0] if req.options else "continue"),
                note="auto",
            )
        return HitlDecision(approved=False, note="awaiting_human")
