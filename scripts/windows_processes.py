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
        super().__init__(f"Windows command teardown unconfirmed: {detail}; recovery directory: {cwd}")
        self.cwd = cwd
        self.processes = processes if processes is not None else []
        self.retained_fixtures: dict[str, str] = {}
        self.pending_launcher: subprocess.Popen | None = None

    def retain_fixture(self, path: Path) -> None:
        location = str(path.resolve())
        recovery = "Confirm the recorded process objects and captured pipes are released before removing this fixture."
        self.retained_fixtures[location] = recovery
        self.add_note(f"Planted fixture retained at {location}. {recovery}")


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
        except OSError as error:
            self.close()
            raise ProcessOwnershipError(*error.args) from error

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
        self.handle: int | None = native().kernel.CreateEventW(None, False, False, None)
        native().require(self.handle, "CreateEventW")
        try:
            native().require(
                native().kernel.SetHandleInformation(self.handle, 1, 1),
                "SetHandleInformation release event",
            )
        except BaseException:
            self.close()
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
        except BaseException:
            native().close(handle)
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
        except BaseException:
            native().close(handle)
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
        for pid in set(job.process_ids()) | self.pending.keys():
            if any(pin.pid == pid and not pin.signaled() for pin in self.pins.values()):
                self.pending.pop(pid, None)
                continue
            try:
                pin = ProcessPin.open(pid)
            except OSError as error:
                if error.winerror == ERROR_INVALID_PARAMETER:
                    self.pending.pop(pid, None)
                    continue  # Windows no longer has a process object for this member.
                if error.winerror == 5:
                    # A terminating member can refuse OpenProcess before its PID disappears.
                    # It remains unconfirmed until a pinned wait or ERROR_INVALID_PARAMETER.
                    self.pending[pid] = error.winerror
                    continue
                raise
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
            finally:
                if pin is not None:
                    pin.close()

    def all_signaled(self) -> bool:
        return bool(self.pins) and not self.pending and all(pin.signaled() for pin in self.pins.values())

    def metadata(self) -> list[dict[str, int]]:
        return [pin.metadata() for pin in self.pins.values()] + [
            {"pid": pid, "unconfirmed_winerror": error} for pid, error in self.pending.items()
        ]

    def close(self) -> None:
        for pin in self.pins.values():
            pin.close()
        self.pins.clear()
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
) -> list[str]:
    deadline = time.monotonic() + TEARDOWN_SECONDS
    problems: list[str] = []
    remaining: list[str] = []
    membership_known = not owned
    outcome: BaseException | None = None
    recovery: list[dict[str, int]] = []
    try:
        for reader in readers:
            reader.close_unstarted()
        if owned:
            try:
                exits.add_members(job)
                membership_known = True
            except (OSError, WindowsProcessError) as error:
                problems.append(f"membership unavailable: {error}")
            try:
                job.terminate()
            except (OSError, KeyboardInterrupt) as error:
                problems.append(f"job termination failed: {error.__class__.__name__}: {error}")
                # Closing requests termination, but never substitutes for the waits below.
                job.close()
        else:
            process.kill()
        while True:
            if owned and job.handle is not None:
                try:
                    exits.add_members(job)
                except (OSError, WindowsProcessError) as error:
                    membership_known = False
                    if not any(problem.startswith("membership unavailable") for problem in problems):
                        problems.append(f"membership unavailable: {error}")
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
        if not safe:
            problems.extend(
                f"captured reader failed: {reader.error.__class__.__name__}: {reader.error}"
                for reader in readers if reader.error is not None
            )
            detail = "; ".join(problems + [
                f"budget={TEARDOWN_SECONDS}s", f"native exits={native_exit}",
                f"pipes released={all(reader.released() for reader in readers)}",
                f"active members={active}",
            ])
            outcome = UnsafeProcessTreeError(detail, cwd, exits.metadata())
        elif problems:
            outcome = WindowsProcessError("; ".join(problems) + "; pinned exits and pipe closure confirmed")
    except (OSError, WindowsProcessError) as error:
        outcome = UnsafeProcessTreeError(
            f"native exit or pipe confirmation failed: {error}", cwd, exits.metadata(),
        )
        outcome.__cause__ = error
    except (KeyboardInterrupt, SystemExit) as error:
        outcome = UnsafeProcessTreeError(
            f"interruption prevented shutdown confirmation within the same {TEARDOWN_SECONDS}s budget",
            cwd, exits.metadata(),
        )
        outcome.__cause__ = error
    finally:
        recovery = exits.metadata()
        for reader in readers:
            if not reader.released():
                reader.stop_delivery.set()
        root_handle = root.handle if root is not None else int(process._handle)
        try:
            if native().wait(root_handle):
                process.returncode = native().exit_code(root_handle)
                if process.stdin is not None:
                    process.stdin.close()
                process._handle.Close()
            elif isinstance(outcome, UnsafeProcessTreeError):
                outcome.pending_launcher = process
        except OSError as error:
            outcome = UnsafeProcessTreeError(f"launcher handle release failed: {error}", cwd, recovery)
            outcome.pending_launcher = process
            outcome.__cause__ = error
        for close in (job.close, exits.close):
            try:
                close()
            except OSError as error:
                outcome = UnsafeProcessTreeError(f"owned handle release failed: {error}", cwd, recovery)
                outcome.__cause__ = error
    if outcome is not None:
        raise outcome
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
            job.assign(int(process._handle))
            owned = True
            for reader in readers:
                reader.start()
            release.release()
            release.close()
            while True:
                exits.add_members(job)
                try:
                    line = lines.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    pass
                else:
                    consume(line)
                if root.signaled():
                    break
        except BaseException:
            if process is not None:
                _finish(job, process, root, exits, readers, lines, False, owned, cwd)
            raise
        remaining = _finish(job, process, root, exits, readers, lines, True, owned, cwd)
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
        if release is not None:
            release.close()
        if process is None:
            exits.close()
            job.close()
