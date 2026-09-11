"""Retry policy extension point.

The router does not retry or reroute. Automatic retries without a frozen
ModelConfig would let independent reviewers collapse onto one model.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RetryPolicy(BaseModel):
    """Declared, not executed.

    max_attempts=1 means no retry. reroute_on_failure must stay false:
    a retry must reuse the same validated ModelConfig (provenance-preserving).
    """

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=8)
    reroute_on_failure: bool = False

    def validate_invariants(self) -> None:
        if self.reroute_on_failure:
            raise ValueError(
                "RetryPolicy.reroute_on_failure is forbidden: "
                "retries must keep the original ModelConfig"
            )
