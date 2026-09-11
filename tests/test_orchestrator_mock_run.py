"""End-to-end mock orchestrator run on spider silk scaffold (V2 gates)."""

from pathlib import Path

import pytest
import yaml

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import LabConfig
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime


REPO = Path(__file__).resolve().parents[1]


def _copy_scaffold(dst: Path) -> None:
    src = REPO / "projects" / "spider_silk_industrial"
    store = ProjectStore(dst)
    for name in ("problem.md", "requirements.md", "assumptions.md", "final_report.md"):
        (dst / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
    store.ensure_layout()


@pytest.mark.asyncio
async def test_orchestrator_mock_run(tmp_path: Path) -> None:
    project_dir = tmp_path / "spider_silk_industrial"
    project_dir.mkdir()
    _copy_scaffold(project_dir)

    config_raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    config_raw["provider"] = "mock"
    config_raw["runtime"]["hitl_on_disputed"] = False
    config = LabConfig.model_validate(config_raw)

    store = ProjectStore(project_dir)
    runtime = LabRuntime(
        store,
        config,
        repo_root=tmp_path,
        hitl=HitlGate(auto_approve=True),
    )
    snapshot = await runtime.run()

    assert snapshot.state == ProjectState.COMPLETED
    assert (project_dir / "research" / "research_batch.json").exists()
    assert (project_dir / "reviews" / "last_verification.json").exists()
    assert (project_dir / "reviews" / "last_red_team.json").exists()
    assert (project_dir / "reviews" / "synthesis_bundle.json").exists()
    assert (project_dir / "reviews" / "last_adjudication.json").exists()
    assert (project_dir / ".runs" / snapshot.run_id / "manifest.json").exists()
    comps = list((project_dir / ".runs" / snapshot.run_id / "computations").glob("*.json"))
    assert comps, "expected immutable computation artifacts"
    final = (project_dir / "final_report.md").read_text(encoding="utf-8")
    assert "Final report" in final
    assert "Report gate" in final
    assert "Verification" in final
    assert "Red team" in final
    assert "Awaiting lab synthesis" not in final
