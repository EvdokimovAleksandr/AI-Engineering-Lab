"""SearchProvider protocol and implementations (mock, replay, web).

ResearchProvider must not call a search API directly — it goes through this boundary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from ai_lab.knowledge.http_transport import HttpTransport, UrllibTransport
from ai_lab.knowledge.models import SearchHit
from ai_lab.knowledge.research_errors import (
    MalformedSourceError,
    MissingCredentialsError,
    SearchProviderError,
    SearchTimeoutError,
    SourceFetchError,
)

DEFAULT_USER_AGENT = "AI-Engineering-Lab/0.1 (research; untrusted-content)"


@runtime_checkable
class SearchProvider(Protocol):
    """Replaceable search backend. Implementations must not invent URIs."""

    name: str

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        timeout_seconds: float = 15.0,
    ) -> list[SearchHit]: ...


def _require_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        raise ValueError("search query must be non-empty")
    return q


def _hit_from_mapping(raw: dict[str, Any], *, index: int) -> SearchHit:
    uri = str(raw.get("uri") or raw.get("url") or "").strip()
    if not uri:
        raise MalformedSourceError(f"Search hit {index} is missing URI")
    return SearchHit(
        uri=uri,
        title=(str(raw["title"]) if raw.get("title") is not None else None),
        publisher=(str(raw["publisher"]) if raw.get("publisher") is not None else None),
        snippet=(str(raw["snippet"]) if raw.get("snippet") is not None else None),
        metadata=dict(raw.get("metadata") or {}),
    )


class MockSearchProvider:
    """Deterministic in-process search. Default hit is mock:// (STUB)."""

    name = "mock_search"

    def __init__(
        self,
        fixtures: dict[str, list[SearchHit]] | None = None,
        *,
        default_hits: list[SearchHit] | None = None,
        mode: str = "ok",
    ) -> None:
        self.fixtures = fixtures or {}
        self.default_hits = default_hits
        # mode: ok | timeout | error | empty
        self.mode = mode

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        timeout_seconds: float = 15.0,
    ) -> list[SearchHit]:
        _ = timeout_seconds
        q = _require_query(query)
        if self.mode == "timeout" or q.startswith("TIMEOUT:"):
            raise SearchTimeoutError(f"Mock search timeout for query={q!r}")
        if self.mode == "error" or q.startswith("ERROR:"):
            raise SearchProviderError(f"Mock search backend error for query={q!r}")
        if self.mode == "empty" or q.startswith("EMPTY:"):
            return []
        hits = self.fixtures.get(q)
        if hits is None:
            hits = self.default_hits
        if hits is None:
            hits = [
                SearchHit(
                    uri="mock://research-stub",
                    title="Mock research stub",
                    publisher="AI Engineering Lab",
                    snippet=f"Stub hit for {q!r}",
                    metadata={"backend": "mock"},
                )
            ]
        return list(hits)[: max(0, limit)]


class ReplaySearchProvider:
    """Serves canned search fixtures from disk — CI must not hit the network."""

    name = "replay_search"

    def __init__(self, records: dict[str, list[SearchHit]] | None = None, *, root: Path | None = None) -> None:
        self.records = records or {}
        self.root = root
        if root is not None:
            self._load_root(root)

    def _load_root(self, root: Path) -> None:
        if not root.is_dir():
            raise FileNotFoundError(f"Replay search fixture directory not found: {root}")
        for path in sorted(root.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            query = str(data.get("query") or path.stem)
            raw_hits = data.get("hits") or []
            hits = [_hit_from_mapping(h, index=i) for i, h in enumerate(raw_hits)]
            self.records[query] = hits

    def register(self, query: str, hits: list[SearchHit]) -> None:
        self.records[query] = hits

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        timeout_seconds: float = 15.0,
    ) -> list[SearchHit]:
        _ = timeout_seconds
        q = _require_query(query)
        if q not in self.records:
            raise KeyError(f"No replay search fixture for query={q!r}")
        return list(self.records[q])[: max(0, limit)]


class WebSearchProvider:
    """Real web search via a named engine. Credentials come from env, never from git."""

    name = "web_search"

    def __init__(
        self,
        *,
        engine: str = "duckduckgo",
        api_key_env: str = "BRAVE_SEARCH_API_KEY",
        transport: HttpTransport | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.engine = engine.strip().lower()
        self.api_key_env = api_key_env
        self.transport = transport or UrllibTransport()
        self.user_agent = user_agent

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if extra:
            headers.update(extra)
        return headers

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        timeout_seconds: float = 15.0,
    ) -> list[SearchHit]:
        q = _require_query(query)
        if self.engine in {"brave", "brave_search"}:
            return self._search_brave(q, limit=limit, timeout_seconds=timeout_seconds)
        if self.engine in {"duckduckgo", "ddg"}:
            return self._search_duckduckgo(q, limit=limit, timeout_seconds=timeout_seconds)
        raise SearchProviderError(f"Unsupported web search engine: {self.engine!r}")

    def _search_brave(self, query: str, *, limit: int, timeout_seconds: float) -> list[SearchHit]:
        key = (os.environ.get(self.api_key_env) or "").strip()
        if not key:
            raise MissingCredentialsError(
                f"Web search engine 'brave' requires environment variable {self.api_key_env}. "
                "Set the key locally or use research.backend=mock / replay (no credentials)."
            )
        count = max(1, min(int(limit), 20))
        url = (
            "https://api.search.brave.com/res/v1/web/search"
            f"?q={quote_plus(query)}&count={count}"
        )
        try:
            resp = self.transport.get(
                url,
                timeout_seconds=timeout_seconds,
                max_bytes=500_000,
                headers=self._headers(
                    {
                        "X-Subscription-Token": key,
                        "Accept": "application/json",
                    }
                ),
            )
        except SearchTimeoutError:
            raise
        except SourceFetchError as exc:
            raise SearchProviderError(f"Brave search request failed: {exc}") from exc
        try:
            payload = json.loads(resp.body)
        except json.JSONDecodeError as exc:
            raise SearchProviderError("Brave search returned non-JSON response") from exc
        if not isinstance(payload, dict):
            raise SearchProviderError("Brave search returned an invalid JSON payload")
        web = payload.get("web") or {}
        results = web.get("results") if isinstance(web, dict) else None
        if results is None:
            raise SearchProviderError("Brave search response missing web.results")
        if not isinstance(results, list):
            raise SearchProviderError("Brave search web.results is not a list")
        hits: list[SearchHit] = []
        for i, item in enumerate(results):
            if not isinstance(item, dict):
                raise SearchProviderError(f"Brave search hit {i} is not an object")
            uri = str(item.get("url") or "").strip()
            if not uri:
                raise MalformedSourceError(f"Brave search hit {i} is missing URI")
            hits.append(
                SearchHit(
                    uri=uri,
                    title=item.get("title"),
                    publisher=(item.get("meta_url") or {}).get("hostname")
                    if isinstance(item.get("meta_url"), dict)
                    else None,
                    snippet=item.get("description"),
                    metadata={"engine": "brave"},
                )
            )
        return hits[:limit]

    def _search_duckduckgo(self, query: str, *, limit: int, timeout_seconds: float) -> list[SearchHit]:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            resp = self.transport.get(
                url,
                timeout_seconds=timeout_seconds,
                max_bytes=500_000,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html",
                },
            )
        except SearchTimeoutError:
            raise
        except SourceFetchError as exc:
            raise SearchProviderError(f"DuckDuckGo search request failed: {exc}") from exc
        hits = _parse_duckduckgo_html(resp.body)
        return hits[: max(0, limit)]


def _parse_duckduckgo_html(html: str) -> list[SearchHit]:
    """Minimal parser for DDG HTML results. Invalid markup fails the hit, not silently."""
    hits: list[SearchHit] = []
    # result__a is the classic DDG HTML endpoint title link
    for match in re_finditer_result_links(html):
        href, title = match
        uri = _unwrap_ddg_redirect(href)
        if not uri:
            continue
        hits.append(
            SearchHit(
                uri=uri,
                title=title or None,
                publisher=urlparse(uri).netloc or None,
                snippet=None,
                metadata={"engine": "duckduckgo"},
            )
        )
    return hits


def re_finditer_result_links(html: str) -> list[tuple[str, str]]:
    import re

    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    out: list[tuple[str, str]] = []
    for m in pattern.finditer(html):
        href = unquote(m.group(1))
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        out.append((href, title))
    return out


def _unwrap_ddg_redirect(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg") or []
        if uddg:
            return unquote(uddg[0])
    return href
