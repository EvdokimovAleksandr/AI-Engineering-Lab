"""JSONL run-event sink — thin foundation for future OpenTelemetry export."""

from __future__ import annotations

import threading
from pathlib import Path

from ai_lab.core.models import RunEvent


class RunEventSink:
    """Append-only event log for a single lab run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.touch()

    def emit(self, event: RunEvent) -> None:
        line = event.model_dump_json() + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read_all(self) -> list[RunEvent]:
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        events: list[RunEvent] = []
        for line in text.splitlines():
            if line.strip():
                events.append(RunEvent.model_validate_json(line))
        return events
