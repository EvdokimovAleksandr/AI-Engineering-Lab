"""Python.execute tool-policy tests (AST whitelist) plus sandbox timeout via the tool."""

import pytest

from ai_lab.core.enums import SandboxStatus
from ai_lab.tools.python_exec import PythonExecTool, SandboxViolation, validate_imports


def test_blocked_import() -> None:
    with pytest.raises(SandboxViolation, match="os"):
        validate_imports("import os\nprint(os.getcwd())", {"math"})


def test_allowed_math_import() -> None:
    validate_imports("import math\nprint(math.pi)", {"math"})


@pytest.mark.asyncio
async def test_sandbox_executes_math() -> None:
    tool = PythonExecTool(timeout_seconds=5, allowed_modules={"math"})
    result = await tool.run(code="import math\nprint(int(math.pi))")
    assert result["returncode"] == 0
    assert result["stdout"].strip() == "3"


@pytest.mark.asyncio
async def test_sandbox_timeout() -> None:
    tool = PythonExecTool(timeout_seconds=0.3, allowed_modules=set())
    result = await tool.run(code="while True:\n    pass\n")
    assert result["timed_out"] is True
    assert result["sandbox_status"] == SandboxStatus.TIMEOUT.value
    assert result["process_alive_after_return"] is False
