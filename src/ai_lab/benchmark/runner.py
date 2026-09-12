"""Run a benchmark as an ordinary lab project (isolated project namespace)."""

from __future__ import annotations

import shutil
from pathlib import Path

from ai_lab.core.models import ProjectSnapshot
from ai_lab.orchestrator.runtime import run_project
from ai_lab.benchmark.registry import get_benchmark


def _ensure_workspace(repo_root: Path, benchmark_id: str, *, work_root: Path | None) -> Path:
    """Copy benchmark scaffold into an isolated projects workspace.

    Claims/evidence stay under that project/.runs/<run_id>/ — never shared
    with other benchmarks or spider_silk_industrial.
    """
    spec = get_benchmark(repo_root, benchmark_id)
    base = work_root or (repo_root / "benchmarks" / ".workspace")
    dest = base / spec.benchmark_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in (
        "problem.md",
        "requirements.md",
        "assumptions.md",
        "expected_capabilities.md",
        "evaluation.md",
        "final_report.md",
    ):
        src = spec.root / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    # Minimal stubs so ProjectStore layout matches ordinary projects.
    for stub in ("requirements.md", "assumptions.md", "final_report.md"):
        path = dest / stub
        if not path.is_file():
            path.write_text(f"# {stub}\n\n(benchmark stub)\n", encoding="utf-8")
    return base


async def run_benchmark(
    benchmark_id: str,
    *,
    repo_root: Path,
    provider: str | None = "mock",
    config_path: Path | None = None,
    auto_approve_hitl: bool = True,
    work_root: Path | None = None,
) -> ProjectSnapshot:
    """Execute benchmark via LabRuntime; persists a normal RunManifest."""
    projects_dir = _ensure_workspace(repo_root, benchmark_id, work_root=work_root)
    return await run_project(
        benchmark_id,
        provider=provider,
        projects_dir=projects_dir,
        config_path=config_path,
        auto_approve_hitl=auto_approve_hitl,
        resume=False,
    )
