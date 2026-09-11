"""Build the default tool registry for a project run."""

from __future__ import annotations

from ai_lab.core.models import LabConfig
from ai_lab.knowledge.research_factory import build_research_provider
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.tracing import RunEventSink
from ai_lab.sandbox.factory import sandbox_from_config
from ai_lab.tools.artifacts import ArtifactsTool
from ai_lab.tools.files import FilesTool
from ai_lab.tools.logging_tool import LoggingTool
from ai_lab.tools.python_exec import PythonExecTool
from ai_lab.tools.registry import ToolRegistry
from ai_lab.tools.research import ResearchTool


def build_tool_registry(
    store: ProjectStore,
    config: LabConfig,
    *,
    run_id: str,
    sink: RunEventSink,
    budget=None,
    knowledge=None,
    repo_root=None,
    run_store=None,
) -> ToolRegistry:
    registry = ToolRegistry(budget=budget)
    allowed = set(config.sandbox.get("allowed_modules") or [])
    policy, sandbox = sandbox_from_config(config, repo_root=repo_root)

    py = PythonExecTool(
        timeout_seconds=policy.timeout_s,
        max_output_bytes=policy.max_stdout_bytes,
        allowed_modules=allowed or None,
        sandbox=sandbox,
        policy=policy,
        run_store=run_store,
        sink=sink,
        run_id=run_id,
        repo_root=repo_root,
    )
    files = FilesTool(store)
    artifacts = ArtifactsTool(store)
    research_provider = build_research_provider(config, repo_root=repo_root)
    research = ResearchTool(
        provider=research_provider,
        knowledge=knowledge,
        run_id=run_id,
        budget=budget,
    )
    logging_tool = LoggingTool(sink, run_id)

    registry.register(py.as_spec())
    registry.register(files.read_spec())
    registry.register(files.write_spec())
    registry.register(artifacts.save_spec())
    registry.register(artifacts.load_spec())
    registry.register(research.as_spec())
    registry.register(logging_tool.as_spec())
    return registry
