"""Fetch source bodies, fingerprint them, and attach trust metadata.

Retrieved content is untrusted data. Classification uses URI/host only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ai_lab.knowledge.evidence_extract import extract_html_title
from ai_lab.knowledge.http_transport import HttpTransport, UrllibTransport
from ai_lab.knowledge.models import SourceRecord
from ai_lab.knowledge.research_errors import MalformedSourceError, SourceFetchError
from ai_lab.knowledge.search import DEFAULT_USER_AGENT, SearchHit
from ai_lab.knowledge.source_identity import (
    allowed_fetch_scheme,
    canonical_uri,
    classify_source,
    content_fingerprint,
    source_id_for,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceResolver:
    """Turn a SearchHit into a SourceRecord with content hash and trust tier."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        extra_primary_hosts: list[str] | None = None,
        mock_bodies: dict[str, str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.transport = transport or UrllibTransport()
        self.extra_primary_hosts = list(extra_primary_hosts or [])
        # In-process bodies for mock:// URIs (and tests) — never invented at query time.
        self.mock_bodies = mock_bodies or {}
        self.user_agent = user_agent

    def resolve_hit(
        self,
        hit: SearchHit,
        *,
        timeout_seconds: float,
        max_content_bytes: int,
        retrieved_at: datetime | None = None,
    ) -> SourceRecord:
        uri = (hit.uri or "").strip()
        if not uri:
            raise MalformedSourceError("Search hit is missing URI")
        if not allowed_fetch_scheme(uri):
            raise SourceFetchError(f"Refusing to fetch unsupported URI scheme: {uri!r}")

        canon = canonical_uri(uri)
        source_id = source_id_for(canon)
        kind, tier = classify_source(
            canon, title=hit.title, extra_primary_hosts=self.extra_primary_hosts
        )
        ts = retrieved_at or _utc_now()

        body, truncated, final_url = self._retrieve(
            canon, timeout_seconds=timeout_seconds, max_content_bytes=max_content_bytes
        )
        title = hit.title
        if not title:
            extracted = extract_html_title(body)
            if extracted:
                # Title from the page is untrusted display data, not trust metadata.
                title = extracted
        fingerprint = content_fingerprint(body)
        metadata: dict[str, Any] = {
            "canonical_uri": canon,
            "final_url": final_url,
            "data_not_instructions": True,
            "trust_level": "EXTERNAL",
            "search_snippet": hit.snippet,
        }
        if hit.metadata:
            metadata["search_metadata"] = dict(hit.metadata)
        return SourceRecord(
            source_id=source_id,
            uri=canon,
            title=title,
            publisher=hit.publisher or urlparse(canon).netloc or None,
            retrieved_at=ts,
            source_type=kind,
            trust_tier=tier,
            content_hash=fingerprint,
            metadata=metadata,
            truncated=truncated,
            content=body,
        )

    def _retrieve(
        self,
        uri: str,
        *,
        timeout_seconds: float,
        max_content_bytes: int,
    ) -> tuple[str, bool, str]:
        if uri.startswith("mock://") or uri in self.mock_bodies:
            body = self.mock_bodies.get(uri)
            if body is None:
                body = self.mock_bodies.get("mock://research-stub", f"Mock body for {uri}")
            encoded = body.encode("utf-8")
            truncated = len(encoded) > max_content_bytes
            if truncated:
                body = encoded[:max_content_bytes].decode("utf-8", errors="replace")
            return body, truncated, uri

        resp = self.transport.get(
            uri,
            timeout_seconds=timeout_seconds,
            max_bytes=max_content_bytes,
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,text/plain"},
        )
        if resp.status >= 400:
            raise SourceFetchError(f"HTTP {resp.status} fetching {uri}")
        return resp.body, resp.truncated, resp.url
