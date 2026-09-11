"""Pluggable HTTP GET used by web search and source fetch.

Tests inject a fake transport so CI never depends on the network.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_lab.knowledge.research_errors import SearchTimeoutError, SourceFetchError


@dataclass
class HttpResponse:
    url: str
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse: ...


class UrllibTransport:
    """stdlib HTTP GET with timeout and hard body-size cap."""

    def get(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        req = Request(url, headers=headers or {}, method="GET")
        try:
            with urlopen(req, timeout=timeout_seconds) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                final_url = str(resp.geturl() or url)
                header_map = {k.lower(): v for k, v in resp.headers.items()}
                raw = resp.read(max_bytes + 1)
        except TimeoutError as exc:
            raise SearchTimeoutError(f"HTTP timeout after {timeout_seconds}s for {url}") from exc
        except socket.timeout as exc:
            raise SearchTimeoutError(f"HTTP timeout after {timeout_seconds}s for {url}") from exc
        except ssl.SSLError as exc:
            raise SourceFetchError(f"TLS error fetching {url}: {exc}") from exc
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read(max_bytes).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise SourceFetchError(f"HTTP {exc.code} fetching {url}: {body[:200]}") from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise SourceFetchError(f"URL error fetching {url}: {reason}") from exc

        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        charset = "utf-8"
        ctype = header_map.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return HttpResponse(
            url=final_url,
            status=status,
            body=text,
            headers=header_map,
            truncated=truncated,
        )


class ScriptedTransport:
    """Deterministic transport for tests: url → HttpResponse or exception."""

    def __init__(
        self,
        responses: dict[str, HttpResponse] | None = None,
        *,
        errors: dict[str, Exception] | None = None,
        default: HttpResponse | Exception | None = None,
    ) -> None:
        self.responses = responses or {}
        self.errors = errors or {}
        self.default = default

    def get(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        _ = timeout_seconds, headers
        if url in self.errors:
            raise self.errors[url]
        if url in self.responses:
            resp = self.responses[url]
            if len(resp.body.encode("utf-8")) > max_bytes:
                encoded = resp.body.encode("utf-8")[:max_bytes]
                return HttpResponse(
                    url=resp.url,
                    status=resp.status,
                    body=encoded.decode("utf-8", errors="replace"),
                    headers=resp.headers,
                    truncated=True,
                )
            return resp
        if isinstance(self.default, Exception):
            raise self.default
        if self.default is not None:
            return self.default
        raise SourceFetchError(f"No scripted HTTP response for {url}")
