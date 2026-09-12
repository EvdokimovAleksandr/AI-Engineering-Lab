"""JSONL run-event sink — thin foundation for future OpenTelemetry export."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from ai_lab.core.models import RunEvent

# Live SSE subscribers: called after each durable append (UI transport only).
EventListener = Callable[[RunEvent], None]


class RunEventSink:
    """Append-only event log for a single lab run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._listeners: list[EventListener] = []
        if not self.path.exists():
            self.path.touch()

    def add_listener(self, listener: EventListener) -> None:
        """Subscribe to live emits (e.g. SSE). Does not replay history."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: EventListener) -> None:
        with self._lock:
            self._listeners = [x for x in self._listeners if x is not listener]

    def emit(self, event: RunEvent) -> None:
        line = event.model_dump_json() + "\n"
        listeners: list[EventListener]
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Listener failure must not break the lab pipeline.
                pass

    def read_all(self) -> list[RunEvent]:
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        events: list[RunEvent] = []
        for line in text.splitlines():
            if line.strip():
                events.append(RunEvent.model_validate_json(line))
        return events

    def read_after(self, offset: int = 0) -> tuple[list[RunEvent], int]:
        """Read events starting at line offset; return (events, next_offset)."""
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        events: list[RunEvent] = []
        for line in lines[offset:]:
            if line.strip():
                events.append(RunEvent.model_validate_json(line))
        return events, len(lines)
