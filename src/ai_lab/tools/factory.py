"""Build the default tool registry for a project run."""

from __future__ import annotations

from ai_lab.core.models import LabConfig
from ai_lab.memory.project_store import ProjectStore
from ai_lab.observability.tracing import RunEventSink
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
) -> ToolRegistry:
    registry = ToolRegistry(budget=budget)
    allowed = set(config.sandbox.get("allowed_modules") or [])
    timeout = float(config.sandbox.get("timeout_seconds", 10))
    max_out = int(config.sandbox.get("max_output_bytes", 200_000))

    py = PythonExecTool(
        timeout_seconds=timeout,
        max_output_bytes=max_out,
        allowed_modules=allowed or None,
    )
    files = FilesTool(store)
    artifacts = ArtifactsTool(store)
    research = ResearchTool()
    logging_tool = LoggingTool(sink, run_id)

    registry.register(py.as_spec())
    registry.register(files.read_spec())
    registry.register(files.write_spec())
    registry.register(artifacts.save_spec())
    registry.register(artifacts.load_spec())
    registry.register(research.as_spec())
    registry.register(logging_tool.as_spec())
    return registry
