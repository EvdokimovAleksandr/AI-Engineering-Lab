"""UI/API service: thin adapter over LabRuntime. Not a second orchestrator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ai_lab.config_loader import load_config
from ai_lab.core.enums import ProjectState
from ai_lab.core.models import LabConfig, RunEvent
from ai_lab.llm.config import apply_provider_override
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.tracing import RunEventSink
from ai_lab.orchestrator.hitl import HitlGate
from ai_lab.orchestrator.runtime import LabRuntime, repo_root_from_here
from ai_lab.sandbox.models import FORBIDDEN_COMPUTE_KEYS
from ai_lab.ui.jobs import get_job, read_lifecycle, start_run_job
from ai_lab.ui.report import (
    build_result_view,
    build_run_summary,
    export_markdown,
)

# UI may only send problem + project metadata. Everything else is trusted config.
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
_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class UiApiError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _repo_root() -> Path:
    return repo_root_from_here()


def _projects_dir(root: Path) -> Path:
    return root / "projects"


def _reject_untrusted_fields(payload: dict[str, Any], *, allowed_extra: set[str] | None = None) -> None:
    allowed = allowed_extra or set()
    for key in payload:
        normalized = str(key).replace("-", "_").lower()
        if normalized in FORBIDDEN_UI_KEYS:
            raise UiApiError(f"UI must not set {key!r}")
        if normalized not in allowed and key not in allowed:
            # Still reject unknown keys that look like privilege escalation.
            if normalized in FORBIDDEN_UI_KEYS:
                raise UiApiError(f"UI must not set {key!r}")


def _validate_project_id(project_id: str) -> str:
    if not isinstance(project_id, str) or not project_id.strip():
        raise UiApiError("project id is required")
    pid = project_id.strip()
    if ".." in pid or "/" in pid or "\\" in pid or not _PROJECT_ID_RE.match(pid):
        raise UiApiError("Invalid project id")
    return pid


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    base = (base[:40] or "investigation").rstrip("_")
    return f"{base}_{uuid4().hex[:6]}"


def list_projects(*, repo_root: Path | None = None, projects_dir: Path | None = None) -> list[str]:
    """Backward-compatible list of project directory names."""
    return [p["project_id"] for p in list_projects_detailed(repo_root=repo_root, projects_dir=projects_dir)]


def list_projects_detailed(
    *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> list[dict[str, Any]]:
    root = repo_root or _repo_root()
    d = projects_dir or _projects_dir(root)
    if not d.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for child in sorted(d.iterdir()):
        if not child.is_dir() or not (child / "problem.md").is_file():
            continue
        meta = _load_project_meta(child)
        runs = _list_run_ids(child)
        last = runs[0] if runs else None
        eng = None
        if last:
            man = _load_json(child / ".runs" / last / "manifest.json") or {}
            eng = man.get("engineering_outcome")
        rows.append(
            {
                "project_id": child.name,
                "title": meta.get("title") or child.name.replace("_", " "),
                "domain": meta.get("domain"),
                "run_count": len(runs),
                "last_run_id": last,
                "last_engineering_outcome": eng,
                "created_at": meta.get("created_at"),
            }
        )
    return rows


def create_project(
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    projects_dir: Path | None = None,
) -> dict[str, Any]:
    """Create a file-backed project from a natural-language problem (optional fields)."""
    if not isinstance(payload, dict):
        raise UiApiError("JSON object required")
    _reject_untrusted_fields(
        payload,
        allowed_extra={
            "name",
            "title",
            "project",
            "project_id",
            "domain",
            "depth",
            "constraints",
            "problem",
        },
    )
    problem = payload.get("problem")
    if problem is not None and (not isinstance(problem, str) or not problem.strip()):
        raise UiApiError("problem must be a non-empty string when provided")
    root = repo_root or _repo_root()
    pdir = projects_dir or _projects_dir(root)
    pdir.mkdir(parents=True, exist_ok=True)
    raw_name = payload.get("name") or payload.get("project_id") or payload.get("project")
    if raw_name:
        project_id = _validate_project_id(str(raw_name))
    else:
        seed = (problem or payload.get("title") or "investigation")[:80]
        project_id = _slugify(str(seed))
    target = pdir / project_id
    if target.exists():
        raise UiApiError(f"Project {project_id!r} already exists", status=409)
    target.mkdir(parents=True)
    store = ProjectStore(target)
    store.ensure_layout()
    title = str(payload.get("title") or project_id.replace("_", " "))
    if isinstance(problem, str) and problem.strip():
        store.write_text(
            "problem.md",
            f"# {title}\n\n{problem.strip()}\n",
        )
    meta = {
        "project_id": project_id,
        "title": title,
        "domain": payload.get("domain"),
        "depth": payload.get("depth"),
        "constraints": payload.get("constraints"),
        "created_at": _utc_now(),
    }
    store.write_json("project_meta.json", meta)
    return {"project_id": project_id, "title": title, "meta": meta}


def get_project(
    project_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    pdir = projects_dir or _projects_dir(root)
    pid = _validate_project_id(project_id)
    store = ProjectStore.open(pdir, pid)
    if not (store.root / "problem.md").is_file():
        raise UiApiError(f"Unknown project {pid!r}", status=404)
    meta = _load_project_meta(store.root)
    runs = []
    for rid in _list_run_ids(store.root):
        man = _load_json(store.root / ".runs" / rid / "manifest.json") or {}
        life = read_lifecycle(store, rid)
        runs.append(build_run_summary(store, rid, manifest=man, lifecycle=life))
    return {
        "project_id": pid,
        "title": meta.get("title") or pid,
        "meta": meta,
        "problem_preview": _safe_preview(store.root / "problem.md"),
        "runs": runs,
    }


def create_run(
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    projects_dir: Path | None = None,
    config: LabConfig | None = None,
    wait: bool | None = None,
) -> dict[str, Any]:
    """Create a plan or execute via LabRuntime. Payload is UNTRUSTED_DATA.

    wait=True (default for direct callers / tests): block until finished.
    wait=False (HTTP UI): return RUNNING immediately and continue in background.
    """
    if not isinstance(payload, dict):
        raise UiApiError("JSON object required")
    _reject_untrusted_fields(
        payload,
        allowed_extra={
            "problem",
            "project",
            "project_id",
            "action",
            "wait",
            "async",
            "create_project",
            "title",
            "domain",
            "depth",
            "constraints",
            "name",
        },
    )
    extra = set(payload) - {
        "problem",
        "project",
        "project_id",
        "action",
        "wait",
        "async",
        "create_project",
        "title",
        "domain",
        "depth",
        "constraints",
        "name",
    }
    if extra:
        raise UiApiError(f"Unsupported UI fields: {sorted(extra)}")
    problem = payload.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        raise UiApiError("problem must be a non-empty string")
    root = repo_root or _repo_root()
    pdir = projects_dir or _projects_dir(root)
    project_id = payload.get("project") or payload.get("project_id") or payload.get("name")
    create_if_missing = bool(payload.get("create_project"))
    if not project_id:
        # Product UX: one natural-language prompt creates a project automatically.
        created = create_project(
            {
                "problem": problem,
                "title": payload.get("title"),
                "domain": payload.get("domain"),
                "depth": payload.get("depth"),
                "constraints": payload.get("constraints"),
            },
            repo_root=root,
            projects_dir=pdir,
        )
        project_id = created["project_id"]
    else:
        project_id = _validate_project_id(str(project_id))
        known = list_projects(repo_root=root, projects_dir=pdir)
        if project_id not in known:
            if create_if_missing:
                create_project(
                    {
                        "name": project_id,
                        "problem": problem,
                        "title": payload.get("title"),
                        "domain": payload.get("domain"),
                        "depth": payload.get("depth"),
                        "constraints": payload.get("constraints"),
                    },
                    repo_root=root,
                    projects_dir=pdir,
                )
            else:
                raise UiApiError(f"Unknown project {project_id!r}", status=404)

    action = str(payload.get("action") or "run").strip().lower()
    if action not in ALLOWED_ACTIONS:
        raise UiApiError("action must be plan or run")

    # Resolve wait: explicit payload wins; else parameter; else True for sync API compat.
    if wait is None:
        if "wait" in payload:
            wait = bool(payload["wait"])
        elif "async" in payload:
            wait = not bool(payload["async"])
        else:
            wait = True

    cfg = config or load_config(root / "config" / "default.yaml")
    cfg = apply_provider_override(cfg, cfg.provider)
    store = ProjectStore.open(pdir, project_id)

    if action == "plan":
        runtime = LabRuntime(
            store,
            cfg,
            repo_root=root,
            hitl=HitlGate(auto_approve=False),
            problem_override=problem,
        )
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
            "project_id": project_id,
            "action": "plan",
            "state": "PLANNED",
            "status": "PLANNED",
            "graph_id": graph.graph_id,
            "task_ids": [t.task_id for t in graph.tasks],
        }

    if not wait:
        job = start_run_job(store=store, config=cfg, repo_root=root, problem=problem)
        return {
            "run_id": job.run_id,
            "project_id": project_id,
            "action": "run",
            "state": "RUNNING",
            "status": "RUNNING",
            "hitl_required": False,
        }

    runtime = LabRuntime(
        store,
        cfg,
        repo_root=root,
        hitl=HitlGate(auto_approve=False),
        problem_override=problem,
    )
    snapshot = _run_async(runtime.run())
    return {
        "run_id": snapshot.run_id,
        "project_id": project_id,
        "action": "run",
        "state": snapshot.state.value,
        "status": "COMPLETED",
        "hitl_required": snapshot.state == ProjectState.AWAITING_HUMAN,
    }


def create_project_run(
    project_id: str,
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    projects_dir: Path | None = None,
    config: LabConfig | None = None,
    wait: bool = False,
) -> dict[str, Any]:
    """POST /api/projects/{id}/runs — start investigation under an existing project."""
    body = dict(payload)
    body["project"] = project_id
    body.setdefault("action", "run")
    return create_run(
        body, repo_root=repo_root, projects_dir=projects_dir, config=config, wait=wait
    )


def _run_async(coro):  # noqa: ANN001
    import asyncio

    return asyncio.run(coro)


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


def get_run(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    life = read_lifecycle(store, run_id)
    job = get_job(run_id)
    if job is not None and life is not None:
        life = {**life, "status": job.status, "error": job.error or life.get("error")}
    summary = build_run_summary(store, run_id, manifest=manifest, lifecycle=life)
    return {
        "run_id": run_id,
        "project_id": store.name,
        "manifest": manifest,
        "lifecycle": life,
        "summary": summary,
        # Legacy fields kept for V2.5 clients/tests.
        "status": get_status(run_id, repo_root=root, projects_dir=projects_dir),
        "result": get_result(run_id, repo_root=root, projects_dir=projects_dir),
    }


def get_status(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    snapshot = store.load_snapshot()
    life = read_lifecycle(store, run_id)
    summary = build_run_summary(store, run_id, manifest=manifest, lifecycle=life)
    executions = _load_run_json(store, run_id, "planner", "executions.json") or []
    graph = _load_run_json(store, run_id, "planner", "task_graph.json") or {}
    validation = _load_run_json(store, run_id, "planner", "validation.json") or {}
    task_status = {
        row.get("task_id"): row.get("status") for row in executions if isinstance(row, dict)
    }
    hitl = snapshot.state == ProjectState.AWAITING_HUMAN
    return {
        "run_id": run_id,
        "project_id": store.name,
        "state": snapshot.state.value,
        "lifecycle_status": summary["status"],
        "stage": summary.get("stage"),
        "engineering_status": summary.get("engineering_status"),
        "required_outputs": summary.get("required_outputs"),
        "verified_outputs": summary.get("verified_outputs"),
        "evidence_status": summary.get("evidence_status"),
        "pipeline": summary.get("pipeline"),
        "hitl_required": hitl,
        "hitl": snapshot.pending_hitl,
        "task_graph_status": validation.get("reason") or manifest.get("final_state"),
        "task_status": task_status,
        # Legacy fixed list retained for older UI; prefer `pipeline`.
        "stages": [n["stage"] for n in (summary.get("pipeline") or [])],
        "graph_id": graph.get("graph_id"),
        "final_state": manifest.get("final_state"),
        "adjudication_status": snapshot.adjudication_status.value if snapshot.adjudication_status else None,
        "error": summary.get("error"),
    }


def get_result(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    life = read_lifecycle(store, run_id)
    view = build_result_view(store, run_id, manifest=manifest, lifecycle=life)
    # Keep V2.5 keys used by older tests while exposing structured report.
    snapshot = store.load_snapshot()
    sim = _load_run_json(store, run_id, "artifacts", "simulation_result.json")
    ver = store.root / "reviews" / "last_verification.json"
    rt = store.root / "reviews" / "last_red_team.json"
    adj = store.root / "reviews" / "last_adjudication.json"
    return {
        **view,
        "state": snapshot.state.value,
        "hitl_required": snapshot.state == ProjectState.AWAITING_HUMAN,
        "task_graph_status": manifest.get("final_state"),
        "simulation_status": (sim or {}).get("status") if isinstance(sim, dict) else None,
        "simulation": sim if view.get("simulation") is None else view.get("simulation"),
        "verification_status": _json_status(ver, "status"),
        "red_team_status": _json_file(rt),
        "adjudication_status": _json_status(adj, "status") or view.get("engineering_outcome"),
        "conclusion": view.get("synthesis")
        or _json_file(store.root / "reviews" / "synthesis_bundle.json"),
        "links": view.get("provenance", {}).get("links")
        or {
            "task_graph": f".runs/{run_id}/planner/task_graph.json",
            "evidence": "knowledge/graph.json",
            "computation": f".runs/{run_id}/computations/",
            "verification": "reviews/last_verification.json",
        },
    }


def get_events(
    run_id: str,
    *,
    repo_root: Path | None = None,
    projects_dir: Path | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    root = repo_root or _repo_root()
    store, _manifest = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    sink = _events_sink(root, run_id, store)
    events, next_offset = sink.read_after(max(0, offset))
    return {
        "run_id": run_id,
        "offset": offset,
        "next_offset": next_offset,
        "events": [_public_event(e) for e in events],
    }


def get_event_sink(
    run_id: str, *, repo_root: Path | None = None, projects_dir: Path | None = None
) -> RunEventSink:
    root = repo_root or _repo_root()
    store, _ = _find_run(run_id, repo_root=root, projects_dir=projects_dir)
    job = get_job(run_id)
    if job is not None and job.sink is not None:
        return job.sink
    return _events_sink(root, run_id, store)


def export_result(
    run_id: str,
    *,
    fmt: str = "json",
    repo_root: Path | None = None,
    projects_dir: Path | None = None,
) -> tuple[str, str]:
    """Return (body, content_type) for markdown or json export."""
    result = get_result(run_id, repo_root=repo_root, projects_dir=projects_dir)
    if fmt == "markdown" or fmt == "md":
        return export_markdown(result), "text/markdown; charset=utf-8"
    return json.dumps(result, indent=2, ensure_ascii=False, default=str), "application/json; charset=utf-8"


def list_recent_runs(
    *, repo_root: Path | None = None, projects_dir: Path | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    root = repo_root or _repo_root()
    rows: list[dict[str, Any]] = []
    for proj in list_projects_detailed(repo_root=root, projects_dir=projects_dir):
        store = ProjectStore.open(projects_dir or _projects_dir(root), proj["project_id"])
        for rid in _list_run_ids(store.root):
            man = _load_json(store.root / ".runs" / rid / "manifest.json") or {}
            life = read_lifecycle(store, rid)
            summary = build_run_summary(store, rid, manifest=man, lifecycle=life)
            rows.append(summary)
    rows.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return rows[:limit]


def _events_sink(root: Path, run_id: str, store: ProjectStore) -> RunEventSink:
    # Prefer repo-level observability path (same as LabRuntime).
    cfg_path = root / "config" / "default.yaml"
    events_dir = root / ".runs"
    if cfg_path.is_file():
        try:
            cfg = load_config(cfg_path)
            events_dir = root / str(cfg.observability.get("run_events_dir", ".runs"))
        except Exception:
            pass
    path = events_dir / f"{run_id}.jsonl"
    return RunEventSink(path)


def _public_event(event: RunEvent) -> dict[str, Any]:
    data = dict(event.data or {})
    # Never leak secret-looking keys from event payloads.
    for key in list(data):
        lk = str(key).lower()
        if any(s in lk for s in ("api_key", "token", "secret", "password")):
            data.pop(key, None)
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "message": event.message,
        "status": event.status,
        "agent_role": event.agent_role,
        "task_id": event.task_id,
        "tool_name": event.tool_name,
        "duration_ms": event.duration_ms,
        "data": data,
    }


def _load_run_json(project: ProjectStore, run_id: str, *parts: str) -> Any | None:
    rel = "/".join((".runs", run_id, *parts))
    path = project.root / rel
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _json_file(path: Path) -> Any | None:
    return _load_json(path)


def _json_status(path: Path, key: str) -> Any | None:
    data = _json_file(path)
    if isinstance(data, dict):
        return data.get(key)
    return None


def _load_project_meta(root: Path) -> dict[str, Any]:
    data = _load_json(root / "project_meta.json")
    return data if isinstance(data, dict) else {}


def _list_run_ids(project_root: Path) -> list[str]:
    runs_dir = project_root / ".runs"
    if not runs_dir.is_dir():
        return []
    rows: list[tuple[str, str]] = []
    for child in runs_dir.iterdir():
        man = child / "manifest.json"
        if child.is_dir() and man.is_file():
            data = _load_json(man) or {}
            rows.append((str(data.get("started_at") or ""), child.name))
    rows.sort(reverse=True)
    return [name for _, name in rows]


def _safe_preview(path: Path, limit: int = 240) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
