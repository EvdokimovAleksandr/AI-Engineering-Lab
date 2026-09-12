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
    projects/<name>/.runs/<run_id>/   # RUN-SCOPED (authoritative for a given run)
      manifest.json
      final_report.md                 # canonical report for this run
      events.jsonl  (optional mirror)
      computations/<artifact_id>.json
      reviews/
      claims/
      decisions/

    Project-scoped (not a run's source of truth):
      problem.md, project_meta.json, run index, legacy final_report.md
    """

    def __init__(self, project: ProjectStore, run_id: str) -> None:
        self.project = project
        self.run_id = run_id
        self.rel_root = f".runs/{run_id}"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for sub in ("", "computations", "reviews", "decisions", "inputs", "artifacts", "planner", "llm", "sandbox"):
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
        routing_version = None
        model_routing: dict[str, dict[str, Any]] = {}
        independence_dump = None
        if getattr(config, "routing", None) or getattr(config, "independence", None):
            from ai_lab.llm.config import (
                independence_policy_from_config,
                routing_policy_from_config,
            )

            policy = routing_policy_from_config(config)
            routing_version = policy.version
            model_routing = policy.public_routing_map()
            independence_dump = independence_policy_from_config(config).model_dump()
        sandbox_backend = None
        sandbox_policy_version = None
        sandbox_caps = None
        if getattr(config, "sandbox", None) is not None:
            from ai_lab.sandbox.factory import report_capabilities
            from ai_lab.sandbox.policy import sandbox_policy_from_config

            sbx_policy = sandbox_policy_from_config(config)
            sandbox_backend = sbx_policy.backend
            sandbox_policy_version = sbx_policy.version
            sandbox_caps = report_capabilities(config).model_dump()
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
                "python.execute": "compute-sandbox-v2.4c",
                "research.query": "v2.2-pipeline",
                "deterministic_verifier": "deterministic-verifier-v1",
                "platform": platform.platform(),
            },
            input_hashes=input_hashes,
            git_commit=_git_commit(repo_root),
            configuration_hash=_sha256_text(config_dump),
            budget=budget,
            notes=[],
            routing_policy_version=routing_version,
            model_routing=model_routing,
            independence_policy=independence_dump,
            sandbox_backend=sandbox_backend,
            sandbox_policy_version=sandbox_policy_version,
            sandbox_capabilities=sandbox_caps,
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

    def finish_manifest(
        self,
        *,
        final_state: str,
        budget: RunBudget | None = None,
        engineering_outcome: str | None = None,
        calculation_spec_ids: list[str] | None = None,
    ) -> RunManifest:
        """Persist end-of-run fields including live budget counters.

        Budget must be passed from LabRuntime — load_manifest alone would keep
        the start-of-run snapshot (tokens_used=0) and lose real LLM usage.
        """
        manifest = self.load_manifest()
        manifest.finished_at = datetime.now(timezone.utc)
        manifest.final_state = final_state
        if budget is not None:
            manifest.budget = budget
        if engineering_outcome is not None:
            manifest.engineering_outcome = engineering_outcome
        if calculation_spec_ids is not None:
            manifest.calculation_spec_ids = list(calculation_spec_ids)
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

    def attach_computation_contract(
        self,
        artifact_id: str,
        *,
        task_id: str | None = None,
        calculation_spec_id: str | None = None,
        objective: str | None = None,
        declared_outputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        claim_ids: list[str] | None = None,
        output_claim_ids: list[str] | None = None,
        kind: str | None = None,
    ) -> str:
        """Sidecar binding for CalculationSpec — does not mutate immutable artifact JSON.

        python.execute saves the artifact before the agent can attach contract fields;
        this sidecar is merged on load so relevance validation sees declared_outputs.
        """
        rel = self.rel("computations", f"{artifact_id}.contract.json")
        payload = {
            "artifact_id": artifact_id,
            "task_id": task_id,
            "calculation_spec_id": calculation_spec_id,
            "objective": objective,
            "declared_outputs": declared_outputs or {},
            "metadata": metadata or {},
            "claim_ids": claim_ids or [],
            "output_claim_ids": output_claim_ids or [],
            "kind": kind,
        }
        self.project.write_json(rel, payload)
        return rel

    def list_computations(self) -> list[ComputationArtifact]:
        folder = self.project.root / self.rel_root / "computations"
        if not folder.is_dir():
            return []
        out: list[ComputationArtifact] = []
        for path in sorted(folder.glob("*.json")):
            if path.name.endswith(".contract.json"):
                continue
            art = ComputationArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))
            contract_path = path.with_name(f"{art.artifact_id}.contract.json")
            if contract_path.is_file():
                contract = json.loads(contract_path.read_text(encoding="utf-8"))
                # Merge contract binding without rewriting immutable core fields.
                updates: dict[str, Any] = {}
                for key in (
                    "task_id",
                    "calculation_spec_id",
                    "objective",
                    "declared_outputs",
                    "claim_ids",
                    "output_claim_ids",
                ):
                    if contract.get(key) not in (None, {}, []):
                        updates[key] = contract[key]
                if contract.get("kind"):
                    updates["kind"] = contract["kind"]
                if contract.get("metadata"):
                    updates["metadata"] = {**art.metadata, **contract["metadata"]}
                if updates:
                    art = art.model_copy(update=updates)
            out.append(art)
        return out

    def save_text(self, name: str, content: str) -> str:
        """Write a run-scoped text artifact (canonical final_report.md lives here)."""
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"Illegal run-scoped text name: {name!r}")
        return self.project.write_text(self.rel(name), content)

    def save_review_json(self, name: str, data: Any) -> str:
        return self.project.write_json(self.rel("reviews", name), data)

    def load_review_json(self, name: str) -> Any | None:
        """Load a run-scoped review JSON, or None if missing."""
        path = self.project.root / self.rel("reviews", name)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_planner_json(self, name: str, data: Any) -> str:
        """Planner artifacts live only under this run's directory."""
        return self.project.write_json(self.rel("planner", name), data)

    def attach_task_graph(self, *, graph_id: str, graph_hash: str, version: int) -> RunManifest:
        manifest = self.load_manifest()
        manifest.task_graph_id = graph_id
        manifest.task_graph_hash = graph_hash
        manifest.task_graph_version = version
        self.save_manifest(manifest)
        return manifest

    def attach_planner(self, resolution: dict[str, Any]) -> RunManifest:
        """Record planner reliability provenance on the existing RunManifest."""
        manifest = self.load_manifest()
        manifest.planner = resolution
        self.save_manifest(manifest)
        return manifest

    def append_llm_invocation(self, payload: dict[str, Any]) -> str:
        """Append-only LLM provenance under this run (not a second log system)."""
        rel = self.rel("llm", "invocations.jsonl")
        full = self.project.root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, sort_keys=True, default=str) + "\n"
        with full.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return rel


def hash_code(code: str) -> str:
    return _sha256_text(code)
