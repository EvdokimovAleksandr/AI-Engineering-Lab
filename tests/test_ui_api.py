"""Local UI/API: LabRuntime adapter, no second orchestrator, security rejects."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

import pytest
import yaml

from ai_lab.core.models import LabConfig
from ai_lab.memory.project_store import ProjectStore
from ai_lab.ui.http import make_server
from ai_lab.ui.service import UiApiError, create_run, get_result, get_status, list_projects

REPO = Path(__file__).resolve().parents[1]


def _copy_project(tmp_path: Path) -> Path:
    src = REPO / "projects" / "spider_silk_industrial"
    projects = tmp_path / "projects"
    dst = projects / "spider_silk_industrial"
    dst.mkdir(parents=True)
    for name in ("problem.md", "requirements.md", "assumptions.md"):
        (dst / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
    ProjectStore(dst).ensure_layout()
    return projects


def _config(**runtime: object) -> LabConfig:
    raw = yaml.safe_load((REPO / "config" / "default.yaml").read_text(encoding="utf-8"))
    raw["provider"] = "mock"
    raw["runtime"]["hitl_on_disputed"] = False
    raw["runtime"].update(runtime)
    return LabConfig.model_validate(raw)


def test_get_projects(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    assert "spider_silk_industrial" in list_projects(repo_root=tmp_path, projects_dir=pdir)


def test_invalid_problem(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    with pytest.raises(UiApiError, match="problem"):
        create_run({"problem": "  ", "project": "spider_silk_industrial"}, repo_root=REPO, projects_dir=pdir)


def test_unknown_project(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    with pytest.raises(UiApiError, match="Unknown project"):
        create_run({"problem": "hello", "project": "nope"}, repo_root=REPO, projects_dir=pdir)


def test_ui_cannot_override_sandbox(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    with pytest.raises(UiApiError, match="sandbox"):
        create_run(
            {
                "problem": "x",
                "project": "spider_silk_industrial",
                "sandbox": {"network": "allow"},
            },
            repo_root=REPO,
            projects_dir=pdir,
        )


def test_ui_cannot_override_routing_or_inject_shell(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    with pytest.raises(UiApiError):
        create_run(
            {"problem": "x", "project": "spider_silk_industrial", "routing": {}},
            repo_root=REPO,
            projects_dir=pdir,
        )
    with pytest.raises(UiApiError):
        create_run(
            {"problem": "x", "project": "spider_silk_industrial", "command": "rm -rf /"},
            repo_root=REPO,
            projects_dir=pdir,
        )
    with pytest.raises(UiApiError):
        create_run(
            {"problem": "x", "project": "spider_silk_industrial", "host_path": "C:/secrets"},
            repo_root=REPO,
            projects_dir=pdir,
        )


def test_plan_creation_and_status(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    created = create_run(
        {
            "problem": "Рассчитай напряжение в волокне диаметром 5 мкм при силе 0.2 N",
            "project": "spider_silk_industrial",
            "action": "plan",
        },
        repo_root=REPO,
        projects_dir=pdir,
        config=_config(),
    )
    assert created["run_id"]
    assert created["state"] == "PLANNED"
    st = get_status(created["run_id"], repo_root=REPO, projects_dir=pdir)
    assert st["graph_id"] == "standard_pipeline"
    res = get_result(created["run_id"], repo_root=REPO, projects_dir=pdir)
    assert res["run_id"] == created["run_id"]
    ui_problem = (pdir / "spider_silk_industrial" / ".runs" / created["run_id"] / "inputs" / "ui_problem.md")
    assert ui_problem.is_file()
    assert "0.2 N" in ui_problem.read_text(encoding="utf-8")


def test_http_get_root_and_post_invalid(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    httpd = make_server("127.0.0.1", 0, repo_root=tmp_path)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "AI Engineering Lab" in body
        conn.request("POST", "/api/runs", body=json.dumps({"problem": "", "project": "x"}), headers={"Content-Type": "application/json"})
        bad = conn.getresponse()
        bad.read()
        assert bad.status == 400
        conn.close()
    finally:
        httpd.shutdown()


def test_ui_run_uniaxial_through_labruntime(tmp_path: Path) -> None:
    pdir = _copy_project(tmp_path)
    cfg = _config()
    cfg.simulation["pipeline"] = "uniaxial_tension"
    created = create_run(
        {
            "problem": "Evaluate uniaxial tensile stress for the synthetic fiber fixture",
            "project": "spider_silk_industrial",
            "action": "run",
        },
        repo_root=REPO,
        projects_dir=pdir,
        config=cfg,
    )
    assert created["run_id"]
    st = get_status(created["run_id"], repo_root=REPO, projects_dir=pdir)
    res = get_result(created["run_id"], repo_root=REPO, projects_dir=pdir)
    assert st["graph_id"] == "uniaxial_tension_pipeline"
    assert res["simulation_status"] == "SUCCESS"
    assert "run_tensile_simulation" in (st.get("task_status") or {})
    run_dir = pdir / "spider_silk_industrial" / ".runs" / created["run_id"]
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "planner" / "task_graph.json").is_file()
    assert (run_dir / "artifacts" / "simulation_result.json").is_file()


def test_ui_plan_writes_existing_run_artifacts(tmp_path: Path) -> None:
    """Plan+status go through LabRuntime artifacts under .runs/."""
    pdir = _copy_project(tmp_path)
    created = create_run(
        {"problem": "tensile check", "project": "spider_silk_industrial", "action": "plan"},
        repo_root=REPO,
        projects_dir=pdir,
        config=_config(),
    )
    run_dir = pdir / "spider_silk_industrial" / ".runs" / created["run_id"]
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "planner" / "task_graph.json").is_file()
    assert (run_dir / "planner" / "validation.json").is_file()
    graph = json.loads((run_dir / "planner" / "task_graph.json").read_text(encoding="utf-8"))
    assert graph["graph_id"] == "standard_pipeline"
