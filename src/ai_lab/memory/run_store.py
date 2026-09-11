"""Run-scoped storage: manifest + immutable computation artifacts."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_lab.core.models import ComputationArtifact, LabConfig, RunBudget, RunManifest
from ai_lab.memory.project_store import ProjectStore


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or "unknown"
    except Exception:
        return "unknown"


def _dependency_version() -> str:
    try:
        from importlib.metadata import version

        return version("ai-engineering-lab")
    except Exception:
        return "unknown"


class RunStore:
    """
    projects/<name>/.runs/<run_id>/
      manifest.json
      events.jsonl  (optional mirror)
      computations/<artifact_id>.json
      reviews/
      decisions/
    """

    def __init__(self, project: ProjectStore, run_id: str) -> None:
        self.project = project
        self.run_id = run_id
        self.rel_root = f".runs/{run_id}"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for sub in ("", "computations", "reviews", "decisions", "inputs", "artifacts"):
            path = self.project.root / self.rel_root / sub if sub else self.project.root / self.rel_root
            path.mkdir(parents=True, exist_ok=True)

    def rel(self, *parts: str) -> str:
        return "/".join((self.rel_root, *parts))

    def build_manifest(
        self,
        *,
        config: LabConfig,
        repo_root: Path,
        budget: RunBudget | None,
        model_id: str | None = None,
    ) -> RunManifest:
        input_hashes: dict[str, str] = {}
        for name in ("problem.md", "requirements.md", "assumptions.md"):
            p = self.project.root / name
            if p.is_file():
                input_hashes[name] = _sha256_file(p)
            else:
                input_hashes[name] = "unknown"

        config_dump = json.dumps(config.model_dump(mode="json"), sort_keys=True, default=str)
        prompt_versions = {
            "chief_engineer": "v2-synthesis-bundle",
            "verification": "v2-blind-bundle+checks",
            "red_team": "v2-blind-bundle",
            "simulation": "v2-immutable-artifacts",
        }
        manifest = RunManifest(
            run_id=self.run_id,
            project_id=self.project.name,
            model_provider=config.provider or "unknown",
            model_id=model_id or next(iter(config.models.values()), "unknown"),
            model_parameters={"temperature": "unknown"},
            prompt_versions=prompt_versions,
            python_version=sys.version.replace("\n", " "),
            dependency_version=_dependency_version(),
            tool_versions={
                "python.execute": "ast-subprocess-v1",
                "research.query": "stub-v1",
                "platform": platform.platform(),
            },
            input_hashes=input_hashes,
            git_commit=_git_commit(repo_root),
            configuration_hash=_sha256_text(config_dump),
            budget=budget,
            notes=[],
        )
        self.save_manifest(manifest)
        # Copy inputs snapshot (immutable for this run)
        for name in input_hashes:
            src = self.project.root / name
            if src.is_file():
                dest = self.rel("inputs", name)
                self.project.write_text(dest, src.read_text(encoding="utf-8"))
        return manifest

    def save_manifest(self, manifest: RunManifest) -> str:
        return self.project.write_json(self.rel("manifest.json"), manifest.model_dump(mode="json"))

    def load_manifest(self) -> RunManifest:
        return RunManifest.model_validate(self.project.read_json(self.rel("manifest.json")))

    def finish_manifest(self, *, final_state: str) -> RunManifest:
        manifest = self.load_manifest()
        manifest.finished_at = datetime.now(timezone.utc)
        manifest.final_state = final_state
        self.save_manifest(manifest)
        return manifest

    def save_computation(self, artifact: ComputationArtifact) -> str:
        """Immutable write — refuses overwrite of existing artifact_id."""
        rel = self.rel("computations", f"{artifact.artifact_id}.json")
        full = self.project.root / rel
        if full.exists():
            raise FileExistsError(f"Computation artifact already exists (immutable): {rel}")
        self.project.write_json(rel, artifact.model_dump(mode="json"))
        return rel

    def list_computations(self) -> list[ComputationArtifact]:
        folder = self.project.root / self.rel_root / "computations"
        if not folder.is_dir():
            return []
        out: list[ComputationArtifact] = []
        for path in sorted(folder.glob("*.json")):
            out.append(ComputationArtifact.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        return out

    def save_review_json(self, name: str, data: Any) -> str:
        return self.project.write_json(self.rel("reviews", name), data)


def hash_code(code: str) -> str:
    return _sha256_text(code)
