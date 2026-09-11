"""DockerSandbox unit + optional integration tests.

Command/policy/availability tests always run.
Integration tests skip explicitly when Docker engine or image is missing —
skip is not a silent PASS of Docker functionality.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_lab.core.enums import EnforcementLevel, ReproductionVerdict, SandboxStatus
from ai_lab.core.models import LabConfig
from ai_lab.memory.project_store import ProjectStore
from ai_lab.memory.run_store import RunStore
from ai_lab.sandbox.compare import compare_computations
from ai_lab.sandbox.docker import DockerSandbox
from ai_lab.sandbox.docker_discover import DockerDiscovery, detect_docker_capabilities
from ai_lab.sandbox.errors import DockerImageUnavailable, SandboxUnsupportedError, SandboxValidationError
from ai_lab.sandbox.factory import build_compute_sandbox
from ai_lab.sandbox.hashing import hashes_for_spec
from ai_lab.sandbox.models import ComputeSpec, DockerRuntimeConfig, SandboxContext, SandboxPolicy
from ai_lab.sandbox.policy import sandbox_policy_from_config

REPO = Path(__file__).resolve().parents[1]


def _docker_policy(**kwargs: object) -> SandboxPolicy:
    docker = kwargs.pop("docker", DockerRuntimeConfig(image="python:3.12-slim", cpus=0.5))
    return SandboxPolicy(backend="docker", docker=docker, **kwargs)  # type: ignore[arg-type]


def _ctx(parent: Path, *, run_id: str = "run_docker", task_id: str = "t1") -> SandboxContext:
    return SandboxContext(run_id=run_id, task_id=task_id, workspace_parent=parent)


def _unavailable() -> DockerDiscovery:
    return DockerDiscovery(installed=False, reason="docker binary not found")


def _help_ok_runner(*, info_rc: int = 1, info_err: str = "Cannot connect to the Docker daemon", inspect_rc: int = 1):
    class FakeRunner:
        binary = "docker"

        def invoke(self, args, **kwargs):  # noqa: ANN001
            import subprocess

            help_txt = (
                "--network --read-only --cap-drop --security-opt --memory "
                "--cpus --pids-limit --user --tmpfs --mount --rm --pull --init"
            )
            if args[:2] == ["run", "--help"]:
                return subprocess.CompletedProcess(args, 0, stdout=help_txt, stderr="")
            if args[0] == "info":
                stdout = "26.0.0|linux" if info_rc == 0 else ""
                return subprocess.CompletedProcess(args, info_rc, stdout=stdout, stderr=info_err)
            if args[0] == "image":
                return subprocess.CompletedProcess(args, inspect_rc, stdout="", stderr="No such image")
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    return FakeRunner()


def test_detect_docker_absent_is_not_backend_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ai_lab.sandbox.docker_discover.shutil.which", lambda _name: None)
    disc = detect_docker_capabilities(check_image=False)
    assert disc.installed is False
    assert disc.daemon_available is False
    assert disc.backend_available is False
    assert "not found" in disc.reason


def test_detect_daemon_absent() -> None:
    disc = detect_docker_capabilities(check_image=False, runner=_help_ok_runner())  # type: ignore[arg-type]
    assert disc.installed is True
    assert disc.daemon_available is False
    assert disc.backend_available is False


def test_detect_image_absent() -> None:
    disc = detect_docker_capabilities(
        docker_cfg=DockerRuntimeConfig(image="python:3.12-slim"),
        check_image=True,
        runner=_help_ok_runner(info_rc=0, info_err=""),  # type: ignore[arg-type]
    )
    assert disc.daemon_available is True
    assert disc.image_available is False
    assert disc.backend_available is False


@pytest.mark.asyncio
async def test_execute_without_docker_fails_loud(tmp_path: Path) -> None:
    sandbox = DockerSandbox(_docker_policy(), discovery=_unavailable())
    parent = tmp_path / "sandbox"
    parent.mkdir()
    with pytest.raises(SandboxUnsupportedError):
        await sandbox.execute(ComputeSpec(code="print(1)"), _ctx(parent))


@pytest.mark.asyncio
async def test_missing_image_is_structured_error(tmp_path: Path) -> None:
    disc = DockerDiscovery(
        installed=True,
        docker_bin="docker",
        daemon_available=True,
        platform_supported=True,
        image_available=False,
        backend_available=False,
        reason="image not available locally (no docker pull): python:3.12-slim",
        flags_supported={k: True for k in (
            "--network", "--read-only", "--cap-drop", "--security-opt", "--memory",
            "--cpus", "--pids-limit", "--user", "--tmpfs", "--mount", "--rm", "--pull", "--init",
        )},
    )
    sandbox = DockerSandbox(_docker_policy(), discovery=disc)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    with pytest.raises((DockerImageUnavailable, SandboxUnsupportedError)):
        await sandbox.execute(ComputeSpec(code="print(1)"), _ctx(parent))


def test_capabilities_without_daemon_are_not_hard() -> None:
    sandbox = DockerSandbox(_docker_policy(), discovery=_unavailable())
    caps = sandbox.capabilities()
    assert caps.backend == "docker"
    assert caps.network_deny == EnforcementLevel.UNSUPPORTED
    assert caps.filesystem_jail == EnforcementLevel.UNSUPPORTED
    assert caps.memory_limit == EnforcementLevel.UNSUPPORTED
    assert caps.timeout == EnforcementLevel.UNSUPPORTED


def test_same_identity_with_digest() -> None:
    policy = _docker_policy()
    spec = ComputeSpec(code="print(1)", inputs={"n": 1}, memory_mb=256)
    common = dict(
        python_version="3.12.0",
        platform_name="docker:linux",
        dependency_hash="dep",
        runner_hash="runner",
        image_digest="sha256:" + "ab" * 32,
    )
    h1 = hashes_for_spec(spec, policy, **common)
    h2 = hashes_for_spec(spec, policy, **common)
    assert h1["computation_hash"] == h2["computation_hash"]
    h3 = hashes_for_spec(spec, policy, **{**common, "image_digest": "sha256:" + "cd" * 32})
    assert h1["computation_hash"] != h3["computation_hash"]


def test_partial_environment_not_reproduction_match() -> None:
    from ai_lab.core.models import ComputationArtifact

    base = dict(
        run_id="r",
        code_hash="c",
        input_hash="i",
        environment_hash="e",
        python_version="3.12.0",
        dependency_hash="d",
        sandbox_policy_version="v2.4c-1",
        sandbox_backend="docker",
        computation_hash="h",
        stdout_hash="o",
        stderr_hash="s",
        returncode=0,
        sandbox_status="SUCCESS",
        environment_reproducibility="partial",
        image_digest=None,
    )
    a = ComputationArtifact(**base)
    b = ComputationArtifact(**base)
    report = compare_computations(a, b)
    assert report.identity_match
    assert report.output_match
    assert report.verdict == ReproductionVerdict.PARTIAL_ENVIRONMENT


def test_cross_run_workspace_paths_differ(tmp_path: Path) -> None:
    from ai_lab.sandbox.workspace import prepare_workspace

    a = prepare_workspace(_ctx(tmp_path / "runA" / "sandbox", run_id="runA", task_id="task1"))
    b = prepare_workspace(_ctx(tmp_path / "runB" / "sandbox", run_id="runB", task_id="task1"))
    assert a != b
    assert a.parent != b.parent
    (a / "out.txt").write_text("A", encoding="utf-8")
    assert not (b / "out.txt").exists()


def test_docker_policy_from_config_loads_image() -> None:
    policy = sandbox_policy_from_config(
        LabConfig(
            provider="mock",
            sandbox={"backend": "docker", "docker": {"image": "python:3.12-slim", "cpus": 1.0}},
        )
    )
    assert policy.backend == "docker"
    assert policy.docker is not None
    assert policy.docker.image == "python:3.12-slim"
    assert policy.fallback_backend == "none"


def test_docker_config_rejects_privileged_key() -> None:
    with pytest.raises(SandboxValidationError, match="forbids"):
        sandbox_policy_from_config(
            LabConfig(
                provider="mock",
                sandbox={"backend": "docker", "docker": {"image": "python:3.12-slim", "privileged": True}},
            )
        )


def _skip_without_docker() -> DockerDiscovery:
    policy = sandbox_policy_from_config(
        LabConfig(provider="mock", sandbox={"backend": "local_subprocess", "docker": {"image": "python:3.12-slim"}})
    )
    disc = detect_docker_capabilities(docker_cfg=policy.docker, check_image=True)
    if not disc.backend_available:
        pytest.skip(f"Docker integration skipped: {disc.reason}")
    return disc


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_python_executes(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=20), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    result = await sandbox.execute(ComputeSpec(code="print(2+2)", timeout_s=20), _ctx(parent))
    assert result.status == SandboxStatus.SUCCESS
    assert result.stdout.strip() == "4"
    assert result.artifact is not None
    assert result.artifact.sandbox_backend == "docker"
    assert result.enforcement["network_deny"] == EnforcementLevel.HARD.value
    assert result.enforcement["filesystem_jail"] == EnforcementLevel.HARD.value
    assert not result.process_alive_after_return
    assert result.security_metadata.get("container_reaped") is True


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_network_denied(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=20), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    code = (
        "import socket\n"
        "try:\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(2)\n"
        "    s.connect(('1.1.1.1', 443))\n"
        "    print('NETWORK_OPEN')\n"
        "except Exception as e:\n"
        "    print('NETWORK_UNAVAILABLE', type(e).__name__)\n"
        "try:\n"
        "    import urllib.request\n"
        "    urllib.request.urlopen('https://example.com', timeout=2)\n"
        "    print('HTTP_OPEN')\n"
        "except Exception as e:\n"
        "    print('HTTP_UNAVAILABLE', type(e).__name__)\n"
    )
    result = await sandbox.execute(ComputeSpec(code=code, timeout_s=20), _ctx(parent, task_id="net"))
    assert "NETWORK_UNAVAILABLE" in result.stdout
    assert "NETWORK_OPEN" not in result.stdout
    assert "HTTP_OPEN" not in result.stdout
    assert result.enforcement["network_deny"] == EnforcementLevel.HARD.value


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_filesystem_workspace_only(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=20), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    host_secret = tmp_path / "host_secret.txt"
    host_secret.write_text("SECRET", encoding="utf-8")
    code = (
        "from pathlib import Path\n"
        "Path('output.txt').write_text('ok', encoding='utf-8')\n"
        "print('WROTE')\n"
        "try:\n"
        "    Path('/etc/shadow').read_text()\n"
        "    print('SHADOW_LEAK')\n"
        "except Exception as e:\n"
        "    print('SHADOW_DENIED', type(e).__name__)\n"
        f"p = Path(r'{host_secret}')\n"
        "print('HOST_EXISTS_IN_CONTAINER', p.exists())\n"
    )
    result = await sandbox.execute(ComputeSpec(code=code, timeout_s=20), _ctx(parent, task_id="fs"))
    workspace = Path(result.security_metadata["workspace"])
    assert (workspace / "output.txt").read_text(encoding="utf-8") == "ok"
    assert "WROTE" in result.stdout
    assert "SHADOW_LEAK" not in result.stdout
    assert "HOST_EXISTS_IN_CONTAINER False" in result.stdout


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_secrets_not_inherited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _skip_without_docker()
    monkeypatch.setenv("AI_LAB_TEST_SECRET", "super-secret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    sandbox = DockerSandbox(_docker_policy(timeout_s=20), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    code = (
        "import os\n"
        "print('SECRET', os.environ.get('AI_LAB_TEST_SECRET'))\n"
        "print('OPENAI', os.environ.get('OPENAI_API_KEY'))\n"
        "print('SANDBOX', os.environ.get('AI_LAB_SANDBOX'))\n"
        "print('BACKEND', os.environ.get('AI_LAB_SANDBOX_BACKEND'))\n"
    )
    result = await sandbox.execute(ComputeSpec(code=code, timeout_s=20), _ctx(parent, task_id="env"))
    assert "SECRET None" in result.stdout
    assert "OPENAI None" in result.stdout
    assert "SANDBOX 1" in result.stdout
    assert "BACKEND docker" in result.stdout


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_memory_limit(tmp_path: Path) -> None:
    _skip_without_docker()
    policy = _docker_policy(timeout_s=20, memory_mb=32)
    sandbox = DockerSandbox(policy, repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    code = "x = bytearray(200 * 1024 * 1024)\nprint('ALLOCATED', len(x))\n"
    result = await sandbox.execute(
        ComputeSpec(code=code, timeout_s=20, memory_mb=32),
        _ctx(parent, task_id="mem"),
    )
    assert result.status in {SandboxStatus.MEMORY_LIMIT, SandboxStatus.NONZERO_EXIT, SandboxStatus.PROCESS_ERROR}
    assert result.status != SandboxStatus.SUCCESS
    assert "ALLOCATED" not in result.stdout


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_cpu_limit_recorded(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=8), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    code = "x=0\nfor i in range(5_000_000):\n    x += i\nprint('DONE', x)\n"
    result = await sandbox.execute(ComputeSpec(code=code, timeout_s=8), _ctx(parent, task_id="cpu"))
    assert result.artifact is not None
    assert result.artifact.resource_limits.get("cpus") == 0.5
    assert result.status in {SandboxStatus.SUCCESS, SandboxStatus.TIMEOUT}


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_pid_limit(tmp_path: Path) -> None:
    _skip_without_docker()
    policy = _docker_policy(timeout_s=20, max_processes=8)
    sandbox = DockerSandbox(policy, repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    code = (
        "import subprocess, sys\n"
        "ok = 0\n"
        "try:\n"
        "    for i in range(40):\n"
        "        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "        ok += 1\n"
        "    print('SPAWNED', ok)\n"
        "except Exception as e:\n"
        "    print('SPAWN_LIMIT', ok, type(e).__name__)\n"
    )
    result = await sandbox.execute(
        ComputeSpec(code=code, timeout_s=20, max_processes=8),
        _ctx(parent, task_id="pids"),
    )
    assert result.enforcement["max_processes"] == EnforcementLevel.HARD.value
    assert "SPAWNED 40" not in result.stdout


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_timeout_no_stale_container(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=1), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    result = await sandbox.execute(
        ComputeSpec(code="while True:\n    pass\n", timeout_s=1),
        _ctx(parent, task_id="to"),
    )
    assert result.status == SandboxStatus.TIMEOUT
    assert not result.process_alive_after_return
    name = (result.artifact.metadata or {}).get("container_name") if result.artifact else None
    if name:
        import subprocess

        ps = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name={name}"],
            capture_output=True,
            text=True,
            shell=False,
            timeout=10,
            check=False,
        )
        assert not (ps.stdout or "").strip()


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_reproducible_identity(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=20), repo_root=REPO)
    parent = tmp_path / "sandbox"
    parent.mkdir()
    spec = ComputeSpec(code="print(123)", timeout_s=20, inputs={"k": 1})
    a = await sandbox.execute(spec, _ctx(parent, task_id="r1"))
    b = await sandbox.execute(spec, _ctx(parent, task_id="r2"))
    assert a.artifact and b.artifact
    assert a.artifact.computation_hash == b.artifact.computation_hash
    assert a.artifact.image_digest == b.artifact.image_digest
    report = compare_computations(a.artifact, b.artifact)
    assert report.identity_match


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_cross_run_isolation(tmp_path: Path) -> None:
    _skip_without_docker()
    sandbox = DockerSandbox(_docker_policy(timeout_s=20), repo_root=REPO)
    parent_a = tmp_path / "runA" / "sandbox"
    parent_b = tmp_path / "runB" / "sandbox"
    parent_a.mkdir(parents=True)
    parent_b.mkdir(parents=True)
    a = await sandbox.execute(
        ComputeSpec(
            code="from pathlib import Path\nPath('mark.txt').write_text('A', encoding='utf-8')\nprint('A')\n",
            timeout_s=20,
        ),
        _ctx(parent_a, run_id="runA", task_id="task1"),
    )
    ws_a = Path(a.security_metadata["workspace"])
    code_b = (
        "from pathlib import Path\n"
        f"p = Path(r'{ws_a / 'mark.txt'}')\n"
        "print('SEES_OTHER_RUN', p.exists())\n"
        "Path('mark.txt').write_text('B', encoding='utf-8')\n"
    )
    b = await sandbox.execute(ComputeSpec(code=code_b, timeout_s=20), _ctx(parent_b, run_id="runB", task_id="task1"))
    assert (ws_a / "mark.txt").read_text(encoding="utf-8") == "A"
    assert "SEES_OTHER_RUN False" in b.stdout
    ws_b = Path(b.security_metadata["workspace"])
    assert (ws_b / "mark.txt").read_text(encoding="utf-8") == "B"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_taskgraph_python_execute_docker(tmp_path: Path) -> None:
    _skip_without_docker()
    from ai_lab.observability.tracing import RunEventSink
    from ai_lab.planner.static import default_pipeline_tasks
    from ai_lab.tools.factory import build_tool_registry

    proj = tmp_path / "p"
    proj.mkdir()
    store = ProjectStore(proj)
    store.ensure_layout()
    rs = RunStore(store, "run_docker_tg")
    config = LabConfig(
        provider="mock",
        sandbox={
            "backend": "docker",
            "timeout_seconds": 20,
            "allowed_modules": ["math"],
            "docker": {"image": "python:3.12-slim", "cpus": 1.0},
        },
    )
    sink = RunEventSink(proj / ".runs" / "run_docker_tg" / "events.jsonl")
    tools = build_tool_registry(store, config, run_id="run_docker_tg", sink=sink, run_store=rs, repo_root=REPO)
    sim_tasks = [t for t in default_pipeline_tasks() if t.output_schema == "computation"]
    out = await tools.call(
        "python.execute",
        allowed=["python.execute"],
        code="print(1)",
        task_id=sim_tasks[0].task_id,
    )
    assert out["sandbox_status"] == SandboxStatus.SUCCESS.value
    assert out["artifact"]["sandbox_backend"] == "docker"
    assert out["computation_hash"]
    events = sink.read_all()
    ev = next(e for e in events if e.tool_name == "python.execute")
    assert ev.data.get("image_digest") or ev.data.get("environment_reproducibility")
    assert "OPENAI_API_KEY" not in str(ev.data)
    host_paths = str(ev.data.get("sanitized_command") or "")
    assert "C:\\" not in host_paths
    from ai_lab.tools.python_exec import PythonExecTool

    tool = PythonExecTool(
        timeout_seconds=20,
        allowed_modules=set(),
        sandbox=build_compute_sandbox(_docker_policy(timeout_s=20), repo_root=REPO, require_available=True),
        policy=_docker_policy(timeout_s=20),
    )
    one = await tool.run(code="print('x')")
    assert one["sandbox_status"] == SandboxStatus.SUCCESS.value
