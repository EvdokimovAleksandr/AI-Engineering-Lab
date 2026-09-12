"""Python.execute tool-policy tests (AST whitelist) plus sandbox timeout via the tool."""

import pytest

from ai_lab.core.enums import SandboxStatus
from ai_lab.tools.python_exec import (
    PythonExecTool,
    SandboxSyntaxError,
    SandboxViolation,
    coerce_sandbox_code,
    validate_imports,
)


def test_blocked_import() -> None:
    with pytest.raises(SandboxViolation, match="os"):
        validate_imports("import os\nprint(os.getcwd())", {"math"})


def test_allowed_math_import() -> None:
    validate_imports("import math\nprint(math.pi)", {"math"})


def test_coerce_dedents_module_snippet() -> None:
    raw = """
        import math
        print(int(math.pi))
    """
    assert coerce_sandbox_code(raw).startswith("import math")


def test_coerce_joins_list_of_lines() -> None:
    assert coerce_sandbox_code(["import math", "x = 1"]) == "import math\nx = 1"


def test_mixed_indent_is_syntax_error() -> None:
    src = "import math\nx = 1\n    y = 2\n"
    with pytest.raises(SandboxSyntaxError, match="unexpected indent"):
        validate_imports(src, {"math"})


@pytest.mark.asyncio
async def test_sandbox_executes_math() -> None:
    tool = PythonExecTool(timeout_seconds=5, allowed_modules={"math"})
    result = await tool.run(code="import math\nprint(int(math.pi))")
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "3"


@pytest.mark.asyncio
async def test_sandbox_runs_indented_llm_snippet() -> None:
    tool = PythonExecTool(timeout_seconds=5, allowed_modules={"math"})
    result = await tool.run(
        code="""
            import math
            print(int(math.pi))
        """
    )
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "3"


@pytest.mark.asyncio
async def test_sandbox_timeout() -> None:
    tool = PythonExecTool(timeout_seconds=0.3, allowed_modules=set())
    result = await tool.run(code="while True:\n    pass\n")
    assert result["timed_out"] is True
    assert result["sandbox_status"] == SandboxStatus.TIMEOUT.value
    assert result["process_alive_after_return"] is False


@pytest.mark.asyncio
async def test_simulation_syntax_error_is_opinion_not_crash(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed indent from the LLM must not abort the investigation run."""
    from ai_lab.agents.base import AgentContext
    from ai_lab.agents.simulation import SimulationAgent
    from ai_lab.core.enums import AgentRole, EvidenceKind, ProjectState
    from ai_lab.core.models import LabConfig, TaskSpec
    from ai_lab.llm.mock import MockProvider
    from ai_lab.memory.decision_log import DecisionLog
    from ai_lab.memory.evidence_store import EvidenceStore
    from ai_lab.memory.project_store import ProjectStore
    from ai_lab.observability.tracing import RunEventSink
    from ai_lab.tools.base import ToolSpec
    from ai_lab.tools.registry import ToolRegistry

    root = tmp_path / "proj"
    root.mkdir()
    store = ProjectStore(root)
    store.ensure_layout()

    async def fake_llm_json(*_a, **_k):
        return {
            "calculation_spec": {
                "objective": "height",
                "required_outputs": ["height_m"],
                "expected_dimensions": {"height_m": "m"},
                "domain": "geometry",
            },
            "code": "import math\nx = 1\n    y = 2\n",
            "declared_outputs": {},
            "claims": [{"statement": "height is 4 m"}],
        }

    monkeypatch.setattr("ai_lab.agents.simulation.llm_json", fake_llm_json)

    async def save(*, path: str, data: object, **_kw: object) -> dict:
        return {"path": path, "saved": True}

    tools = ToolRegistry()
    tools.register(PythonExecTool(timeout_seconds=5, allowed_modules={"math"}).as_spec())
    tools.register(ToolSpec(name="artifacts.save", description="a", handler=save))
    ctx = AgentContext(
        run_id="run_syntax",
        store=store,
        evidence=EvidenceStore(store, run_id="run_syntax"),
        decisions=DecisionLog(store.root / "decisions" / "decision_log.jsonl"),
        tools=tools,
        llm=MockProvider(),
        config=LabConfig(provider="mock"),
        sink=RunEventSink(store.root / ".runs" / "run_syntax" / "events.jsonl"),
    )
    task = TaskSpec(
        task_id="calculation",
        role=AgentRole.SIMULATION,
        objective="height",
        output_schema="computation",
        state_context=ProjectState.CALCULATION,
        allowed_tools=["python.execute", "artifacts.save"],
    )
    result = await SimulationAgent().run(task, ctx)
    assert result.raw.get("exec") is None
    assert "syntax error" in (result.raw.get("contract_error") or "").lower()
    assert result.claims
    assert all(c.kind == EvidenceKind.OPINION for c in result.claims)
    assert "not valid Python" in result.claims[0].statement
