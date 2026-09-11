"""Append-only decision journal (JSONL)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ai_lab.core.models import DecisionRecord


class DecisionLog:
    """Persists DecisionRecord entries for auditability over long-lived projects."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.touch()

    def append(self, record: DecisionRecord) -> None:
        line = record.model_dump_json() + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def read_all(self) -> list[DecisionRecord]:
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        records: list[DecisionRecord] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            records.append(DecisionRecord.model_validate_json(line))
        return records

    def filter_by_status(self, status: str) -> list[DecisionRecord]:
        return [r for r in self.read_all() if r.status.value == status]
