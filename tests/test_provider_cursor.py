"""Provider smoke CLI, error taxonomy, and Cursor wiring guards."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ai_lab.cli import main
from ai_lab.core.models import LabConfig, LLMMessage, LLMRequest
from ai_lab.llm.cursor_sdk import CursorSDKProvider, _extract_usage, _map_cursor_agent_error
from ai_lab.llm.errors import (
    AuthenticationError,
    MalformedResponse,
    ProviderUnavailable,
    RateLimitError,
    Timeout,
)
from ai_lab.llm.registry import create_provider


def test_agents_do_not_import_cursor_sdk() -> None:
    """Vendor SDK must stay behind LLMProvider — agents stay vendor-neutral."""
    agents_root = Path(__file__).resolve().parents[1] / "src" / "ai_lab" / "agents"
    offenders: list[str] = []
    for path in agents_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "cursor_sdk" in text or "from cursor_sdk" in text or "import cursor_sdk" in text:
            offenders.append(str(path))
    assert offenders == []


def test_missing_cursor_key_is_authentication_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(AuthenticationError, match="CURSOR_API_KEY"):
        CursorSDKProvider(api_key=None)


def test_map_cursor_errors() -> None:
    class Fake:
        def __init__(self, message: str, *, is_retryable: bool = False, retry_after=None):
            self.message = message
            self.is_retryable = is_retryable
            self.retry_after = retry_after

        def __str__(self) -> str:
            return self.message

    assert isinstance(_map_cursor_agent_error(Fake("401 unauthorized")), AuthenticationError)
    assert isinstance(_map_cursor_agent_error(Fake("429 rate limit", retry_after=1.5)), RateLimitError)
    assert isinstance(_map_cursor_agent_error(Fake("request timed out")), Timeout)
    assert isinstance(
        _map_cursor_agent_error(Fake("backend blip", is_retryable=True)),
        ProviderUnavailable,
    )
    mapped = _map_cursor_agent_error(Fake("429 rate limit", retry_after=2))
    assert isinstance(mapped, RateLimitError)
    assert mapped.retry_after == 2.0
    assert mapped.retryable is True
    assert AuthenticationError("x").retryable is False


def test_extract_usage_unknown_when_absent() -> None:
    class R:
        status = "ok"
        result = "{}"
        id = "r1"

    assert _extract_usage(R()) == {}


def test_extract_usage_from_dict() -> None:
    class R:
        usage = {"input_tokens": 3, "output_tokens": 7}

    usage = _extract_usage(R())
    assert usage["input_tokens"] == 3
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 10


@pytest.mark.asyncio
async def test_cursor_malformed_json_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    provider = CursorSDKProvider(api_key="test-key-not-real", reasoning_only=True)

    async def _fake_prompt(prompt: str, model: str):
        class R:
            status = "finished"
            result = "not-json-at-all"
            id = "x"
            usage = None
            duration_ms = 1

        return R()

    monkeypatch.setattr(provider, "_prompt_once", _fake_prompt)
    with pytest.raises(MalformedResponse):
        await provider.complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="hi")],
                metadata={"agent_role": "chief_engineer"},
            )
        )


@pytest.mark.asyncio
async def test_cursor_invalid_latex_escape_is_repaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulation-style Cursor JSON with raw \\sigma must parse, not abort the run."""
    pytest.importorskip("cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key-not-real")
    provider = CursorSDKProvider(api_key="test-key-not-real", reasoning_only=True)

    async def _fake_prompt(prompt: str, model: str):
        class R:
            status = "finished"
            result = '{"expression": "\\sigma = 4F/(\\pi * d**2)", "ok": true}'
            id = "x"
            usage = None
            duration_ms = 1

        return R()

    monkeypatch.setattr(provider, "_prompt_once", _fake_prompt)
    response = await provider.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="hi")],
            metadata={"agent_role": "simulation"},
        )
    )
    assert response.parsed is not None
    assert response.parsed["ok"] is True
    assert "sigma" in response.parsed["expression"]


def test_extract_usage_from_token_usage_object() -> None:
    class TokenUsage:
        input_tokens = 4
        output_tokens = 6
        total_tokens = 10
        cache_read_tokens = 0
        cache_write_tokens = 0
        reasoning_tokens = None

    class R:
        usage = TokenUsage()

    usage = _extract_usage(R())
    assert usage["input_tokens"] == 4
    assert usage["output_tokens"] == 6
    assert usage["total_tokens"] == 10


def test_provider_test_cli_missing_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # main() calls load_dotenv(); force the env var empty after that would reload .env.
    monkeypatch.setenv("CURSOR_API_KEY", "")
    monkeypatch.setattr("ai_lab.cli_provider._key_present", lambda: False)
    code = main(["provider", "test", "--provider", "cursor_sdk"])
    captured = capsys.readouterr()
    assert code == 1
    assert "CURSOR_API_KEY" in captured.err
    assert "cursor.com/dashboard/integrations" in captured.err
    # No full Python traceback for the expected missing-key path
    assert "Traceback" not in captured.err


def test_provider_test_cli_mock() -> None:
    code = main(["provider", "test", "--provider", "mock"])
    assert code == 0


def test_create_provider_mock_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    provider = create_provider("mock", LabConfig(provider="mock"))
    assert provider.name == "mock"


def test_env_example_documents_cursor_key() -> None:
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "CURSOR_API_KEY=" in text
    assert "git check-ignore" in text


def _cursor_sdk_importable() -> bool:
    try:
        import cursor_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def _cursor_live_ready() -> bool:
    import os

    from dotenv import load_dotenv

    load_dotenv()
    if not (os.environ.get("CURSOR_API_KEY") or "").strip():
        return False
    return _cursor_sdk_importable()


@pytest.mark.cursor
@pytest.mark.skipif(not _cursor_live_ready(), reason="CURSOR_API_KEY or cursor-sdk missing")
def test_live_cursor_provider_smoke_cli() -> None:
    """Optional contract: real Cursor one-shot. Not run in default CI."""
    code = main(["provider", "test", "--provider", "cursor_sdk"])
    assert code == 0
