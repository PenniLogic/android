"""Own Windows command trees before execution and confirm shutdown before file restoration.

The Job/process-object/bounded-drain boundary follows infra's accepted Windows
bootstrap/conformance pattern. This module is Android-owned and uses only the
standard library. A release event preserves the consumer's inherited stdin.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import TextIO


TEARDOWN_SECONDS = 5.0
POLL_SECONDS = 0.02
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
ERROR_INVALID_PARAMETER = 87
ERROR_MORE_DATA = 234
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x100000


class ProcessOwnershipError(OSError):
    """The trusted idle launcher could not be owned; no consumer may be released."""


class WindowsProcessError(RuntimeError):
    """A command failed after its tree and pipes were confirmed safe to restore."""


class UnsafeProcessTreeError(WindowsProcessError):
    """Shutdown is unconfirmed; callers must preserve files the consumer could hold."""

    restoration_safe = False

    def __init__(self, detail: str, cwd: Path, processes: list[dict[str, int]] | None = None):
        super().__init__("Windows command teardown unconfirmed")
        self.cwd = cwd
        self.processes = processes if processes is not None else []
        self.retained_fixtures: dict[str, str] = {}
        self.pending_launcher: subprocess.Popen | None = None
        self.primary_error: BaseException | None = None
        self.secondary_errors: list[tuple[str, BaseException]] = []
        self.failures: list[dict[str, object]] = []
        self.confirmation: dict[str, object] = {
            "native_exits": False, "pipes_released": False, "membership_known": False,
        }
        self.pending_resources: list[object] = []

    def record_failure(self, stage: str, error: BaseException) -> None:
        self.secondary_errors.append((stage, error))
        self.failures.append(_failure_fields(stage, error))

    def diagnostic(self) -> dict[str, object]:
        processes = [
            {
                "pid": process["pid"],
                "created": process.get("created"),
                "creation_filetime_100ns": process.get("created"),
                "native_exit": (
                    "confirmed" if self.confirmation["native_exits"] and process.get("created") is not None
                    else "unconfirmed"
                ),
                **({"unconfirmed_winerror": process["unconfirmed_winerror"]}
                   if "unconfirmed_winerror" in process else {}),
            }
            for process in self.processes
        ]
        return {
            "code": "windows_process_teardown_unconfirmed",
            "restoration_safe": False,
            "budget_seconds": TEARDOWN_SECONDS,
            "recovery_context": {
                "working_directory": ".",
                "action": "Confirm the recorded creation-owned process exits and reader-owned pipe release before restoration.",
            },
            "processes": processes,
            "unconfirmed_identities": [process for process in processes if process["native_exit"] == "unconfirmed"],
            "confirmation": self.confirmation,
            "pending_launcher": self.pending_launcher is not None,
            "pending_resource_types": [type(resource).__name__ for resource in self.pending_resources],
            "retained_fixtures": self.retained_fixtures,
            "failures": self.failures,
        }

    def __str__(self) -> str:
        return "WINDOWS_PROCESS_RECOVERY " + json.dumps(self.diagnostic(), ensure_ascii=True, sort_keys=True)

    def retain_fixture(self, path: Path) -> None:
        try:
            location = str(path.absolute().relative_to(self.cwd.absolute()))
        except ValueError:
            location = "outside_working_directory"
        recovery = "Confirm the recorded process objects and captured pipes are released before removing this fixture."
        self.retained_fixtures[location] = recovery
        self.add_note(f"Planted fixture retained at {location}. {recovery}")


def _failure_fields(stage: str, error: BaseException) -> dict[str, object]:
    return {
        "stage": stage,
        "type": type(error).__name__,
        **{name: value for name in ("errno", "winerror") if isinstance(value := getattr(error, name, None), int)},
        **({"children": [_failure_fields(stage, child) for child in error.exceptions]}
           if isinstance(error, BaseExceptionGroup) else {}),
    }


def _cleanup_error(
    outcome: BaseException | None,
    error: BaseException,
    stage: str,
    cwd: Path,
    processes: list[dict[str, int]],
    process: subprocess.Popen | None,
    confirmation: dict[str, object],
) -> UnsafeProcessTreeError:
    def nested_unsafe(value: BaseException) -> UnsafeProcessTreeError | None:
        if isinstance(value, UnsafeProcessTreeError):
            return value
        if isinstance(value, BaseExceptionGroup):
            for child in value.exceptions:
                if found := nested_unsafe(child):
                    return found
        return None

    secondary_unsafe = nested_unsafe(error)
    unsafe = (
        outcome if isinstance(outcome, UnsafeProcessTreeError)
        else secondary_unsafe or UnsafeProcessTreeError(stage, cwd, processes)
    )
    if unsafe.primary_error is None and outcome is not unsafe:
        unsafe.primary_error = outcome
        if outcome is not None and outcome is not unsafe:
            unsafe.record_failure("primary", outcome)
    unsafe.record_failure(stage, error)
    known = {(entry["pid"], entry.get("created")) for entry in unsafe.processes}
    unsafe.processes.extend(entry for entry in processes if (entry["pid"], entry.get("created")) not in known)
    if secondary_unsafe is not None and secondary_unsafe is not unsafe:
        known = {(entry["pid"], entry.get("created")) for entry in unsafe.processes}
        unsafe.processes.extend(
            entry for entry in secondary_unsafe.processes if (entry["pid"], entry.get("created")) not in known
        )
        unsafe.retained_fixtures.update(secondary_unsafe.retained_fixtures)
        unsafe.pending_resources.extend(secondary_unsafe.pending_resources)
        if unsafe.pending_launcher is None:
            unsafe.pending_launcher = secondary_unsafe.pending_launcher
    unsafe.confirmation.update(confirmation)
    if process is not None and not process._handle.closed:
        unsafe.pending_launcher = process
    return unsafe


def _close_after_error(error: BaseException, close: Callable[[], None], stage: str) -> None:
    try:
        close()
    except BaseException as secondary:
        if isinstance(error, UnsafeProcessTreeError):
            error.record_failure(stage, secondary)
            raise error from None
        if isinstance(secondary, UnsafeProcessTreeError):
            secondary.record_failure("primary", error)
            raise secondary from None
        raise BaseExceptionGroup("Owned operation and resource release failed", [error, secondary]) from None


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "read_operations", "write_operations", "other_operations",
        "read_bytes", "write_bytes", "other_bytes",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
        ("page_faults", wintypes.DWORD), ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD), ("terminated_processes", wintypes.DWORD),
    ]


def _pid_list(capacity: int) -> ctypes.Structure:
    class Pids(ctypes.Structure):
        _fields_ = [
            ("assigned", wintypes.DWORD), ("listed", wintypes.DWORD),
            ("pids", ctypes.c_size_t * capacity),
        ]
    return Pids()


class _Native:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows process ownership is available only on Windows")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = (
            ("CreateJobObjectW", wintypes.HANDLE, (ctypes.c_void_p, wintypes.LPCWSTR)),
            ("SetInformationJobObject", wintypes.BOOL,
             (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)),
            ("QueryInformationJobObject", wintypes.BOOL,
             (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)),
            ("AssignProcessToJobObject", wintypes.BOOL, (wintypes.HANDLE, wintypes.HANDLE)),
            ("IsProcessInJob", wintypes.BOOL,
             (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))),
            ("TerminateJobObject", wintypes.BOOL, (wintypes.HANDLE, wintypes.UINT)),
            ("OpenProcess", wintypes.HANDLE, (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)),
            ("GetCurrentProcess", wintypes.HANDLE, ()),
            ("DuplicateHandle", wintypes.BOOL,
             (wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
              wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)),
            ("WaitForSingleObject", wintypes.DWORD, (wintypes.HANDLE, wintypes.DWORD)),
            ("GetProcessTimes", wintypes.BOOL,
             (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4),
            ("GetExitCodeProcess", wintypes.BOOL,
             (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))),
            ("CreateEventW", wintypes.HANDLE,
             (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)),
            ("SetEvent", wintypes.BOOL, (wintypes.HANDLE,)),
            ("SetHandleInformation", wintypes.BOOL,
             (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)),
            ("CloseHandle", wintypes.BOOL, (wintypes.HANDLE,)),
        )
        for name, result, arguments in declarations:
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, arguments

    @staticmethod
    def require(success: object, operation: str) -> None:
        if not success:
            raise ctypes.WinError(ctypes.get_last_error(), operation)

    def close(self, handle: int) -> None:
        self.require(self.kernel.CloseHandle(handle), "CloseHandle")

    def duplicate(self, handle: int) -> int:
        copy = wintypes.HANDLE()
        current = self.kernel.GetCurrentProcess()
        self.require(
            self.kernel.DuplicateHandle(current, handle, current, ctypes.byref(copy), 0, False, 2),
            "DuplicateHandle",
        )
        assert copy.value is not None
        return copy.value

    def wait(self, handle: int, seconds: float = 0) -> bool:
        result = self.kernel.WaitForSingleObject(handle, max(0, int(seconds * 1000)))
        if result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            raise ctypes.WinError(ctypes.get_last_error(), "WaitForSingleObject")
        return result == WAIT_OBJECT_0

    def created(self, handle: int) -> int:
        times = [wintypes.FILETIME() for _ in range(4)]
        self.require(
            self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)),
            "GetProcessTimes",
        )
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def exit_code(self, handle: int) -> int:
        result = wintypes.DWORD()
        self.require(self.kernel.GetExitCodeProcess(handle, ctypes.byref(result)), "GetExitCodeProcess")
        return result.value


_native_instance: _Native | None = None


def native() -> _Native:
    global _native_instance
    if _native_instance is None:
        _native_instance = _Native()
    return _native_instance


class WindowsJob:
    def __init__(self) -> None:
        self.handle: int | None = None
        api = native()
        try:
            self.handle = api.kernel.CreateJobObjectW(None, None)
            api.require(self.handle, "CreateJobObjectW")
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # kill-on-close, without a breakaway permission
            api.require(
                api.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                "SetInformationJobObject",
            )
        except BaseException as error:
            failure = ProcessOwnershipError(*error.args) if isinstance(error, OSError) else error
            _close_after_error(failure, self.close, "job.initialize.close")
            if failure is error:
                raise
            raise failure from error

    def assign(self, process_handle: int) -> None:
        try:
            native().require(
                native().kernel.AssignProcessToJobObject(self.handle, process_handle),
                "AssignProcessToJobObject",
            )
        except OSError as error:
            raise ProcessOwnershipError(*error.args) from error

    def process_ids(self) -> list[int]:
        for capacity in (64, 4096):
            result = _pid_list(capacity)
            if native().kernel.QueryInformationJobObject(
                self.handle, 3, ctypes.byref(result), ctypes.sizeof(result), None,
            ):
                return list(result.pids[:result.listed])
            if ctypes.get_last_error() != ERROR_MORE_DATA:
                break
        raise ctypes.WinError(ctypes.get_last_error(), "QueryInformationJobObject process membership")

    def active_processes(self) -> int:
        result = _Accounting()
        native().require(
            native().kernel.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(result), ctypes.sizeof(result), None,
            ),
            "QueryInformationJobObject accounting",
        )
        return result.active_processes

    def terminate(self) -> None:
        native().require(native().kernel.TerminateJobObject(self.handle, 1), "TerminateJobObject")

    def close(self) -> None:
        if self.handle is not None:
            native().close(self.handle)
            self.handle = None


class ReleaseEvent:
    def __init__(self) -> None:
        self.handle: int | None = None
        try:
            self.handle = native().kernel.CreateEventW(None, False, False, None)
            native().require(self.handle, "CreateEventW")
            native().require(
                native().kernel.SetHandleInformation(self.handle, 1, 1),
                "SetHandleInformation release event",
            )
        except BaseException as error:
            _close_after_error(error, self.close, "release_event.initialize.close")
            raise

    def release(self) -> None:
        native().require(native().kernel.SetEvent(self.handle), "SetEvent")

    def close(self) -> None:
        if self.handle is not None:
            native().close(self.handle)
            self.handle = None


@dataclass
class ProcessPin:
    pid: int
    created: int
    handle: int

    @classmethod
    def from_process(cls, process: subprocess.Popen) -> ProcessPin:
        handle = native().duplicate(int(process._handle))
        try:
            return cls(process.pid, native().created(handle), handle)
        except BaseException as error:
            _close_after_error(error, lambda: native().close(handle), "process_pin.initialize.close")
            raise

    @classmethod
    def open(cls, pid: int, created: int | None = None) -> ProcessPin:
        handle = native().kernel.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        native().require(handle, "OpenProcess creation-pinned process")
        try:
            birth = native().created(handle)
            if created is not None and birth != created:
                raise WindowsProcessError("Process creation identity changed; refusing the reused PID")
            return cls(pid, birth, handle)
        except BaseException as error:
            _close_after_error(error, lambda: native().close(handle), "process_pin.open.close")
            raise

    def signaled(self) -> bool:
        return native().wait(self.handle)

    def metadata(self) -> dict[str, int]:
        return {"pid": self.pid, "created": self.created}

    def close(self) -> None:
        native().close(self.handle)


class ProcessExits:
    def __init__(self) -> None:
        self.pins: dict[tuple[int, int], ProcessPin] = {}
        self.pending: dict[int, int] = {}

    def add_root(self, process: subprocess.Popen) -> ProcessPin:
        pin = ProcessPin.from_process(process)
        self.pins[(pin.pid, pin.created)] = pin
        return pin

    def add_members(self, job: WindowsJob) -> None:
        failures: list[BaseException] = []
        for pid in set(job.process_ids()) | self.pending.keys():
            try:
                pinned = any(pin.pid == pid and not pin.signaled() for pin in self.pins.values())
            except BaseException as error:
                failures.append(error)
                # A failed wait must not prevent other owned identities from being pinned.
                continue
            if pinned:
                self.pending.pop(pid, None)
                continue
            try:
                pin = ProcessPin.open(pid)
            except BaseException as error:
                if isinstance(error, OSError) and getattr(error, "winerror", None) == ERROR_INVALID_PARAMETER:
                    self.pending.pop(pid, None)
                    continue  # Windows no longer has a process object for this member.
                if isinstance(error, OSError) and getattr(error, "winerror", None) == 5:
                    # A terminating member can refuse OpenProcess before its PID disappears.
                    # It remains unconfirmed until a pinned wait or ERROR_INVALID_PARAMETER.
                    self.pending[pid] = error.winerror
                    continue
                failures.append(error)
                continue
            inside = wintypes.BOOL()
            try:
                native().require(
                    native().kernel.IsProcessInJob(pin.handle, job.handle, ctypes.byref(inside)),
                    "IsProcessInJob",
                )
                if not inside.value:
                    if pin.signaled():
                        self.pending.pop(pid, None)
                        continue
                    raise WindowsProcessError("Job member identity changed before its process object was pinned")
                key = (pin.pid, pin.created)
                if key not in self.pins:
                    self.pins[key] = pin
                    pin = None
                self.pending.pop(pid, None)
            except BaseException as error:
                if pin is not None:
                    release = pin
                    pin = None
                    try:
                        _close_after_error(error, release.close, "process_member.close")
                    except BaseException as cleanup_error:
                        error = cleanup_error
                failures.append(error)
            finally:
                if pin is not None:
                    try:
                        pin.close()
                    except BaseException as error:
                        failures.append(error)
        if failures:
            for error in failures:
                if isinstance(error, (UnsafeProcessTreeError, KeyboardInterrupt, SystemExit)):
                    for secondary in failures:
                        if secondary is not error:
                            if isinstance(error, UnsafeProcessTreeError):
                                error.record_failure("process_members", secondary)
                            else:
                                error.add_note(json.dumps(_failure_fields("process_members", secondary)))
                    raise error from None
            if len(failures) == 1:
                raise failures[0]
            raise BaseExceptionGroup("Owned process membership confirmation failed", failures)

    def all_signaled(self) -> bool:
        return bool(self.pins) and not self.pending and all(pin.signaled() for pin in self.pins.values())

    def metadata(self) -> list[dict[str, int]]:
        return [pin.metadata() for pin in self.pins.values()] + [
            {"pid": pid, "unconfirmed_winerror": error} for pid, error in self.pending.items()
        ]

    def close(self) -> None:
        failures = []
        for key, pin in list(self.pins.items()):
            try:
                pin.close()
            except BaseException as error:
                failures.append(error)
            else:
                del self.pins[key]
        if failures:
            raise BaseExceptionGroup("Creation-owned process handle release failed", failures)
        self.pending.clear()


_GATE = """import ctypes
from ctypes import wintypes
import json
import subprocess
import sys

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel.CloseHandle.restype = wintypes.BOOL
event = int(sys.argv[1])
if kernel.WaitForSingleObject(event, 0xffffffff) != 0:
    raise ctypes.WinError(ctypes.get_last_error())
if not kernel.CloseHandle(event):
    raise ctypes.WinError(ctypes.get_last_error())
try:
    result = subprocess.run(json.loads(sys.argv[2]), stdout=sys.stdout, stderr=sys.stdout, check=False)
except OSError as error:
    status = {"error": str(error), "errno": error.errno,
              "winerror": error.winerror, "filename": error.filename}
except ValueError as error:
    status = {"invalid_command": str(error)}
else:
    status = {"exit_code": result.returncode}
print(json.dumps(status), file=sys.stderr, flush=True)
"""


class _Reader:
    def __init__(self, stream: TextIO, lines: queue.Queue[str] | None = None) -> None:
        self.stream = stream
        self.lines = lines
        self.control: list[str] = []
        self.error: BaseException | None = None
        self.done = threading.Event()
        self.stop_delivery = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self) -> None:
        try:
            with self.stream:
                for line in self.stream:
                    if self.lines is None:
                        self.control.append(line)
                    else:
                        while not self.stop_delivery.is_set():
                            try:
                                self.lines.put(line, timeout=POLL_SECONDS)
                                break
                            except queue.Full:
                                continue
        except BaseException as error:
            self.error = error
        finally:
            self.done.set()

    def start(self) -> None:
        self.thread.start()

    def close_unstarted(self) -> None:
        if self.thread.ident is None:
            self.stream.close()
            self.done.set()

    def released(self) -> bool:
        return self.done.is_set() and not self.thread.is_alive() and self.stream.closed and self.error is None


def _finish(
    job: WindowsJob,
    process: subprocess.Popen,
    root: ProcessPin | None,
    exits: ProcessExits,
    readers: list[_Reader],
    lines: queue.Queue[str],
    retain_output: bool,
    owned: bool,
    cwd: Path,
    primary_error: BaseException | None = None,
) -> list[str]:
    deadline = time.monotonic() + TEARDOWN_SECONDS
    problems: list[tuple[str, BaseException]] = []
    remaining: list[str] = []
    membership_known = not owned
    outcome: BaseException | None = None
    recovery = [root.metadata()] if root is not None else [{"pid": process.pid}]
    confirmation: dict[str, object] = {
        "native_exits": False, "pipes_released": False, "membership_known": membership_known,
        "active_members": None,
    }
    job_close_attempted = False
    try:
        recovery = exits.metadata()
        for reader in readers:
            reader.close_unstarted()
        if owned:
            try:
                exits.add_members(job)
                membership_known = True
            except BaseException as error:
                problems.append(("membership", error))
            try:
                job.terminate()
            except BaseException as error:
                problems.append(("job.terminate", error))
                # Closing requests termination, but never substitutes for the waits below.
                job_close_attempted = True
                try:
                    job.close()
                except BaseException as close_error:
                    outcome = _cleanup_error(
                        primary_error, close_error, "job.close", cwd, exits.metadata(), process, confirmation,
                    )
                    outcome.pending_resources.append(job)
        else:
            process.kill()
        while True:
            if owned and job.handle is not None:
                try:
                    exits.add_members(job)
                except BaseException as error:
                    membership_known = False
                    if not any(stage == "membership" for stage, _ in problems):
                        problems.append(("membership", error))
            try:
                line = lines.get(timeout=max(0, min(POLL_SECONDS, deadline - time.monotonic())))
            except queue.Empty:
                pass
            else:
                if retain_output:
                    remaining.append(line)
            native_exit = exits.all_signaled() if root is not None else native().wait(int(process._handle))
            pipes_released = all(reader.released() for reader in readers)
            if native_exit and pipes_released and lines.empty():
                break
            if time.monotonic() >= deadline:
                break
        active = job.active_processes() if job.handle is not None else None
        native_exit = exits.all_signaled() if root is not None else native().wait(int(process._handle))
        safe = membership_known and native_exit and all(reader.released() for reader in readers)
        safe = safe and (active == 0 or job.handle is None)
        recovery = exits.metadata()
        confirmation.update({
            "native_exits": native_exit, "pipes_released": all(reader.released() for reader in readers),
            "membership_known": membership_known, "active_members": active,
        })
        if not safe:
            outcome = _cleanup_error(
                outcome or primary_error, WindowsProcessError("Exit or pipe confirmation incomplete"),
                "teardown.confirm", cwd, recovery, process, confirmation,
            )
        elif problems and outcome is None:
            outcome = WindowsProcessError(
                "job termination failed; pinned exits and pipe closure confirmed; "
                + json.dumps([_failure_fields(stage, error) for stage, error in problems], sort_keys=True)
            )
        if isinstance(outcome, UnsafeProcessTreeError):
            for stage, error in problems:
                outcome.record_failure(stage, error)
            for reader in readers:
                if reader.error is not None:
                    outcome.record_failure("reader", reader.error)
    except BaseException as error:
        outcome = _cleanup_error(
            outcome or primary_error, error, "teardown.confirm", cwd, recovery, process, confirmation,
        )
    finally:
        try:
            recovery = exits.metadata()
        except BaseException as error:
            outcome = _cleanup_error(
                outcome or primary_error, error, "process.metadata", cwd, recovery, process, confirmation,
            )
        for reader in readers:
            stop_delivery = True
            try:
                stop_delivery = not reader.released()
            except BaseException as error:
                outcome = _cleanup_error(
                    outcome or primary_error, error, "reader.finalize", cwd, recovery, process, confirmation,
                )
                outcome.pending_resources.append(reader)
            if stop_delivery:
                try:
                    reader.stop_delivery.set()
                except BaseException as error:
                    outcome = _cleanup_error(
                        outcome or primary_error, error, "reader.stop_delivery", cwd, recovery, process, confirmation,
                    )
                    outcome.pending_resources.append(reader)
        root_handle = root.handle if root is not None else int(process._handle)
        try:
            if native().wait(root_handle):
                process.returncode = native().exit_code(root_handle)
                if process.stdin is not None:
                    process.stdin.close()
                process._handle.Close()
            else:
                confirmation["native_exits"] = False
                outcome = _cleanup_error(
                    outcome or primary_error, WindowsProcessError("Launcher exit is unconfirmed"),
                    "launcher.finalize", cwd, recovery, process, confirmation,
                )
                outcome.confirmation["native_exits"] = False
                outcome.pending_launcher = process
        except BaseException as error:
            outcome = _cleanup_error(
                outcome or primary_error, error, "launcher.finalize", cwd, recovery, process, confirmation,
            )
            outcome.pending_launcher = process
        for stage, resource in (("job.close", job), ("process_handles.close", exits)):
            if stage == "job.close" and job_close_attempted:
                continue
            try:
                resource.close()
            except BaseException as error:
                outcome = _cleanup_error(
                    outcome or primary_error, error, stage, cwd, recovery, process, confirmation,
                )
                outcome.pending_resources.append(resource)
        if isinstance(outcome, UnsafeProcessTreeError):
            outcome.confirmation.update(confirmation)
    if outcome is not None:
        raise outcome from None
    return remaining


def run_command(
    command: Sequence[str],
    cwd: Path,
    consume: Callable[[str], None],
    *,
    env: Mapping[str, str] | None = None,
) -> int:
    """Stream a command with inherited stdin; return only after native tree/pipe release.

    Command execution has no new time limit. Completion or interruption starts one
    five-second budget shared by tree termination, native exit waits and pipe drain.
    UnsafeProcessTreeError forbids caller restoration; it is not a Gradle result.
    """
    job = WindowsJob()
    release: ReleaseEvent | None = None
    process: subprocess.Popen | None = None
    exits = ProcessExits()
    readers: list[_Reader] = []
    lines: queue.Queue[str] = queue.Queue(maxsize=128)
    owned = False
    root: ProcessPin | None = None
    recovery: list[dict[str, int]] = []
    confirmation: dict[str, object] = {
        "native_exits": False, "pipes_released": False, "membership_known": False,
    }
    try:
        release = ReleaseEvent()
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [release.handle]}
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-u", "-c", _GATE,
                 str(release.handle), json.dumps(list(command))],
                cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", startupinfo=startup,
            )
            assert process.stdout is not None and process.stderr is not None
            readers = [_Reader(process.stdout, lines), _Reader(process.stderr)]
            root = exits.add_root(process)
            recovery = [root.metadata()]
            job.assign(int(process._handle))
            owned = True
            for reader in readers:
                reader.start()
            release.release()
            release.close()
            while True:
                exits.add_members(job)
                recovery = exits.metadata()
                try:
                    line = lines.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    pass
                else:
                    consume(line)
                if root.signaled():
                    break
        except BaseException as error:
            if process is not None:
                _finish(job, process, root, exits, readers, lines, False, owned, cwd, error)
            raise
        remaining = _finish(job, process, root, exits, readers, lines, True, owned, cwd)
        confirmation.update({"native_exits": True, "pipes_released": True, "membership_known": True})
        # Output delivery may block in the caller; it cannot extend the shutdown budget.
        for line in remaining:
            consume(line)
        control = "".join(readers[1].control)
        try:
            status = json.loads(control)
        except (ValueError, TypeError) as error:
            raise WindowsProcessError("Trusted Windows launcher returned an invalid command status") from error
        if not isinstance(status, dict):
            raise WindowsProcessError("Trusted Windows launcher returned a non-object command status")
        if "error" in status:
            raise OSError(status["errno"], status["error"], status["filename"], status["winerror"])
        if "invalid_command" in status:
            raise ValueError(status["invalid_command"])
        if type(status.get("exit_code")) is not int:
            raise WindowsProcessError("Trusted Windows launcher did not report a command exit code")
        return status["exit_code"]
    finally:
        # An unsafe outcome can exist before it has been raised. Never replace it
        # with an event/handle close failure or a second console interrupt.
        outcome = sys.exception()
        for stage, resource in (
            ("release_event.close", release),
            ("process_handles.close", exits if process is None else None),
            ("job.close", job if process is None else None),
        ):
            if resource is None:
                continue
            try:
                resource.close()
            except BaseException as error:
                proof = outcome.confirmation if isinstance(outcome, UnsafeProcessTreeError) else confirmation
                outcome = _cleanup_error(outcome, error, stage, cwd, recovery, process, proof)
                outcome.pending_resources.append(resource)
        if isinstance(outcome, UnsafeProcessTreeError):
            raise outcome from None
