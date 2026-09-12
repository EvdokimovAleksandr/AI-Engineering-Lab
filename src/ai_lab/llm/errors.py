"""Typed LLM provider failures — fail loud, no silent vendor fallback.

Agent code should catch these (or let them bubble). Never branch on vendor
strings like ``if provider == "cursor"`` inside agents.
"""

from __future__ import annotations


class LLMProviderError(RuntimeError):
    """Base error for any LLMProvider backend failure."""

    retryable: bool = False


class AuthenticationError(LLMProviderError):
    """Missing or invalid credentials. Never retry."""

    retryable = False


class RateLimitError(LLMProviderError):
    """Provider rate limit / quota. May be retryable after delay."""

    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(LLMProviderError):
    """Backend package missing, network down, or service unavailable."""

    retryable = True


class Timeout(LLMProviderError):
    """Provider call exceeded its time budget."""

    retryable = True


class MalformedResponse(LLMProviderError):
    """Response body is not usable (empty, non-JSON, truncated)."""

    retryable = False


class SchemaError(LLMProviderError):
    """Response parsed but does not satisfy the expected structured schema."""

    retryable = False
