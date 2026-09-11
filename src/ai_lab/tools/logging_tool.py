"""Structured logging tool for agents (optional explicit log writes)."""

from __future__ import annotations

from typing import Any

from ai_lab.core.models import RunEvent
from ai_lab.observability.logger import get_logger
from ai_lab.observability.tracing import RunEventSink
from ai_lab.tools.base import ToolSpec

logger = get_logger(__name__)


class LoggingTool:
    name = "logging.emit"
    description = "Emit a structured observability event for the current run"

    def __init__(self, sink: RunEventSink, run_id: str) -> None:
        self.sink = sink
        self.run_id = run_id

    def as_spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, handler=self.run)

    async def run(self, message: str = "", data: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        if not message:
            raise ValueError("logging.emit requires 'message'")
        event = RunEvent(
            run_id=self.run_id,
            message=message,
            data=data or {},
            status="ok",
        )
        self.sink.emit(event)
        logger.info("[%s] %s", self.run_id, message)
        return {"event_id": event.event_id}
