"""Deterministic source identity, fingerprint, and trust-tier classification.

Trust uses existing SourceTrustTier only. SourceKind is descriptive metadata,
computed from URI/host heuristics — never from retrieved body text (untrusted).
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from ai_lab.core.enums import SourceKind, SourceTrustTier
from ai_lab.knowledge.hashing import sha256_text
from ai_lab.knowledge.research_errors import MalformedSourceError

# Hosts treated as PRIMARY scientific/government/patent literature.
_PRIMARY_HOSTS = frozenset(
    {
        "arxiv.org",
        "doi.org",
        "dx.doi.org",
        "pubmed.ncbi.nlm.nih.gov",
        "ncbi.nlm.nih.gov",
        "nature.com",
        "science.org",
        "sciencedirect.com",
        "springer.com",
        "link.springer.com",
        "ieee.org",
        "ieeexplore.ieee.org",
        "acm.org",
        "dl.acm.org",
        "wiley.com",
        "onlinelibrary.wiley.com",
        "pnas.org",
        "cell.com",
        "thelancet.com",
        "nejm.org",
        "rsc.org",
        "pubs.acs.org",
        "acs.org",
        "aps.org",
        "iopscience.iop.org",
        "uspto.gov",
        "patents.google.com",
        "epo.org",
        "worldwide.espacenet.com",
        "wipo.int",
        "patentscope.wipo.int",
        "nist.gov",
        "nasa.gov",
        "nih.gov",
        "nsf.gov",
        "europa.eu",
        "who.int",
    }
)

_ENCYCLOPEDIA_HOSTS = frozenset(
    {
        "wikipedia.org",
        "en.wikipedia.org",
        "britannica.com",
    }
)

_BLOG_HOSTS = frozenset(
    {
        "medium.com",
        "substack.com",
        "wordpress.com",
        "blogspot.com",
        "blogger.com",
        "tumblr.com",
        "dev.to",
        "hashnode.dev",
    }
)

_REVIEW_HINTS = ("review", "survey", "meta-analysis", "overview")


def canonical_uri(uri: str) -> str:
    """Normalize a URI for identity. Fragment is not part of source identity."""
    if not uri or not str(uri).strip():
        raise MalformedSourceError("Source URI is missing")
    raw = str(uri).strip()
    parsed = urlparse(raw)
    if not parsed.scheme:
        raise MalformedSourceError(f"Source URI has no scheme: {uri!r}")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    # Drop fragment; keep query (may be required for some publishers).
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def source_id_for(uri: str) -> str:
    """Stable source_id derived from canonical URI (not from title or body)."""
    return "src_" + sha256_text(canonical_uri(uri))[:16]


def content_fingerprint(content: str) -> str:
    """SHA-256 of retrieved content — changes if the source body changes."""
    return sha256_text(content)


def evidence_id_for(source_id: str, text: str, *, paragraph: int | None) -> str:
    """Stable evidence id: source + locator + text."""
    loc = "" if paragraph is None else str(paragraph)
    return "evd_" + sha256_text(f"{source_id}|{loc}|{text}")[:16]


def _host(uri: str) -> str:
    return urlparse(canonical_uri(uri)).netloc.lower()


def _host_matches(host: str, allowed: frozenset[str] | set[str]) -> bool:
    for item in allowed:
        if host == item or host.endswith("." + item):
            return True
    return False


def classify_source(
    uri: str,
    *,
    title: str | None = None,
    extra_primary_hosts: list[str] | None = None,
) -> tuple[SourceKind, SourceTrustTier]:
    """Classify from URI (and optional extra primary hosts). Body text is ignored.

    Title is used only for review-article hints, never to raise trust above
    what the host justifies.
    """
    raw = str(uri).strip()
    if raw.startswith("mock://"):
        return SourceKind.STUB, SourceTrustTier.STUB

    host = _host(raw)
    extra = {h.lower().lstrip(".") for h in (extra_primary_hosts or []) if h}

    if _host_matches(host, _ENCYCLOPEDIA_HOSTS) or host.endswith(".wikipedia.org"):
        return SourceKind.ENCYCLOPEDIA, SourceTrustTier.SECONDARY
    if _host_matches(host, _BLOG_HOSTS):
        return SourceKind.BLOG, SourceTrustTier.SECONDARY

    if host.endswith(".gov") or host.endswith(".mil") or _host_matches(host, frozenset({"europa.eu"})):
        if _host_matches(host, frozenset({"uspto.gov"})) or "patent" in host:
            return SourceKind.PATENT, SourceTrustTier.PRIMARY
        return SourceKind.GOVERNMENT, SourceTrustTier.PRIMARY
    if host.endswith(".edu") or host.endswith(".ac.uk"):
        return SourceKind.UNIVERSITY, SourceTrustTier.PRIMARY

    if _host_matches(
        host,
        frozenset({"patents.google.com", "epo.org", "wipo.int", "worldwide.espacenet.com", "patentscope.wipo.int"}),
    ):
        return SourceKind.PATENT, SourceTrustTier.PRIMARY

    # Extra hosts from config are treated as manufacturer/official docs, not a quality score.
    if extra and _host_matches(host, extra):
        return SourceKind.MANUFACTURER_DOCS, SourceTrustTier.PRIMARY

    if _host_matches(host, _PRIMARY_HOSTS):
        title_l = (title or "").lower()
        if any(hint in title_l for hint in _REVIEW_HINTS):
            # Scientific venue remains PRIMARY; kind records that it is a review.
            return SourceKind.REVIEW_ARTICLE, SourceTrustTier.PRIMARY
        return SourceKind.SCIENTIFIC_ARTICLE, SourceTrustTier.PRIMARY

    return SourceKind.UNKNOWN, SourceTrustTier.SECONDARY


def allowed_fetch_scheme(uri: str) -> bool:
    scheme = urlparse(uri).scheme.lower()
    if uri.startswith("mock://"):
        return True
    return scheme in {"http", "https"}
