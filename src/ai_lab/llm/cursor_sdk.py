"""Cursor SDK reasoning backend — uses existing Cursor account (CURSOR_API_KEY)."""

from __future__ import annotations

import os
from pathlib import Path

from ai_lab.core.models import LLMRequest, LLMResponse
from ai_lab.llm.base import extract_json_object
from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


class CursorSDKProvider:
    """
    One-shot Agent.prompt as structured completion.

    Lab tools remain the only sanctioned compute/filesystem path.
    Prompts must request JSON-only answers.
    """

    name = "cursor_sdk"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = "composer-2.5",
        cwd: Path | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("CURSOR_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "CursorSDKProvider requires CURSOR_API_KEY "
                "(see Cursor Dashboard → Integrations). No silent fallback."
            )
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "cursor-sdk is not installed. Install with: pip install 'ai-engineering-lab[cursor]'"
            ) from exc
        self._Agent = Agent
        self._AgentOptions = AgentOptions
        self._LocalAgentOptions = LocalAgentOptions
        self.default_model = default_model
        self.cwd = cwd or Path.cwd()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self.default_model
        prompt = self._format_prompt(request)
        # Agent.prompt is sync in Python SDK — run as-is; callers may wrap in to_thread if needed.
        result = self._Agent.prompt(
            prompt,
            self._AgentOptions(
                api_key=self.api_key,
                model=model,
                local=self._LocalAgentOptions(cwd=str(self.cwd)),
            ),
        )
        if getattr(result, "status", None) == "error":
            run_id = getattr(result, "id", None)
            logger.error("Cursor SDK run failed: run_id=%s", run_id)
            raise RuntimeError(f"Cursor SDK run failed: run_id={run_id}")

        content = getattr(result, "result", None) or getattr(result, "text", None) or str(result)
        if not isinstance(content, str):
            content = str(content)
        try:
            parsed = extract_json_object(content)
        except ValueError as exc:
            logger.error("Cursor SDK response was not valid JSON: %s", exc)
            raise
        run_id = getattr(result, "id", None)
        return LLMResponse(
            content=content,
            parsed=parsed,
            model=model,
            provider=self.name,
            run_id=str(run_id) if run_id else None,
        )

    def _format_prompt(self, request: LLMRequest) -> str:
        parts: list[str] = [
            "You are a reasoning backend for AI Engineering Lab.",
            "Return ONLY a single JSON object. Do not edit project files.",
            "Do not run shell commands. Do not invent FACT without sources.",
            "",
        ]
        for msg in request.messages:
            parts.append(f"## {msg.role.upper()}\n{msg.content}\n")
        if request.response_schema_name:
            parts.append(f"Schema name hint: {request.response_schema_name}")
        return "\n".join(parts)
