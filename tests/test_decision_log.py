"""Decision log append/read tests."""

from pathlib import Path

from ai_lab.core.enums import DecisionStatus
from ai_lab.core.models import DecisionRecord
from ai_lab.memory.decision_log import DecisionLog


def test_decision_log_roundtrip(tmp_path: Path) -> None:
    log = DecisionLog(tmp_path / "decisions.jsonl")
    rec = DecisionRecord(
        question="Is spinning the bottleneck?",
        hypothesis="Yes under current hosts",
        agents_involved=["research", "red_team"],
        status=DecisionStatus.PROPOSED,
        next_action="Run verification",
    )
    log.append(rec)
    items = log.read_all()
    assert len(items) == 1
    assert items[0].decision_id == rec.decision_id
    assert items[0].question == rec.question
