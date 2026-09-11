"""CLI helper for `python -m ai_lab research` — existing CLI, not a new program."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ai_lab.config_loader import load_config
from ai_lab.knowledge import KnowledgeService
from ai_lab.knowledge.ingest_research import ingest_research_result
from ai_lab.knowledge.models import ResearchLimits
from ai_lab.knowledge.research_factory import build_research_provider
from ai_lab.memory.project_store import ProjectStore
from ai_lab.tools.research import _public_result


async def run_research_cli(
    *,
    query: str,
    config_path: Path | None,
    provider: str | None,
    research_backend: str | None,
    project: str | None,
    limit: int | None,
) -> int:
    config = load_config(config_path)
    if provider:
        from ai_lab.llm.config import apply_provider_override

        config = apply_provider_override(config, provider)
    if research_backend:
        research_cfg = dict(config.research or {})
        research_cfg["backend"] = research_backend
        config.research = research_cfg

    repo_root = Path(__file__).resolve().parents[2]
    research_provider = build_research_provider(config, repo_root=repo_root)
    limits = None
    if limit is not None:
        raw = dict((config.research or {}).get("limits") or {})
        limits = ResearchLimits(
            max_queries=int(raw.get("max_queries", 8)),
            max_sources=int(limit),
            max_content_bytes=int(raw.get("max_content_bytes", 200_000)),
            timeout_seconds=float(raw.get("timeout_seconds", 15.0)),
            max_evidence_per_source=int(raw.get("max_evidence_per_source", 3)),
        )

    result = await research_provider.research(query, limits=limits)
    ingest = None
    if project:
        path = Path(project)
        if path.exists() and path.is_dir():
            store = ProjectStore(path)
        else:
            store = ProjectStore.open(repo_root / "projects", Path(project).name)
        store.ensure_layout()
        run_id = f"run_{uuid4().hex[:12]}"
        knowledge = KnowledgeService(store, run_id=run_id)
        ingest = ingest_research_result(knowledge, result, run_id=run_id, created_by="research")

    payload = _public_result(result)
    payload["findings"] = [f.model_dump(mode="json") for f in result.findings]
    if ingest is not None:
        payload["ingest"] = ingest
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0
