"""Replay provider unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_lab.core.models import LLMMessage, LLMRequest
from ai_lab.knowledge.models import ReplayRecord
from ai_lab.knowledge.replay import ReplayProvider, ensure_fixture_dirs, save_replay


@pytest.mark.asyncio
async def test_replay_provider_serves_fixture(tmp_path: Path) -> None:
    ensure_fixture_dirs(tmp_path)
    rec = ReplayRecord(
        role="chief",
        request={"prompt": "hi"},
        response={"summary": "from fixture"},
        model="fixture-model",
    )
    save_replay(tmp_path, "chief", rec)
    provider = ReplayProvider({"chief_engineer": rec})
    resp = await provider.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="x")],
            metadata={"agent_role": "chief_engineer"},
        )
    )
    assert resp.provider == "replay"
    assert resp.parsed["summary"] == "from fixture"
