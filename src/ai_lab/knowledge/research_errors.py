"""Typed failures for the research pipeline — fail loud, no silent fallback."""


class ResearchError(RuntimeError):
    """Base error for research backend / provenance pipeline failures."""


class SearchTimeoutError(ResearchError):
    """Search backend exceeded its timeout."""


class SearchProviderError(ResearchError):
    """Search backend returned an invalid or failed response."""


class MissingCredentialsError(ResearchError):
    """Required API credentials are absent — never fall back to mock silently."""


class ResearchLimitExceeded(ResearchError):
    """Research operation exceeded configured query/source/content caps."""


class MalformedSourceError(ResearchError):
    """A search hit or retrieved document is missing required identity fields."""


class SourceFetchError(ResearchError):
    """Fetching source content failed (network, HTTP status, or scheme)."""
