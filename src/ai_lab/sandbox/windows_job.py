"""Windows Job Objects helper — isolated from orchestrator / ToolRegistry.

Uses ctypes only (stdlib). If creation/assignment fails, callers must record
UNSUPPORTED and fall back to TerminateProcess on the direct child — not pretend
the job still enforces memory/CPU/tree-kill.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

from ai_lab.observability.logger import get_logger

logger = get_logger(__name__)

# Win32 constants
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400

JobObjectExtendedLimitInformation = 9

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

STILL_ACTIVE = 259


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def job_objects_available() -> bool:
    return sys.platform == "win32"


class WindowsJob:
    """One job for one sandbox worker (+ descendants that do not break away)."""

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self.assigned = False

    @classmethod
    def try_create(
        cls,
        *,
        memory_bytes: int | None,
        cpu_time_s: float | None,
        max_processes: int | None,
    ) -> WindowsJob | None:
        if not job_objects_available():
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            err = ctypes.get_last_error()
            logger.error("CreateJobObjectW failed: winerror=%s", err)
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if memory_bytes and memory_bytes > 0:
            flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY
            info.ProcessMemoryLimit = int(memory_bytes)
            info.JobMemoryLimit = int(memory_bytes)
        if cpu_time_s and cpu_time_s > 0:
            flags |= JOB_OBJECT_LIMIT_PROCESS_TIME
            # 100-nanosecond ticks
            info.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_time_s * 10_000_000)
        if max_processes and max_processes > 0:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
        info.BasicLimitInformation.LimitFlags = flags

        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        ok = kernel32.SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            logger.error("SetInformationJobObject failed: winerror=%s", err)
            kernel32.CloseHandle(handle)
            return None
        return cls(int(handle))

    def assign(self, pid: int) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        access = PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION
        proc = kernel32.OpenProcess(access, False, int(pid))
        if not proc:
            logger.error("OpenProcess for job assign failed: pid=%s winerror=%s", pid, ctypes.get_last_error())
            return False
        try:
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            ok = kernel32.AssignProcessToJobObject(self._handle, proc)
            if not ok:
                logger.error(
                    "AssignProcessToJobObject failed: pid=%s winerror=%s",
                    pid,
                    ctypes.get_last_error(),
                )
                return False
            self.assigned = True
            return True
        finally:
            kernel32.CloseHandle(proc)

    def terminate(self, exit_code: int = 1) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject(self._handle, exit_code)

    def query_peak_memory(self) -> int | None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        needed = wintypes.DWORD(0)
        ok = kernel32.QueryInformationJobObject(
            self._handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(needed),
        )
        if not ok:
            return None
        return int(info.PeakJobMemoryUsed)

    def close(self) -> None:
        """CloseHandle + KILL_ON_JOB_CLOSE terminates remaining job members."""
        if not self._handle:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle(self._handle)
        self._handle = 0


def pid_is_alive(pid: int | None) -> bool:
    """Best-effort check that a PID is still a live process (PID reuse possible)."""
    if pid is None or pid <= 0:
        return False
    if sys.platform != "win32":
        import os

        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        code = wintypes.DWORD(0)
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        if not ok:
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def job_query_info(job: WindowsJob | None) -> dict[str, Any]:
    if job is None:
        return {"attached": False}
    return {
        "attached": job.assigned,
        "peak_memory_bytes": job.query_peak_memory() if job.assigned else None,
    }
