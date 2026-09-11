"""Record/replay foundation for LLM interactions (no live API required)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_lab.knowledge.models import ReplayRecord

# Default fixture tree under repo
FIXTURE_ROLES = ("chief", "research", "theorist", "verification", "red_team", "simulation")


def fixtures_root(repo_root: Path) -> Path:
    return repo_root / "fixtures" / "llm"


def ensure_fixture_dirs(repo_root: Path) -> Path:
    root = fixtures_root(repo_root)
    for role in FIXTURE_ROLES:
        (root / role).mkdir(parents=True, exist_ok=True)
    return root


def save_replay(repo_root: Path, role: str, record: ReplayRecord) -> Path:
    ensure_fixture_dirs(repo_root)
    safe_role = role if role in FIXTURE_ROLES else "research"
    path = fixtures_root(repo_root) / safe_role / f"{record.fixture_id}.json"
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_replay(path: Path) -> ReplayRecord:
    return ReplayRecord.model_validate_json(path.read_text(encoding="utf-8"))


def load_replay_by_id(repo_root: Path, role: str, fixture_id: str) -> ReplayRecord:
    path = fixtures_root(repo_root) / role / f"{fixture_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Replay fixture not found: {path}")
    return load_replay(path)


class ReplayProvider:
    """
    LLMProvider-compatible stub that serves canned fixtures.

    Used in tests — not a silent fallback for production providers.
    """

    name = "replay"

    def __init__(self, records: dict[str, ReplayRecord] | None = None) -> None:
        self.records = records or {}

    def register(self, role: str, record: ReplayRecord) -> None:
        self.records[role] = record

    async def complete(self, request) -> Any:
        from ai_lab.core.models import LLMResponse

        role = (request.metadata or {}).get("agent_role", "chief_engineer")
        # Map agent_role to fixture bucket
        bucket = role.replace("_engineer", "").replace("chief", "chief")
        if role == "chief_engineer":
            bucket = "chief"
        rec = self.records.get(role) or self.records.get(bucket)
        if rec is None:
            raise KeyError(f"No replay fixture registered for role={role}")
        # Same routing config must replay the same fixture; mismatches fail loud.
        if rec.model and rec.model != "unknown" and request.model:
            if rec.model != request.model:
                raise KeyError(
                    f"Replay fixture model mismatch for role={role}: "
                    f"fixture={rec.model!r} request={request.model!r}"
                )
        if rec.routed_model:
            expected_model = rec.routed_model.get("model")
            if expected_model and request.model and expected_model != request.model:
                raise KeyError(
                    f"Replay routing model mismatch for role={role}: "
                    f"fixture={expected_model!r} request={request.model!r}"
                )
        routing = {}
        if rec.routing_policy_version or rec.routed_model:
            routing = {
                "model": rec.model,
                "provider": rec.provider,
                "routing_policy_version": rec.routing_policy_version,
                "routed_model": rec.routed_model,
            }
        return LLMResponse(
            content=json.dumps(rec.response),
            parsed=rec.response if isinstance(rec.response, dict) else None,
            model=rec.model,
            provider=self.name,
            run_id=rec.fixture_id,
            routing_policy_version=rec.routing_policy_version,
            routing=routing,
        )
