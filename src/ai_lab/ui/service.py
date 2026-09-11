"""UI/API service: thin adapter over LabRuntime. Not a second orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_lab.config_loader import load_config
from ai_lab.core.enums import ProjectState
from ai_lab.core.models import LabConfig
from ai_lab.llm.config import apply_provider_override
from ai_lab.memory.project_store import ProjectStore
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime, repo_root_from_here
from ai_lab.sandbox.models import FORBIDDEN_COMPUTE_KEYS

# UI may only send problem + project. Everything else is trusted config.
FORBIDDEN_UI_KEYS = FORBIDDEN_COMPUTE_KEYS | frozenset(
    {
        "sandbox",
        "routing",
        "budget",
        "run_budget",
        "api_key",
        "api_keys",
        "provider",
        "config",
        "docker",
        "image",
        "network",
        "host_path",
        "cwd",
        "command",
        "shell",
        "auto_approve_hitl",
        "routing_policy",
        "independence",
        "fallback_backend",
        "docker_host",
        "solver_import",
        "privileged",
    }
)

ALLOWED_ACTIONS = frozenset({"plan", "run"})


class UiApiError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _repo_root() -> Path:
    return repo_root_from_here()


def _projects_dir(root: Path) -> Path:
    return root / "projects"


def list_projects(*, repo_root: Path | None = None, projects_dir: Path | None = None) -> list[str]:
    root = repo_root or _repo_root()
    d = projects_dir or _projects_dir(root)
    if not d.is_dir():
        return []
    names = []
    for child in sorted(d.iterdir()):
        if child.is_dir() and (child / "problem.md").is_file():
            names.append(child.name)
    return names


def _reject_untrusted_fields(payload: dict[str, Any]) -> None:
    for key in payload:
        normalized = str(key).replace("-", "_").lower()
        if normalized in FORBIDDEN_UI_KEYS:
            raise UiApiError(f"UI must not set {key!r}")


def _load_run_json(project: ProjectStore, run_id: str, *parts: str) -> Any | None:
    rel = "/".join((".runs", run_id, *parts))
    path = project.root / rel
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _find_run(
    run_id: str, *, repo_root: Path, projects_dir: Path | None = None
) -> tuple[ProjectStore, dict[str, Any]]:
    if not run_id or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise UiApiError("Invalid run_id", status=400)
    for name in list_projects(repo_root=repo_root, projects_dir=projects_dir):
        store = ProjectStore.open(projects_dir or _projects_dir(repo_root), name)
        manifest_path = store.root / ".runs" / run_id / "manifest.json"
        if manifest_path.is_file():
            return store, json.loads(manifest_path.read_text(encoding="utf-8"))
    raise UiApiError(f"Unknown run_id {run_id!r}", status=404)


def create_run(
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    projects_dir: Path | None = None,
    config: LabConfig | None = None,
) -> dict[str, Any]:
    """Create a plan or execute via LabRuntime. Payload is UNTRUSTED_DATA."""
    if not isinstance(payload, dict):
        raise UiApiError("JSON object required")
    _reject_untrusted_fields(payload)
    extra = set(payload) - {"problem", "project", "project_id", "action"}
    if extra:
        raise UiApiError(f"Unsupported UI fields: {sorted(extra)}")
    problem = payload.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        raise UiApiError("problem must be a non-empty string")
    project_id = payload.get("project") or payload.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise UiApiError("project is required")
    if ".." in project_id or "/" in project_id or "\\" in project_id:
        raise UiApiError("Invalid project id")
    action = str(payload.get("action") or "run").strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise UiApiError("action must be plan or run")
    root = repo_root or _repo_root()
    pdir = projects_dir or _projects_dir(root)
    known = list_projects(repo_root=root, projects_dir=pdir)
    if project_id not in known:
        raise UiApiError(f"Unknown project {project_id!r}", status=404)
    cfg = config or load_config(root / "config" / "default.yaml")
    cfg = apply_provider_override(cfg, cfg.provider)
    store = ProjectStore.open(pdir, project_id)
    runtime = LabRuntime(
        store,
        cfg,
        repo_root=root,
        hitl=HitlGate(auto_approve=False),
        problem_override=problem,
    )
    if action == "plan":
        runtime.run_store.build_manifest(
            config=cfg,
            repo_root=root,
            budget=runtime.budget,
            model_id=next(iter(cfg.models.values()), "unknown"),
        )
        runtime.project.write_text(runtime.run_store.rel("inputs", "ui_problem.md"), problem)
        graph = _run_async(runtime._prepare_task_graph())
        runtime.run_store.finish_manifest(final_state="PLANNED")
        return {
            "run_id": runtime.run_id,
            "action": "plan",
            "state": "PLANNED",
            "graph_id": graph.graph_id,
            "task_ids": [t.task_id for t in graph.tasks],
        }
    snapshot = _run_async(runtime.run())
    return {
        "run_id": snapshot.run_id,
        "action": "run",
        "state": snapshot.state.value,
        "hitl_required": snapshot.state == ProjectState.AWAITING_HUMAN,
    }


def _run_async(coro):  # noqa: ANN001
    import asyncio

    return asyncio.run(coro)


def get_run(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    return {
        "run_id": run_id,
        "project_id": store.name,
        "manifest": manifest,
        "status": get_status(run_id, repo_root=root, projects_dir=projects_dir),
        "result": get_result(run_id, repo_root=root, projects_dir=projects_dir),
    }


def get_status(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    snapshot = store.load_snapshot()
    executions = _load_run_json(store, run_id, "planner", "executions.json") or []
    graph = _load_run_json(store, run_id, "planner", "task_graph.json") or {}
    validation = _load_run_json(store, run_id, "planner", "validation.json") or {}
    stages = [
        "Problem",
        "Planning",
        "Validation",
        "Execution",
        "Verification",
        "Red Team",
        "Adjudication",
        "Result",
    ]
    task_status = {
        row.get("task_id"): row.get("status") for row in executions if isinstance(row, dict)
    }
    hitl = snapshot.state == ProjectState.AWAITING_HUMAN
    return {
        "run_id": run_id,
        "project_id": store.name,
        "state": snapshot.state.value,
        "hitl_required": hitl,
        "hitl": snapshot.pending_hitl,
        "task_graph_status": validation.get("reason") or manifest.get("final_state"),
        "task_status": task_status,
        "stages": stages,
        "graph_id": graph.get("graph_id"),
        "final_state": manifest.get("final_state"),
        "adjudication_status": snapshot.adjudication_status.value if snapshot.adjudication_status else None,
    }


def get_result(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    snapshot = store.load_snapshot()
    sim = _load_run_json(store, run_id, "artifacts", "simulation_result.json")
    ver = store.root / "reviews" / "last_verification.json"
    rt = store.root / "reviews" / "last_red_team.json"
    adj = store.root / "reviews" / "last_adjudication.json"
    return {
        "run_id": run_id,
        "state": snapshot.state.value,
        "hitl_required": snapshot.state == ProjectState.AWAITING_HUMAN,
        "task_graph_status": manifest.get("final_state"),
        "simulation_status": (sim or {}).get("status") if isinstance(sim, dict) else None,
        "simulation": sim,
        "verification_status": _json_status(ver, "status"),
        "red_team_status": _json_file(rt),
        "adjudication_status": _json_status(adj, "status"),
        "conclusion": _json_file(store.root / "reviews" / "synthesis_bundle.json"),
        "links": {
            "task_graph": f".runs/{run_id}/planner/task_graph.json",
            "evidence": "knowledge/graph.json",
            "computation": f".runs/{run_id}/computations/",
            "verification": "reviews/last_verification.json",
        },
    }


def _json_file(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _json_status(path: Path, key: str) -> Any | None:
    data = _json_file(path)
    if isinstance(data, dict):
        return data.get(key)
    return None
