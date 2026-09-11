"""Build ResearchProvider from LabConfig.research (existing YAML dict pattern)."""

from __future__ import annotations

from pathlib import Path
import json

from ai_lab.core.models import LabConfig
from ai_lab.knowledge.http_transport import HttpTransport
from ai_lab.knowledge.models import ResearchLimits
from ai_lab.knowledge.research_errors import SearchProviderError
from ai_lab.knowledge.research_provider import MockResearchProvider, PipelineResearchProvider, ResearchProvider
from ai_lab.knowledge.search import ReplaySearchProvider, WebSearchProvider
from ai_lab.knowledge.source_resolver import SourceResolver


def _limits_from_config(research_cfg: dict) -> ResearchLimits:
    raw = dict(research_cfg.get("limits") or {})
    return ResearchLimits(
        max_queries=int(raw.get("max_queries", 8)),
        max_sources=int(raw.get("max_sources", 12)),
        max_content_bytes=int(raw.get("max_content_bytes", 200_000)),
        timeout_seconds=float(raw.get("timeout_seconds", 15.0)),
        max_evidence_per_source=int(raw.get("max_evidence_per_source", 3)),
    )


def _fixtures_root(repo_root: Path | None) -> Path | None:
    if repo_root is None:
        return None
    path = repo_root / "fixtures" / "research"
    return path if path.is_dir() else None


def build_research_provider(
    config: LabConfig,
    *,
    repo_root: Path | None = None,
    transport: HttpTransport | None = None,
) -> ResearchProvider:
    """Select mock | replay | web. Missing web credentials fail loud (no mock fallback)."""
    research_cfg = dict(config.research or {})
    backend = str(research_cfg.get("backend") or "mock").strip().lower()
    limits = _limits_from_config(research_cfg)
    extra_hosts = [str(h) for h in (research_cfg.get("primary_hosts") or [])]
    resolver = SourceResolver(transport=transport, extra_primary_hosts=extra_hosts)

    if backend in {"mock", "stub"}:
        return MockResearchProvider(limits=limits, resolver=resolver)

    if backend == "replay":
        root = _fixtures_root(repo_root)
        replay_dir = research_cfg.get("replay_dir")
        if replay_dir:
            root = Path(replay_dir)
        if root is None:
            raise SearchProviderError(
                "research.backend=replay requires fixtures/research or research.replay_dir"
            )
        search = ReplaySearchProvider(root=root)
        bodies = {}
        for path in root.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            for src in data.get("sources") or []:
                uri = src.get("uri")
                body = src.get("content")
                if uri and body is not None:
                    bodies[str(uri)] = str(body)
        if bodies:
            resolver.mock_bodies.update(bodies)
        return PipelineResearchProvider(
            search, resolver, limits=limits, provider_name="replay_research"
        )

    if backend == "web":
        web_cfg = dict(research_cfg.get("web") or {})
        engine = str(web_cfg.get("engine") or "duckduckgo")
        api_key_env = str(web_cfg.get("api_key_env") or "BRAVE_SEARCH_API_KEY")
        search = WebSearchProvider(
            engine=engine,
            api_key_env=api_key_env,
            transport=transport,
        )
        return PipelineResearchProvider(
            search, resolver, limits=limits, provider_name="web_research"
        )

    raise SearchProviderError(
        f"Unknown research.backend={backend!r}; expected mock, replay, or web"
    )
