"""Contain session-owned processes without changing the VM's native-call policy."""

from __future__ import annotations

import os
import signal


def enter_process_scope() -> None:
    """Isolate Unix descendants before accepting any language execution."""
    if os.name != "nt":
        os.setsid()


class ProcessScope:
    """Own a Windows kill-on-close job or a Unix session process group."""

    def __init__(self, process) -> None:
        """Attach containment before the parent can submit any program."""
        self.pid = process.pid
        self.handle = None
        if os.name == "nt":
            self._attach_windows(process)

    def _attach_windows(self, process) -> None:
        """Use pointer-width Win32 signatures and non-inherited job ownership."""
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            """ABI layout of JOBOBJECT_BASIC_LIMIT_INFORMATION."""

            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            """ABI layout of IO_COUNTERS."""

            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "read_operations",
                    "write_operations",
                    "other_operations",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            """ABI layout of JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""

            _fields_ = [
                ("basic", BasicLimits),
                ("io", IoCounters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ) or not kernel.AssignProcessToJobObject(handle, process.sentinel):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        self.kernel = kernel
        self.handle = handle

    def close(self) -> None:
        """Release the scope and terminate remaining ordinary descendants.

        This reclaims OS resources, not language destructors or external effects.
        Unix processes that deliberately leave the group are outside this scope.
        """
        if os.name == "nt":
            if self.handle is not None:
                if not self.kernel.CloseHandle(self.handle):
                    import ctypes

                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = None
        else:
            if self.pid is None:
                return
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.pid = None
