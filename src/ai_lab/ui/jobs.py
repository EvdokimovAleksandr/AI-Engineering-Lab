"""Background LabRuntime jobs for non-blocking UI runs.

UI must not block HTTP until the investigation finishes. Jobs persist a small
lifecycle file under the run dir so reconnect/reload can restore state.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_lab.core.enums import ProjectState
from ai_lab.core.models import LabConfig
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.logger import get_logger
from ai_lab.observability.tracing import RunEventSink
from ai_lab.orchestrator.hitl import HitlDecision, HitlGate
from ai_lab.orchestrator.runtime import LabRuntime

logger = get_logger(__name__)

LIFECYCLE_FILENAME = "ui_lifecycle.json"


@dataclass
class RunJob:
    """In-memory handle for a UI-started run (also mirrored to disk)."""

    run_id: str
    project_id: str
    status: str = "RUNNING"  # RUNNING | COMPLETED | FAILED | ERROR
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    sink: RunEventSink | None = None
    thread: threading.Thread | None = None


# Active jobs for live SSE fan-out. Completed runs remain readable from files.
_JOBS: dict[str, RunJob] = {}
_JOBS_LOCK = threading.Lock()


def get_job(run_id: str) -> RunJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(run_id)


def register_job(job: RunJob) -> None:
    with _JOBS_LOCK:
        _JOBS[job.run_id] = job


def unregister_live(run_id: str) -> None:
    """Drop live sink reference after finish; lifecycle file remains authoritative."""
    with _JOBS_LOCK:
        job = _JOBS.get(run_id)
        if job is not None:
            job.sink = None


def lifecycle_path(project: ProjectStore, run_id: str) -> Path:
    return project.root / ".runs" / run_id / LIFECYCLE_FILENAME


def write_lifecycle(project: ProjectStore, run_id: str, payload: dict[str, Any]) -> None:
    path = lifecycle_path(project, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def read_lifecycle(project: ProjectStore, run_id: str) -> dict[str, Any] | None:
    path = lifecycle_path(project, run_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def start_run_job(
    *,
    store: ProjectStore,
    config: LabConfig,
    repo_root: Path,
    problem: str,
    auto_approve_hitl: bool = False,
    simulation_fixture: str | None = None,
) -> RunJob:
    """Create LabRuntime, persist RUNNING lifecycle, execute in a daemon thread.

    Jobs are in-process threads — they do not survive UI process restart.
    Event JSONL on disk still allows SSE replay of already-emitted events.
    """
    runtime = LabRuntime(
        store,
        config,
        repo_root=repo_root,
        hitl=HitlGate(auto_approve=auto_approve_hitl),
        problem_override=problem,
        simulation_fixture=simulation_fixture,
    )
    # Manifest + problem snapshot before the thread starts so GET /runs/{id} works immediately.
    runtime.run_store.build_manifest(
        config=config,
        repo_root=repo_root,
        budget=runtime.budget,
        model_id=next(iter(config.models.values()), "unknown"),
    )
    store.write_text(runtime.run_store.rel("inputs", "ui_problem.md"), problem)
    job = RunJob(run_id=runtime.run_id, project_id=store.name, sink=runtime.sink)
    write_lifecycle(
        store,
        runtime.run_id,
        {
            "run_id": runtime.run_id,
            "project_id": store.name,
            "status": "RUNNING",
            "started_at": job.started_at,
            "error": None,
        },
    )
    register_job(job)

    def _worker() -> None:
        try:
            snapshot = asyncio.run(runtime.run())
            eng = None
            if snapshot.adjudication_status is not None:
                eng = snapshot.adjudication_status.value
            status = "COMPLETED"
            error = None
            if snapshot.state == ProjectState.BUDGET_EXCEEDED:
                status = "FAILED"
                # Runtime swallows BudgetExceeded so the snapshot is saved; surface the reason.
                error = runtime.stop_reason or "budget exceeded"
                job.error = error
            elif snapshot.state == ProjectState.AWAITING_HUMAN:
                status = "AWAITING_HUMAN"
            job.status = status
            job.finished_at = datetime.now(timezone.utc).isoformat()
            write_lifecycle(
                store,
                runtime.run_id,
                {
                    "run_id": runtime.run_id,
                    "project_id": store.name,
                    "status": status,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "final_state": snapshot.state.value,
                    "engineering_outcome": eng,
                    "error": error,
                },
            )
        except Exception as exc:
            logger.error("UI run job %s failed: %s", runtime.run_id, exc)
            job.status = "ERROR"
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc).isoformat()
            write_lifecycle(
                store,
                runtime.run_id,
                {
                    "run_id": runtime.run_id,
                    "project_id": store.name,
                    "status": "ERROR",
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "error": str(exc),
                },
            )
            # Best-effort failure event if runtime did not emit run.completed.
            try:
                from ai_lab.core.models import RunEvent

                runtime.sink.emit(
                    RunEvent(
                        run_id=runtime.run_id,
                        message="run.failed",
                        status="error",
                        data={"reason": str(exc)},
                    )
                )
            except Exception as emit_exc:
                logger.error("Failed to emit run.failed for %s: %s", runtime.run_id, emit_exc)
        finally:
            unregister_live(runtime.run_id)

    thread = threading.Thread(target=_worker, name=f"lab-run-{runtime.run_id}", daemon=True)
    job.thread = thread
    thread.start()
    return job


def resume_run_job(
    *,
    store: ProjectStore,
    config: LabConfig,
    repo_root: Path,
    run_id: str,
    choice: str | None = None,
    note: str = "",
    answers: dict[str, str] | None = None,
) -> RunJob:
    """Continue the same run_id after a scope/HITL pause. Does not create a new project."""
    if not run_id:
        raise ValueError("resume requires run_id")
    decision = HitlDecision(approved=True, choice=choice, note=note or "", answers=answers)
    runtime = LabRuntime(
        store,
        config,
        repo_root=repo_root,
        hitl=HitlGate(auto_approve=False, pending_decision=decision),
        resume_run_id=run_id,
        problem_override=None,
    )
    job = RunJob(run_id=run_id, project_id=store.name, sink=runtime.sink)
    write_lifecycle(
        store,
        run_id,
        {
            "run_id": run_id,
            "project_id": store.name,
            "status": "RUNNING",
            "started_at": job.started_at,
            "error": None,
        },
    )
    register_job(job)

    def _worker() -> None:
        try:
            snapshot = asyncio.run(runtime.run())
            eng = None
            if snapshot.adjudication_status is not None:
                eng = snapshot.adjudication_status.value
            status = "COMPLETED"
            error = None
            if snapshot.state == ProjectState.BUDGET_EXCEEDED:
                status = "FAILED"
                error = runtime.stop_reason or "budget exceeded"
                job.error = error
            elif snapshot.state == ProjectState.AWAITING_HUMAN:
                status = "AWAITING_HUMAN"
            job.status = status
            job.finished_at = datetime.now(timezone.utc).isoformat()
            write_lifecycle(
                store,
                runtime.run_id,
                {
                    "run_id": runtime.run_id,
                    "project_id": store.name,
                    "status": status,
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "final_state": snapshot.state.value,
                    "engineering_outcome": eng,
                    "error": error,
                },
            )
        except Exception as exc:
            logger.error("UI resume job %s failed: %s", runtime.run_id, exc)
            job.status = "ERROR"
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc).isoformat()
            write_lifecycle(
                store,
                runtime.run_id,
                {
                    "run_id": runtime.run_id,
                    "project_id": store.name,
                    "status": "ERROR",
                    "started_at": job.started_at,
                    "finished_at": job.finished_at,
                    "error": str(exc),
                },
            )
        finally:
            unregister_live(runtime.run_id)

    thread = threading.Thread(target=_worker, name=f"lab-resume-{run_id}", daemon=True)
    job.thread = thread
    thread.start()
    return job
