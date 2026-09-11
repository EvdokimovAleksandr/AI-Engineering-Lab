"""Sandbox worker process.

This file is executed as a script with `python -I runner.py --workspace <dir>`.
It must not import ai_lab or read project config / .env / other runs.

User code is untrusted. Isolation is the process + parent OS policy, not this
BEST_EFFORT open() guard.
"""

from __future__ import annotations

import argparse
import builtins
import json
import os
import sys
import traceback
from pathlib import Path


def _install_workspace_guard(workspace: Path) -> None:
    """BEST_EFFORT: wrap open() so paths outside workspace fail.

    pathlib uses io.open, not builtins.open, so both are wrapped.
    ctypes / os.open can still bypass this. Not a security boundary.
    """
    import io

    root = workspace.resolve()
    real_open = builtins.open
    real_io_open = io.open

    def guarded_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if isinstance(file, int):
            return real_io_open(file, mode, *args, **kwargs)
        path = Path(file)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            resolved = path.resolve()
        except OSError:
            raise PermissionError(f"FILESYSTEM_DENIED: cannot resolve {file!r}") from None
        try:
            resolved.relative_to(root)
        except ValueError:
            raise PermissionError(f"FILESYSTEM_DENIED: {resolved} is outside sandbox workspace") from None
        return real_io_open(file, mode, *args, **kwargs)

    builtins.open = guarded_open  # type: ignore[assignment]
    io.open = guarded_open  # type: ignore[assignment]
    _ = real_open


def _apply_posix_rlimits(limits: dict) -> None:
    if os.name == "nt":
        return
    try:
        import resource
    except Exception:
        return
    memory_mb = limits.get("memory_mb")
    cpu_time_s = limits.get("cpu_time_s")
    if memory_mb:
        nbytes = int(memory_mb) * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
        except Exception as exc:
            sys.stderr.write(f"sandbox runner: RLIMIT_AS failed: {exc}\n")
    if cpu_time_s:
        seconds = max(1, int(float(cpu_time_s)))
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
        except Exception as exc:
            sys.stderr.write(f"sandbox runner: RLIMIT_CPU failed: {exc}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-lab-sandbox-runner")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"sandbox runner: workspace missing: {workspace}", file=sys.stderr)
        return 2

    # Только файлы, которые launcher сам положил в workspace.
    code_path = workspace / "user_code.py"
    inputs_path = workspace / "inputs.json"
    limits_path = workspace / "limits.json"
    result_path = workspace / "runner_result.json"

    if not code_path.is_file():
        print("sandbox runner: user_code.py missing", file=sys.stderr)
        return 2

    limits: dict = {}
    if limits_path.is_file():
        limits = json.loads(limits_path.read_text(encoding="utf-8"))

    os.chdir(workspace)
    _install_workspace_guard(workspace)
    _apply_posix_rlimits(limits)

    inputs: dict = {}
    if inputs_path.is_file():
        inputs = json.loads(inputs_path.read_text(encoding="utf-8"))

    code = code_path.read_text(encoding="utf-8")
    # INPUTS передаются JSON-файлом, не интерполяцией в командную строку.
    namespace: dict = {
        "__name__": "__main__",
        "__file__": str(code_path),
        "INPUTS": inputs,
    }
    status = "ok"
    error = None
    try:
        compiled = compile(code, str(code_path), "exec")
        exec(compiled, namespace, namespace)  # noqa: S102 — untrusted by design, process-isolated
    except PermissionError as exc:
        status = "filesystem_denied"
        error = str(exc)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        _write_result(result_path, status=status, error=error)
        return 3
    except SystemExit as exc:
        code_rc = exc.code
        rc = int(code_rc) if isinstance(code_rc, int) else (0 if code_rc is None else 1)
        _write_result(result_path, status="system_exit", error=str(exc), exit_code=rc)
        return rc
    except Exception as exc:
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        _write_result(result_path, status=status, error=error)
        return 1

    _write_result(result_path, status=status, error=error)
    return 0


def _write_result(path: Path, *, status: str, error: str | None, exit_code: int | None = None) -> None:
    payload = {"status": status, "error": error, "exit_code": exit_code}
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
