"""Pure Docker command-builder and mount-validator tests. No Docker engine required."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from ai_lab.core.enums import NetworkPolicy
from ai_lab.sandbox.docker_cmd import (
    FORBIDDEN_DOCKER_FLAGS,
    assert_command_safe,
    build_docker_command,
    make_container_name,
    resolve_image_ref,
    validate_image_ref,
)
from ai_lab.sandbox.errors import SandboxSecurityPolicyError, SandboxValidationError
from ai_lab.sandbox.models import ComputeSpec, DockerRuntimeConfig, SandboxPolicy
from ai_lab.sandbox.paths import is_path_inside, validate_bind_source

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "src" / "ai_lab" / "sandbox" / "runner.py"


def _policy() -> SandboxPolicy:
    return SandboxPolicy(
        backend="docker",
        docker=DockerRuntimeConfig(image="python:3.12-slim", cpus=1.0),
        memory_mb=256,
        max_processes=8,
        network=NetworkPolicy.DENY,
    )


def _plan(tmp_path: Path, *, spec: ComputeSpec | None = None, image: str = "python:3.12-slim") -> list[str]:
    root = tmp_path / "sandbox"
    ws = root / "t1"
    ws.mkdir(parents=True)
    plan = build_docker_command(
        policy=_policy(),
        spec=spec or ComputeSpec(code="print(1)", memory_mb=256, max_processes=8),
        image=image,
        container_name=make_container_name("run_a", "t1", "nonce1"),
        workspace_host=ws,
        workspace_root=root,
        runner_host=RUNNER,
        runner_expected=RUNNER,
        run_id="run_a",
    )
    return plan.argv


def test_command_contains_required_isolation_flags(tmp_path: Path) -> None:
    argv = _plan(tmp_path)
    joined = " ".join(argv)
    assert argv[0] == "docker"
    assert argv[1] == "run"
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in argv and argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "256m"
    assert "--cpus" in argv and argv[argv.index("--cpus") + 1] == "1"
    assert "--pids-limit" in argv and argv[argv.index("--pids-limit") + 1] == "8"
    assert "--user" in argv and argv[argv.index("--user") + 1] == "65534:65534"
    assert "--rm" in argv
    assert "--pull" in argv and argv[argv.index("--pull") + 1] == "never"
    assert "python" in argv
    assert "-I" in argv
    assert "/opt/ai-lab/runner.py" in argv
    assert "--workspace" in argv
    idx = argv.index("--workspace")
    assert argv[idx + 1] == "/workspace"
    assert "--privileged" not in joined
    mount_tokens = [argv[i + 1] for i, t in enumerate(argv) if t == "--mount"]
    assert any("target=/workspace" in m for m in mount_tokens)
    assert any("target=/opt/ai-lab/runner.py,readonly" in m for m in mount_tokens)


def test_forbidden_flags_absent(tmp_path: Path) -> None:
    argv = _plan(tmp_path)
    for bad in FORBIDDEN_DOCKER_FLAGS:
        assert bad not in argv
        if bad not in {"--net=host", "--network=host"}:
            assert all(bad not in token or token in {"--network", "--net"} for token in argv)
    assert "-v" not in argv
    assert "--volume" not in argv
    assert "--cap-add" not in argv
    assert "--device" not in argv
    assert "--pid" not in argv
    assert "--ipc" not in argv
    assert_command_safe(argv)


def test_user_code_not_in_argv(tmp_path: Path) -> None:
    spec = ComputeSpec(code="print('; --privileged')\n# --network=host", memory_mb=64, max_processes=4)
    argv = _plan(tmp_path, spec=spec)
    assert "; --privileged" not in argv
    assert "--privileged" not in argv
    assert not any("--network=host" in t for t in argv)
    assert spec.code not in " ".join(argv)


def test_container_name_ignores_injection() -> None:
    name = make_container_name("run", "; --privileged", "x")
    assert name.startswith("ai-lab-")
    assert "--privileged" not in name
    assert ";" not in name
    assert name == make_container_name("run", "; --privileged", "x")
    assert name != make_container_name("run", "other", "x")
    assert name != make_container_name("run", "; --privileged", "y")


def test_image_ref_rejects_injection() -> None:
    with pytest.raises(SandboxValidationError):
        validate_image_ref("python:3.12; --privileged")
    with pytest.raises(SandboxValidationError):
        validate_image_ref("--privileged")
    with pytest.raises(SandboxValidationError):
        validate_image_ref("python:3.12 && cat /etc/passwd")
    pinned = resolve_image_ref("python:3.12-slim", "a" * 64)
    assert pinned.endswith("@" + "sha256:" + "a" * 64)


def test_memory_and_cpus_are_translated_not_raw_flags(tmp_path: Path) -> None:
    spec = ComputeSpec(code="print(1)", memory_mb=512, max_processes=3)
    argv = _plan(tmp_path, spec=spec)
    assert argv[argv.index("--memory") + 1] == "512m"
    assert "--memory" not in spec.code
    assert "unlimited" not in argv


def test_mount_validator_not_string_prefix(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project2 = tmp_path / "project2"
    project.mkdir()
    project2.mkdir()
    secret = project2 / "secret.txt"
    secret.write_text("x", encoding="utf-8")
    # Naive startswith would accept project2 as inside project.
    assert str(project2).startswith(str(project))
    assert not is_path_inside(project2, project)
    with pytest.raises(SandboxSecurityPolicyError, match="outside"):
        validate_bind_source(project2, project)
    inside = project / "sandbox" / "t1"
    inside.mkdir(parents=True)
    assert validate_bind_source(inside, project) == inside.resolve()


def test_mount_validator_rejects_dotdot(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    sneaky = root / ".." / "secret"
    with pytest.raises(SandboxSecurityPolicyError, match="outside"):
        validate_bind_source(sneaky, root)


def test_mount_validator_rejects_unc() -> None:
    root = Path("C:/tmp/ai-lab-root") if sys.platform == "win32" else Path("/tmp/ai-lab-root")
    unc = Path(r"\\server\share\x") if sys.platform == "win32" else Path("//server/share/x")
    assert not is_path_inside(unc, root)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_windows_drive_paths_not_prefix(tmp_path: Path) -> None:
    a = tmp_path / "project"
    b = tmp_path / "project2"
    a.mkdir()
    b.mkdir()
    assert not is_path_inside(b, a)
    with pytest.raises(SandboxSecurityPolicyError):
        validate_bind_source(b, a)


def test_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    link = root / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink/junction not permitted on this platform")
    with pytest.raises(SandboxSecurityPolicyError, match="outside"):
        validate_bind_source(link, root)


def test_network_allow_rejected_by_builder(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    ws = root / "t1"
    ws.mkdir(parents=True)
    spec = ComputeSpec(code="print(1)", network=NetworkPolicy.ALLOW)
    with pytest.raises(SandboxSecurityPolicyError, match="network"):
        build_docker_command(
            policy=_policy(),
            spec=spec,
            image="python:3.12-slim",
            container_name="ai-lab-abc",
            workspace_host=ws,
            workspace_root=root,
            runner_host=RUNNER,
            runner_expected=RUNNER,
            run_id="r",
        )
