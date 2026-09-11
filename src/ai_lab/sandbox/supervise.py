"""Parent-side process supervision: wall timeout, output caps, stream close.

Used by LocalSubprocessSandbox and DockerSandbox. Isolation mechanism is the
backend; this module only watches pipes and the deadline.
"""

from __future__ import annotations

import asyncio
import sys
import time

from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)


async def supervise_process(
    proc: asyncio.subprocess.Process,
    *,
    timeout_s: float,
    max_stdout: int,
    max_stderr: int,
) -> tuple[bytes, bytes, bool, bool]:
    """Читаем stdout/stderr с cap; timeout убивает wait, а не thread pool."""
    out = bytearray()
    err = bytearray()
    exceeded = {"out": False, "err": False}

    async def pump(stream, buf: bytearray, limit: int, key: str) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            if exceeded[key] or len(buf) >= limit:
                exceeded[key] = True
                return
            take = min(len(chunk), limit - len(buf))
            buf.extend(chunk[:take])
            if take < len(chunk):
                exceeded[key] = True
                return

    t_out = asyncio.create_task(pump(proc.stdout, out, max_stdout, "out"))
    t_err = asyncio.create_task(pump(proc.stderr, err, max_stderr, "err"))
    t_wait = asyncio.create_task(proc.wait())

    deadline = time.monotonic() + timeout_s
    timed_out = False
    output_exceeded = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        if exceeded["out"] or exceeded["err"]:
            output_exceeded = True
            break
        if t_wait.done():
            await asyncio.wait({t_out, t_err}, timeout=1.0)
            output_exceeded = exceeded["out"] or exceeded["err"]
            break
        await asyncio.wait(
            {t_wait, t_out, t_err},
            timeout=min(0.05, remaining),
            return_when=asyncio.FIRST_COMPLETED,
        )

    if timed_out or output_exceeded:
        t_out.cancel()
        t_err.cancel()

    return bytes(out), bytes(err), timed_out, output_exceeded


def close_subprocess_streams(proc: asyncio.subprocess.Process | None) -> None:
    """Закрыть pipe transports, иначе на Windows остаётся ResourceWarning после timeout."""
    if proc is None:
        return
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        transport = getattr(stream, "_transport", None)
        if transport is None:
            continue
        try:
            transport.close()
        except Exception:
            pass


async def wait_killed(proc: asyncio.subprocess.Process | None) -> None:
    """Дождаться exit после kill; не маскировать зависший процесс."""
    if proc is None:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        logger.error("Process did not exit after kill: pid=%s", proc.pid)
        try:
            proc.kill()
        except OSError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except Exception as exc:
            logger.error("Final wait after kill failed: %s", exc)


def kill_local_process(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL / TerminateProcess для local backend. Docker kill — отдельный путь."""
    if proc.returncode is not None:
        return
    if sys.platform != "win32" and proc.pid:
        import os
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    except OSError as exc:
        logger.error("proc.kill failed: %s", exc)
