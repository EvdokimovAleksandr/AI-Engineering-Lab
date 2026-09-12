"""V2.4b compute sandbox: process isolation, policy, reproducibility, honest capabilities."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_lab.cli import main
from ai_lab.core.enums import EnforcementLevel, NetworkPolicy, ReproductionVerdict, SandboxStatus
from ai_lab.core.models import ComputationArtifact, LabConfig, RunBudget
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.run_store import RunStore
from ai_lab.orchestrator.budget import BudgetExceeded, record_tool_call
from ai_lab.sandbox.compare import compare_computations
from ai_lab.sandbox.docker import DockerSandbox
from ai_lab.sandbox.errors import SandboxSecurityPolicyError, SandboxUnsupportedError, SandboxValidationError
from ai_lab.sandbox.factory import sandbox_from_config
from ai_lab.sandbox.hashing import hashes_for_spec, python_version_fingerprint
from ai_lab.sandbox.local import LocalSubprocessSandbox
from ai_lab.sandbox.models import ComputeSpec, SandboxContext, SandboxPolicy
from ai_lab.sandbox.policy import bind_spec_to_policy, sandbox_policy_from_config
from ai_lab.sandbox.windows_job import pid_is_alive
from ai_lab.tools.python_exec import PythonExecTool, SandboxViolation, validate_imports
from ai_lab.tools.registry import ToolRegistry


REPO = Path(__file__).resolve().parents[1]


def _policy(**kwargs: object) -> SandboxPolicy:
    return SandboxPolicy(**kwargs)  # type: ignore[arg-type]


def _sandbox(tmp_path: Path, **policy_kw: object) -> tuple[LocalSubprocessSandbox, Path]:
    policy = _policy(**policy_kw)
    parent = tmp_path / "sandbox"
    parent.mkdir(parents=True, exist_ok=True)
    return LocalSubprocessSandbox(policy, repo_root=REPO), parent


def _ctx(tmp_path: Path, parent: Path, *, task_id: str = "t1", run_id: str = "run_sbx") -> SandboxContext:
    _ = tmp_path
    return SandboxContext(run_id=run_id, task_id=task_id, workspace_parent=parent)


# --- Specification ---


def test_valid_compute_spec() -> None:
    spec = ComputeSpec(code="print(1)", inputs={"x": 1}, timeout_s=2)
    assert spec.network == NetworkPolicy.DENY
    assert spec.working_dir_policy == "sandbox_workspace"


def test_invalid_spec_rejects_host_control() -> None:
    with pytest.raises(ValidationError):
        ComputeSpec.model_validate({"code": "print(1)", "cwd": "/tmp"})
    with pytest.raises(ValidationError):
        ComputeSpec.model_validate({"code": "print(1)", "command": "python"})
    with pytest.raises(ValidationError):
        ComputeSpec(code="print(1)", metadata={"docker_args": "--privileged"})
    with pytest.raises(ValidationError):
        ComputeSpec.model_validate({"code": "print(1)", "image": "evil"})
    with pytest.raises(ValidationError):
        ComputeSpec.model_validate({"code": "print(1)", "privileged": True})


def test_invalid_timeout_and_memory() -> None:
    with pytest.raises(ValidationError):
        ComputeSpec(code="print(1)", timeout_s=0)
    with pytest.raises(ValidationError):
        ComputeSpec(code="print(1)", timeout_s=-1)
    with pytest.raises(ValidationError):
        ComputeSpec(code="print(1)", memory_mb=0)
    with pytest.raises(ValidationError):
        ComputeSpec(code="print(1)", max_stdout_bytes=0)


def test_policy_escalation_rejected() -> None:
    policy = _policy(timeout_s=5, memory_mb=64, max_stdout_bytes=1000, network=NetworkPolicy.DENY)
    spec = ComputeSpec(code="print(1)", timeout_s=5, memory_mb=64, max_stdout_bytes=1000)
    with pytest.raises(SandboxSecurityPolicyError, match="timeout_s"):
        bind_spec_to_policy(spec, policy, requested_overrides={"timeout_s": 30})
    with pytest.raises(SandboxSecurityPolicyError, match="memory_mb"):
        bind_spec_to_policy(spec, policy, requested_overrides={"memory_mb": 512})
    with pytest.raises(SandboxSecurityPolicyError, match="network"):
        bind_spec_to_policy(spec, policy, requested_overrides={"network": "allow"})
    with pytest.raises(SandboxSecurityPolicyError, match="max_stdout"):
        bind_spec_to_policy(spec, policy, requested_overrides={"max_stdout_bytes": 10_000})


@pytest.mark.asyncio
async def test_python_execute_rejects_cwd_kwarg() -> None:
    tool = PythonExecTool(timeout_seconds=3, allowed_modules=set())
    with pytest.raises(SandboxSecurityPolicyError, match="host-control"):
        await tool.run(code="print(1)", cwd="C:/Windows")


# --- Process ---


@pytest.mark.asyncio
async def test_normal_execution(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=5)
    spec = ComputeSpec(code="print(2+2)", timeout_s=5)
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.SUCCESS
    assert result.exit_code == 0
    assert result.stdout.strip() == "4"
    assert result.artifact is not None
    assert result.artifact.computation_hash != "unknown"
    assert not result.process_alive_after_return
    assert not pid_is_alive(result.resource_usage.pid)


@pytest.mark.asyncio
async def test_nonzero_exit_and_stderr(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=5)
    spec = ComputeSpec(code="import sys\nprint('boom', file=sys.stderr)\nsys.exit(7)", timeout_s=5)
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.NONZERO_EXIT
    assert result.exit_code == 7
    assert "boom" in result.stderr


@pytest.mark.asyncio
async def test_timeout_kills_process(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=0.4)
    spec = ComputeSpec(code="while True:\n    pass\n", timeout_s=0.4)
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.TIMEOUT
    assert result.timed_out
    assert result.artifact is not None
    assert result.artifact.sandbox_status == SandboxStatus.TIMEOUT.value
    assert not result.process_alive_after_return
    assert not pid_is_alive(result.resource_usage.pid)


@pytest.mark.asyncio
async def test_timeout_kills_grandchild_when_job_attached(tmp_path: Path) -> None:
    import asyncio
    import time

    sandbox, parent = _sandbox(tmp_path, timeout_s=1.5, max_processes=8)
    spec = ComputeSpec(
        code=(
            "import subprocess, sys, os, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "print('CHILD', p.pid, flush=True)\n"
            "time.sleep(0.2)\n"
            "while True:\n"
            "    pass\n"
        ),
        timeout_s=1.5,
    )
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.TIMEOUT
    assert not pid_is_alive(result.resource_usage.pid)
    child_pid = None
    for line in result.stdout.splitlines():
        if line.startswith("CHILD "):
            child_pid = int(line.split()[1])
    if result.security_metadata.get("job_object_attached"):
        assert child_pid is not None
        # Process-table lag after TerminateJobObject — poll, do not skip the guarantee.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pid_is_alive(child_pid):
            await asyncio.sleep(0.05)
        assert not pid_is_alive(child_pid), (
            f"grandchild pid={child_pid} still alive after job terminate "
            "(security guarantee not met)"
        )
    else:
        assert result.enforcement["process_tree_kill"] != EnforcementLevel.HARD.value


# --- Filesystem ---


@pytest.mark.asyncio
async def test_workspace_write_and_outside_denied(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=5)
    env_path = tmp_path / ".env"
    env_path.write_text("SECRET=1", encoding="utf-8")
    other_run = tmp_path / ".runs" / "run_other" / "x.txt"
    other_run.parent.mkdir(parents=True)
    other_run.write_text("nope", encoding="utf-8")
    spec = ComputeSpec(
        code=(
            "from pathlib import Path\n"
            "Path('local_result.txt').write_text('ok', encoding='utf-8')\n"
            "print('WROTE')\n"
            f"p = Path(r'{env_path}')\n"
            "try:\n"
            "    print(p.read_text())\n"
            "    print('ENV_LEAK')\n"
            "except PermissionError:\n"
            "    print('ENV_DENIED')\n"
            f"q = Path(r'{other_run}')\n"
            "try:\n"
            "    print(q.read_text())\n"
            "    print('RUN_LEAK')\n"
            "except PermissionError:\n"
            "    print('RUN_DENIED')\n"
        ),
        timeout_s=5,
    )
    result = await sandbox.execute(spec, _ctx(tmp_path, parent, task_id="fs1"))
    workspace = Path(result.security_metadata["workspace"])
    assert (workspace / "local_result.txt").read_text(encoding="utf-8") == "ok"
    assert "WROTE" in result.stdout
    assert result.enforcement["filesystem_jail"] == EnforcementLevel.BEST_EFFORT.value
    # Guard is BEST_EFFORT (builtins.open). Path.read_text typically uses it.
    assert "ENV_DENIED" in result.stdout
    assert "ENV_LEAK" not in result.stdout
    assert "RUN_DENIED" in result.stdout
    assert "RUN_LEAK" not in result.stdout


# --- Environment ---


@pytest.mark.asyncio
async def test_secret_env_stripped_allowlist_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SECRET", "super-secret-value")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-secret")
    sandbox, parent = _sandbox(tmp_path, timeout_s=5)
    spec = ComputeSpec(
        code=(
            "import os\n"
            "print('SECRET', os.environ.get('TEST_SECRET'))\n"
            "print('CURSOR', os.environ.get('CURSOR_API_KEY'))\n"
            "print('PATH_SET', bool(os.environ.get('PATH')))\n"
            "print('SANDBOX', os.environ.get('AI_LAB_SANDBOX'))\n"
        ),
        timeout_s=5,
    )
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.SUCCESS
    assert "SECRET None" in result.stdout
    assert "CURSOR None" in result.stdout
    assert "PATH_SET True" in result.stdout
    assert "SANDBOX 1" in result.stdout
    assert result.enforcement["environment_isolation"] == EnforcementLevel.HARD.value


# --- Output caps ---


@pytest.mark.asyncio
async def test_stdout_cap_kills_process(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=8, max_stdout_bytes=2048, max_stderr_bytes=2048)
    spec = ComputeSpec(
        code="print('A' * 2_000_000)",
        timeout_s=8,
        max_stdout_bytes=2048,
        max_stderr_bytes=2048,
    )
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.OUTPUT_LIMIT
    assert len(result.stdout.encode("utf-8")) <= 2048 + 16
    assert not result.process_alive_after_return
    assert not pid_is_alive(result.resource_usage.pid)


@pytest.mark.asyncio
async def test_stderr_cap(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=8, max_stdout_bytes=2048, max_stderr_bytes=512)
    spec = ComputeSpec(
        code="import sys\nsys.stderr.write('B' * 2_000_000)\n",
        timeout_s=8,
        max_stdout_bytes=2048,
        max_stderr_bytes=512,
    )
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.status == SandboxStatus.OUTPUT_LIMIT
    assert len(result.stderr.encode("utf-8")) <= 512 + 16
    assert not pid_is_alive(result.resource_usage.pid)


# --- Reproducibility ---


def test_computation_identity_same_and_different() -> None:
    policy_a = _policy(timeout_s=5, memory_mb=128)
    policy_b = _policy(timeout_s=9, memory_mb=128)
    py = python_version_fingerprint()
    common = dict(
        python_version=py,
        platform_name="test-os",
        dependency_hash="dep1",
        runner_hash="runner1",
    )
    spec1 = ComputeSpec(code="print(1)", inputs={"n": 1}, timeout_s=5, memory_mb=128)
    spec2 = ComputeSpec(code="print(1)", inputs={"n": 1}, timeout_s=5, memory_mb=128)
    spec3 = ComputeSpec(code="print(1)", inputs={"n": 2}, timeout_s=5, memory_mb=128)
    h1 = hashes_for_spec(spec1, policy_a, **common)
    h2 = hashes_for_spec(spec2, policy_a, **common)
    h3 = hashes_for_spec(spec3, policy_a, **common)
    h4 = hashes_for_spec(spec1, policy_b, **common)
    assert h1["computation_hash"] == h2["computation_hash"]
    assert h1["code_hash"] == h3["code_hash"]
    assert h1["computation_hash"] != h3["computation_hash"]
    assert h1["computation_hash"] != h4["computation_hash"]


def test_compare_computations_match_and_mismatch() -> None:
    base = dict(
        run_id="r",
        code_hash="c",
        input_hash="i",
        environment_hash="e",
        python_version="3.11.0",
        dependency_hash="d",
        sandbox_policy_version="v2.4b-1",
        sandbox_backend="local_subprocess",
        computation_hash="h",
        stdout_hash="o",
        stderr_hash="s",
        returncode=0,
        sandbox_status="SUCCESS",
    )
    a = ComputationArtifact(**base)
    b = ComputationArtifact(**base)
    assert compare_computations(a, b).verdict == ReproductionVerdict.REPRODUCTION_MATCH
    c = ComputationArtifact(**{**base, "stdout_hash": "other"})
    assert compare_computations(a, c).verdict == ReproductionVerdict.REPRODUCTION_MISMATCH
    d = ComputationArtifact(**{**base, "input_hash": "i2", "computation_hash": "h2"})
    assert compare_computations(a, d).verdict == ReproductionVerdict.IDENTITY_DIFFERENT


# --- Capabilities / network honesty ---


def test_windows_capability_report_is_honest() -> None:
    sandbox = LocalSubprocessSandbox(_policy())
    caps = sandbox.capabilities()
    assert caps.timeout == EnforcementLevel.HARD
    assert caps.process_kill == EnforcementLevel.HARD
    assert caps.output_limit == EnforcementLevel.HARD
    assert caps.environment_isolation == EnforcementLevel.HARD
    assert caps.network_deny == EnforcementLevel.UNSUPPORTED
    assert caps.filesystem_jail == EnforcementLevel.BEST_EFFORT
    if sys.platform == "win32":
        assert caps.job_objects in {EnforcementLevel.HARD, EnforcementLevel.UNSUPPORTED}
        if caps.job_objects == EnforcementLevel.HARD:
            assert caps.memory_limit == EnforcementLevel.HARD
            assert caps.cpu_limit == EnforcementLevel.HARD
            assert caps.process_tree_kill == EnforcementLevel.HARD
        else:
            assert caps.memory_limit == EnforcementLevel.UNSUPPORTED
            assert caps.cpu_limit == EnforcementLevel.UNSUPPORTED
    else:
        assert caps.job_objects == EnforcementLevel.UNSUPPORTED


@pytest.mark.asyncio
async def test_network_policy_requested_not_fake_hard_deny(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=5)
    spec = ComputeSpec(
        code="print('policy', 'deny')\n",
        timeout_s=5,
        network=NetworkPolicy.DENY,
    )
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert spec.network == NetworkPolicy.DENY
    assert result.security_metadata["network_policy"] == "deny"
    assert result.enforcement["network_deny"] == EnforcementLevel.UNSUPPORTED.value
    # Не утверждаем, что urllib был реально заблокирован OS — isolation не HARD.


@pytest.mark.asyncio
async def test_memory_enforcement_metadata_not_faked(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=5, memory_mb=64)
    spec = ComputeSpec(code="print('ok')", timeout_s=5, memory_mb=64)
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    level = EnforcementLevel(result.enforcement["memory_limit"])
    assert level in {EnforcementLevel.HARD, EnforcementLevel.UNSUPPORTED}
    if not result.security_metadata.get("job_object_attached") and sys.platform == "win32":
        assert level != EnforcementLevel.HARD


@pytest.mark.asyncio
async def test_process_spawn_enforcement_classified(tmp_path: Path) -> None:
    sandbox, parent = _sandbox(tmp_path, timeout_s=5)
    spec = ComputeSpec(code="print('spawn-meta')", timeout_s=5, max_processes=2)
    result = await sandbox.execute(spec, _ctx(tmp_path, parent))
    assert result.enforcement["max_processes"] in {
        EnforcementLevel.HARD.value,
        EnforcementLevel.UNSUPPORTED.value,
    }


# --- ToolRegistry / RunBudget ---


@pytest.mark.asyncio
async def test_python_execute_one_tool_call_not_two(tmp_path: Path) -> None:
    budget = RunBudget(max_tool_calls=1, max_agent_calls=10)
    registry = ToolRegistry(budget=budget)
    tool = PythonExecTool(timeout_seconds=5, allowed_modules=set())
    registry.register(tool.as_spec())
    spec = tool.as_spec()
    assert spec.sandbox_required is True
    assert spec.network == "deny"
    out = await registry.call("python.execute", code="print('hi')")
    assert out["stdout"].strip() == "hi"
    assert budget.tool_calls == 1
    with pytest.raises(BudgetExceeded):
        record_tool_call(budget)


@pytest.mark.asyncio
async def test_timeout_via_tool_returns_status_not_raise() -> None:
    tool = PythonExecTool(timeout_seconds=0.4, allowed_modules=set())
    out = await tool.run(code="while True:\n    pass\n")
    assert out["timed_out"] is True
    assert out["sandbox_status"] == SandboxStatus.TIMEOUT.value
    assert out["process_alive_after_return"] is False
    pid = (out.get("security_metadata") or {}).get("workspace")
    _ = pid
    art = ComputationArtifact.model_validate(out["artifact"])
    assert art.sandbox_status == SandboxStatus.TIMEOUT.value


# --- Provenance ---


@pytest.mark.asyncio
async def test_artifact_saved_and_manifest_sandbox_fields(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    (proj / "problem.md").write_text("x", encoding="utf-8")
    rs = RunStore(store, "run_prov")
    config = LabConfig(provider="mock", sandbox={"backend": "local_subprocess", "timeout_seconds": 5})
    man = rs.build_manifest(config=config, repo_root=REPO, budget=None)
    assert man.sandbox_backend == "local_subprocess"
    assert man.sandbox_policy_version
    assert man.sandbox_capabilities is not None
    assert man.sandbox_capabilities["timeout"] == "HARD"

    from ai_lab.observability.tracing import RunEventSink

    sink = RunEventSink(proj / ".runs" / "run_prov" / "events.jsonl")
    tool = PythonExecTool(
        timeout_seconds=5,
        allowed_modules=set(),
        run_store=rs,
        sink=sink,
        run_id="run_prov",
        repo_root=REPO,
    )
    out = await tool.run(code="print(42)", task_id="task_sim")
    assert out["artifact_saved"] is True
    arts = rs.list_computations()
    assert len(arts) == 1
    assert arts[0].computation_hash == out["computation_hash"]
    events = sink.read_all()
    assert any(e.tool_name == "python.execute" for e in events)
    ev = next(e for e in events if e.tool_name == "python.execute")
    assert ev.data["code_hash"]
    assert ev.data["computation_hash"]
    assert ev.task_id == "task_sim"
    assert "SECRET" not in str(ev.data)


# --- Docker backend (no silent fallback; implementation lives in test_docker_*) ---


@pytest.mark.asyncio
async def test_docker_backend_no_local_fallback(tmp_path: Path) -> None:
    from ai_lab.sandbox.docker_discover import DockerDiscovery
    from ai_lab.sandbox.models import DockerRuntimeConfig

    policy = _policy(backend="docker", docker=DockerRuntimeConfig(image="python:3.12-slim"))
    docker = DockerSandbox(
        policy,
        discovery=DockerDiscovery(installed=False, reason="docker binary not found"),
    )
    assert docker.name == "docker"
    with pytest.raises(SandboxUnsupportedError, match="unavailable|not found"):
        await docker.execute(
            ComputeSpec(code="print(1)"),
            _ctx(tmp_path, tmp_path),
        )


def test_unknown_backend_fails_loud() -> None:
    with pytest.raises(SandboxValidationError, match="Unknown sandbox backend"):
        sandbox_policy_from_config(LabConfig(provider="mock", sandbox={"backend": "k8s"}))


def test_factory_local_backend() -> None:
    policy, sandbox = sandbox_from_config(
        LabConfig(provider="mock", sandbox={"backend": "local_subprocess"}),
        repo_root=REPO,
    )
    assert policy.backend == "local_subprocess"
    assert isinstance(sandbox, LocalSubprocessSandbox)


def test_factory_docker_unavailable_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_lab.sandbox.factory import build_compute_sandbox
    from ai_lab.sandbox.models import DockerRuntimeConfig

    def boom(self):  # noqa: ANN001
        raise SandboxUnsupportedError("Docker daemon unavailable: test stub")

    monkeypatch.setattr(DockerSandbox, "require_available", boom)
    with pytest.raises(SandboxUnsupportedError, match="unavailable"):
        sandbox_from_config(
            LabConfig(
                provider="mock",
                sandbox={"backend": "docker", "docker": {"image": "python:3.12-slim"}},
            ),
            repo_root=REPO,
        )
    sandbox = build_compute_sandbox(
        _policy(backend="docker", docker=DockerRuntimeConfig()),
        require_available=False,
    )
    assert isinstance(sandbox, DockerSandbox)
    assert not isinstance(sandbox, LocalSubprocessSandbox)


def test_fallback_backend_rejected() -> None:
    with pytest.raises(SandboxValidationError, match="fallback"):
        sandbox_policy_from_config(
            LabConfig(
                provider="mock",
                sandbox={"backend": "docker", "fallback_backend": "local_subprocess", "docker": {"image": "python:3.12-slim"}},
            )
        )


# --- CLI ---


def test_cli_sandbox_capabilities(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["sandbox"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Backend: local_subprocess" in out
    assert "Backend: docker" in out
    assert "Timeout: HARD" in out
    assert "Network isolation: UNSUPPORTED" in out
    assert "not a fully secure container" in out.lower() or "not a fully secure" in out
    assert "container-isolated compute" in out.lower()
    assert "Docker:" in out
    assert "installed:" in out


# --- Existing tool policy still blocks os import for agents ---


def test_tool_policy_still_blocks_os_import() -> None:
    with pytest.raises(SandboxViolation, match="os"):
        validate_imports("import os\nprint(os.getcwd())", {"math"})


@pytest.mark.asyncio
async def test_taskgraph_python_execute_goes_through_sandbox(tmp_path: Path) -> None:
    """TaskGraph intent → ToolRegistry → python.execute → ComputeSandbox → artifact."""
    from ai_lab.observability.tracing import RunEventSink
    from ai_lab.planner.static import default_pipeline_tasks
    from ai_lab.tools.factory import build_tool_registry

    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    rs = RunStore(store, "run_tg")
    config = LabConfig(
        provider="mock",
        sandbox={"backend": "local_subprocess", "timeout_seconds": 5, "allowed_modules": ["math"]},
    )
    sink = RunEventSink(proj / ".runs" / "run_tg" / "events.jsonl")
    tools = build_tool_registry(
        store, config, run_id="run_tg", sink=sink, run_store=rs, repo_root=REPO
    )
    sim_tasks = [t for t in default_pipeline_tasks() if t.output_schema == "computation"]
    assert sim_tasks
    out = await tools.call(
        "python.execute",
        allowed=["python.execute"],
        code="print(1)",
        task_id=sim_tasks[0].task_id,
    )
    assert out["sandbox_status"] == SandboxStatus.SUCCESS.value
    assert out["artifact_id"]
    assert rs.list_computations()
    assert out["computation_hash"]
