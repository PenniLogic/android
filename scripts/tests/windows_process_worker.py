"""Finite owned subprocess fixtures for the Windows lifecycle regressions."""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
import traceback
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quality_gates  # noqa: E402
import windows_processes as windows  # noqa: E402


LEAF_SECONDS = 4.0
STARTUP_SECONDS = 3.0


def leaf(root: Path, seconds: float = LEAF_SECONDS) -> None:
    api = windows.native()
    created = api.created(api.kernel.GetCurrentProcess())
    with (root / "owned.txt").open("w", encoding="ascii") as held:
        held.write("synthetic held file\n")
        held.flush()
        (root / "leaf-ready.json").write_text(
            json.dumps({"pid": os.getpid(), "created": created}), encoding="ascii",
        )
        print("> Task :app:testDebugUnitTest", flush=True)
        time.sleep(seconds)
        (root / "leaf-natural-completion").write_text("finite sleep completed\n", encoding="ascii")


def parent(root: Path) -> None:
    import _winapi

    command = [sys.executable, "-I", "-S", "-B", "-u", str(Path(__file__).resolve()), "--leaf", str(root)]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESTDHANDLES
    startup.hStdInput = _winapi.GetStdHandle(_winapi.STD_INPUT_HANDLE)
    startup.hStdOutput = _winapi.GetStdHandle(_winapi.STD_OUTPUT_HANDLE)
    startup.hStdError = _winapi.GetStdHandle(_winapi.STD_ERROR_HANDLE)
    hp, ht, pid, _ = _winapi.CreateProcess(
        None, subprocess.list2cmdline(command), None, None, True, 0, None, str(root), startup,
    )
    try:
        created = windows.native().created(hp)
        deadline = time.monotonic() + STARTUP_SECONDS
        while not (root / "leaf-ready.json").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Finite child did not acknowledge startup")
            time.sleep(0.01)
        ready = json.loads((root / "leaf-ready.json").read_text(encoding="ascii"))
        if ready != {"pid": pid, "created": created} or windows.native().wait(hp):
            raise RuntimeError("The finite child was not alive with its creation-pinned startup identity")
        (root / "parent-handoff.json").write_text(json.dumps(ready), encoding="ascii")
    finally:
        _winapi.CloseHandle(ht)
        _winapi.CloseHandle(hp)


def scenario(root: Path, mode: str) -> None:
    wrapper = root / "gradlew.bat"
    role = "--parent" if mode == "eof" else "--leaf"
    command = f'"{sys.executable}" -I -S -B -u "{Path(__file__).resolve()}" {role} "{root}"'
    wrapper.write_text("@echo off\r\n" + command + "\r\nexit /b 0\r\n", encoding="ascii", newline="")
    real_popen = subprocess.Popen
    roots: list[subprocess.Popen] = []
    root_pins: list[windows.ProcessPin] = []
    child_pins: list[windows.ProcessPin] = []
    watches: list[threading.Thread] = []
    root_exit_times: list[float] = []
    started = time.monotonic()
    result: dict[str, object] = {"mode": mode, "fixture_leaf_seconds": LEAF_SECONDS}
    real_print = print
    planted = root / "planted.kt"
    case_modes = ("safe_case", "unsafe_pipe", "unsafe_exit", "refused_case")

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        roots.append(process)
        pin = windows.ProcessPin.from_process(process)
        root_pins.append(pin)

        def observe_exit() -> None:
            if not windows.native().wait(pin.handle, LEAF_SECONDS + STARTUP_SECONDS):
                raise TimeoutError("The finite source root did not exit")
            root_exit_times.append(time.monotonic() - started)

        watch = threading.Thread(target=observe_exit)
        watches.append(watch)
        watch.start()
        return process

    def console(*args, **kwargs):
        if args and str(args[0]).startswith("> Task"):
            ready = json.loads((root / "leaf-ready.json").read_text(encoding="ascii"))
            pin = windows.ProcessPin.open(ready["pid"], ready["created"])
            child_pins.append(pin)
            if pin.signaled():
                raise RuntimeError("The child exited before the console startup handshake")
            result["child_started"] = pin.metadata()
            result["child_alive_at_callback"] = True
            if mode != "eof":
                raise KeyboardInterrupt
        return None

    try:
        with contextlib.ExitStack() as patches:
            patches.enter_context(mock.patch.object(quality_gates, "ROOT", root))
            patches.enter_context(mock.patch.object(quality_gates.subprocess, "Popen", side_effect=recording_popen))
            patches.enter_context(mock.patch("builtins.print", side_effect=console))
            if mode == "unsafe_pipe":
                patches.enter_context(mock.patch.object(windows._Reader, "released", return_value=False))
            if mode == "unsafe_exit":
                patches.enter_context(mock.patch.object(windows.ProcessExits, "all_signaled", return_value=False))
            if mode == "refused_case":
                patches.enter_context(mock.patch.object(
                    windows.WindowsJob, "terminate", side_effect=OSError("synthetic job termination refusal"),
                ))
            if mode != "eof":
                try:
                    if mode in case_modes:
                        quality_gates.self_test_case(
                            "finite lifecycle", planted, "synthetic planted fixture\n",
                            ("testDebugUnitTest",), None,
                        )
                    else:
                        quality_gates.run_gradle(("testDebugUnitTest",))
                except windows.UnsafeProcessTreeError as error:
                    if mode not in ("unsafe_pipe", "unsafe_exit"):
                        raise
                    result["unsafe_error"] = str(error)
                    result["retained_fixtures"] = error.retained_fixtures
                    result["recovery_processes"] = error.processes
                    result["planted_fixture_retained"] = planted.read_text(encoding="utf-8")
                except windows.WindowsProcessError as error:
                    if mode != "refused_case":
                        raise
                    result["safe_known_lifecycle_error"] = str(error)
                    result["planted_fixture_removed"] = not planted.exists()
                except KeyboardInterrupt:
                    if mode in ("unsafe_pipe", "unsafe_exit", "refused_case"):
                        raise AssertionError("An incomplete/refused teardown was hidden by the original interrupt")
                    result["keyboard_interrupt_preserved"] = True
                    if mode == "safe_case":
                        result["planted_fixture_removed"] = not planted.exists()
                else:
                    raise AssertionError("The console interruption was not propagated")
            else:
                run = quality_gates.run_gradle(("testDebugUnitTest",))
                result["exit_code"] = run.exit_code
                result["console"] = run.console
        result["source_return_seconds"] = time.monotonic() - started
        if len(roots) != 1 or len(child_pins) != 1:
            raise AssertionError("Expected one source launcher and one acknowledged descendant")
        source_root, root_pin, child = roots[0], root_pins[0], child_pins[0]
        result["root"] = root_pin.metadata()
        result["root_signaled_before_source_return"] = root_pin.signaled()
        result["child_signaled_before_source_return"] = child.signaled()
        result["child_native_exit_code_at_return"] = windows.native().exit_code(child.handle)
        result["child_completed_naturally"] = (root / "leaf-natural-completion").exists()
        result["source_read_pipe_closed"] = source_root.stdout is not None and source_root.stdout.closed
        result["source_control_pipe_closed"] = source_root.stderr is None or source_root.stderr.closed
        result["source_process_handle_closed"] = source_root._handle.closed
        try:
            (root / "owned.txt").unlink()
        except PermissionError as error:
            result["held_file_removal"] = {"removed": False, "winerror": error.winerror}
        else:
            result["held_file_removal"] = {"removed": True}
        for watch in watches:
            watch.join(STARTUP_SECONDS)
            if watch.is_alive():
                raise TimeoutError("Source process observer remained alive")
        result["root_exit_seconds"] = root_exit_times[0]
        result["delay_after_root_exit_seconds"] = (
            result["source_return_seconds"] - root_exit_times[0]
        )
        result["process_ids_creation_times_retained"] = all(
            pin.pid > 0 and pin.created > 0 for pin in [root_pin, child]
        )
    finally:
        for watch in watches:
            watch.join(LEAF_SECONDS + STARTUP_SECONDS)
            if watch.is_alive():
                raise TimeoutError("Owned finite process observer did not stop")
        for pin in root_pins + child_pins:
            pin.close()
        for process in roots:
            if not process._handle.closed:
                if not windows.native().wait(int(process._handle)):
                    raise RuntimeError("Owned original source root did not signal before handle cleanup")
                process.wait(timeout=0)
                process._handle.Close()
    real_print("WINDOWS_LIFECYCLE_RESULT=" + json.dumps(result), flush=True)


def finalizer_scenario(root: Path, mode: str, actual_cli: bool = False) -> None:
    """Record a real live tree before a separate owner recovers injected finalizer faults."""
    wrapper = root / "gradlew.bat"
    worker = Path(__file__).resolve()
    wrapper.write_text(
        '@echo off\r\nif "%~1"=="formatterInputScopeRegression" exit /b 0\r\n'
        + f'"{sys.executable}" -I -S -B -u "{worker}" --leaf "{root}" --seconds 12\r\nexit /b 0\r\n',
        encoding="ascii", newline="",
    )
    original_popen = subprocess.Popen
    original_reader_init = windows._Reader.__init__
    original_reader_released = windows._Reader.released
    original_job_init = windows.WindowsJob.__init__
    original_job_close = windows.WindowsJob.close
    original_job_terminate = windows.WindowsJob.terminate
    original_all_signaled = windows.ProcessExits.all_signaled
    original_release_close = windows.ReleaseEvent.close
    original_unsafe_init = windows.UnsafeProcessTreeError.__init__
    original_pin_close = windows.ProcessPin.close
    api = windows.native()
    original_wait = api.wait
    processes, pins, readers, jobs, events, constructed = [], [], [], [], [], []
    event_calls = {}
    child = None
    caught = None
    fault_active = False
    started = time.monotonic()
    result = {"mode": mode, "actual_cli": actual_cli, "finite_leaf_seconds": 12, "finalizer_faults": []}
    failing, formatting, lint = [root / name for name in ("planted.kt", "later-format.kt", "later-lint.xml")]
    real_print = print
    cli_stderr = io.StringIO()
    if actual_cli:
        script = root / "scripts" / "quality_gates.py"
        script.parent.mkdir()
        script.write_bytes(Path(quality_gates.__file__).read_bytes())
        failing = root / r"app\src\test\kotlin\com\pennilogic\android\PlantedFailingTest.kt"
        formatting = root / r"app\src\main\kotlin\com\pennilogic\android\PlantedFormatViolation.kt"
        lint = root / r"app\src\main\res\values\planted_lint_violation.xml"

    def recording_popen(*args, **kwargs):
        nonlocal fault_active
        command = args[0]
        fault_active = "formatterInputScopeRegression" not in command[-1]
        process = original_popen(*args, **kwargs)
        processes.append(process)
        pins.append(windows.ProcessPin.from_process(process))
        return process

    def recording_reader(self, *args, **kwargs):
        original_reader_init(self, *args, **kwargs)
        readers.append(self)

    def recording_job(self):
        original_job_init(self)
        jobs.append(self)

    def recording_unsafe(self, *args, **kwargs):
        original_unsafe_init(self, *args, **kwargs)
        constructed.append(self)

    def event_close(self):
        if not fault_active:
            return original_release_close(self)
        if self not in events:
            events.append(self)
        event_calls[id(self)] = event_calls.get(id(self), 0) + 1
        if event_calls[id(self)] == 1:
            return  # Keep the real event for the late-close counterexample.
        result["finalizer_faults"].append("release_event.close")
        if mode in ("event_interrupt", "multiple_finalizers"):
            raise KeyboardInterrupt("SYNTHETIC_PRIVATE_DIAGNOSTIC_CANARY")
        if mode == "event_oserror":
            raise OSError(5, "SYNTHETIC_PRIVATE_DIAGNOSTIC_CANARY")
        original_release_close(self)

    def wait(handle, seconds=0):
        if mode == "finish_wait_interrupt" and constructed:
            result["finalizer_faults"].append("launcher.finalizer_wait")
            raise KeyboardInterrupt("SYNTHETIC_PRIVATE_DIAGNOSTIC_CANARY")
        return original_wait(handle, seconds)

    def job_close(self):
        if child is None:
            return original_job_close(self)
        if mode == "multiple_finalizers":
            result["finalizer_faults"].append("job.close")
            raise OSError(5, "SYNTHETIC_PRIVATE_DIAGNOSTIC_CANARY")
        # The independently recorded owner closes this real Job after the observation.

    def console(*args, **kwargs):
        nonlocal child
        if args and str(args[0]).startswith("> Task"):
            ready = json.loads((root / "leaf-ready.json").read_text(encoding="ascii"))
            child = windows.ProcessPin.open(ready["pid"], ready["created"])
            if original_wait(child.handle):
                raise AssertionError("The finite child exited before the fault-injection handshake")
            raise KeyboardInterrupt("synthetic original consumer interruption")
        if kwargs.get("file") is sys.stderr:
            return real_print(*args, **kwargs)

    def terminate(job):
        if child is None:
            original_job_terminate(job)

    def all_signaled(exits):
        if child is not None:
            raise OSError(5, "synthetic native wait-proof refusal")
        return original_all_signaled(exits)

    def reader_released(reader):
        if mode == "reader_finalizer_interrupt" and constructed:
            result["finalizer_faults"].append("reader.finalize")
            raise KeyboardInterrupt("SYNTHETIC_PRIVATE_DIAGNOSTIC_CANARY")
        return original_reader_released(reader)

    def pin_close(pin):
        if mode == "pin_close_secondary_interrupt" and constructed:
            result["finalizer_faults"].append("process_handle.close")
            raise KeyboardInterrupt("SYNTHETIC_PRIVATE_DIAGNOSTIC_CANARY")
        original_pin_close(pin)

    try:
        with contextlib.ExitStack() as patches:
            patches.enter_context(mock.patch.object(quality_gates, "ROOT", root))
            patches.enter_context(mock.patch.object(quality_gates, "APP", root))
            for name, path in (("FAILING_TEST", failing), ("FORMAT_VIOLATION", formatting), ("LINT_VIOLATION", lint)):
                patches.enter_context(mock.patch.object(quality_gates, name, path))
            patches.enter_context(mock.patch.object(
                quality_gates, "run_formatter_input_scope", return_value=quality_gates.GradleRun(0, 0, ""),
            ))
            patches.enter_context(mock.patch.object(windows.subprocess, "Popen", side_effect=recording_popen))
            patches.enter_context(mock.patch.object(windows._Reader, "__init__", new=recording_reader))
            patches.enter_context(mock.patch.object(windows._Reader, "released", new=reader_released))
            patches.enter_context(mock.patch.object(windows.ProcessPin, "close", new=pin_close))
            patches.enter_context(mock.patch.object(windows.WindowsJob, "__init__", new=recording_job))
            patches.enter_context(mock.patch.object(windows.WindowsJob, "terminate", new=terminate))
            patches.enter_context(mock.patch.object(windows.WindowsJob, "close", new=job_close))
            patches.enter_context(mock.patch.object(windows.ReleaseEvent, "close", new=event_close))
            patches.enter_context(mock.patch.object(windows.UnsafeProcessTreeError, "__init__", new=recording_unsafe))
            patches.enter_context(mock.patch.object(windows.ProcessExits, "all_signaled", new=all_signaled))
            patches.enter_context(mock.patch.object(api, "wait", side_effect=wait))
            patches.enter_context(mock.patch("builtins.print", side_effect=console))
            try:
                if actual_cli:
                    patches.enter_context(mock.patch.object(sys, "argv", [str(script), "self-test"]))
                    with contextlib.redirect_stderr(cli_stderr):
                        try:
                            runpy.run_path(str(script), run_name="__main__")
                        except SystemExit as error:
                            result["operator_cli_exit_code"] = error.code
                            caught = constructed[-1] if constructed else error
                        except BaseException as error:
                            traceback.print_exception(error, file=cli_stderr)
                            result["operator_cli_exit_code"] = 130 if isinstance(error, KeyboardInterrupt) else 1
                            caught = error
                else:
                    quality_gates.self_test()
            except BaseException as error:
                caught = error
        if caught is None or len(processes) != (2 if actual_cli else 1) or child is None:
            raise AssertionError("The real finalizer case did not reach its owned root/child/error handshake")
        result.update({
            "exception_type": type(caught).__name__,
            "unsafe_constructed": bool(constructed),
            "unsafe_preserved_as_primary": isinstance(caught, windows.UnsafeProcessTreeError),
            "same_constructed_unsafe": any(caught is error for error in constructed),
            "root": pins[-1].metadata(),
            "child": child.metadata(),
            "root_signaled_at_error": original_wait(pins[-1].handle),
            "child_signaled_at_error": original_wait(child.handle),
            "read_pipes_closed_at_error": all(reader.stream.closed for reader in readers),
            "launcher_handle_closed_at_error": processes[-1]._handle.closed,
            "plant_retained_at_error": failing.exists(),
            "later_plants_aborted": not formatting.exists() and not lint.exists(),
            "error_return_seconds": time.monotonic() - started,
            "retained_fixtures": getattr(caught, "retained_fixtures", {}),
            "recovery_processes": getattr(caught, "processes", []),
            "pending_launcher_preserved": getattr(caught, "pending_launcher", None) is processes[-1],
        })
        try:
            (root / "owned.txt").unlink()
        except PermissionError as error:
            result["live_held_file_unlink_winerror"] = error.winerror
        else:
            raise AssertionError("The finite child unexpectedly released its held file before owner recovery")
        if actual_cli:
            result["operator_cli_stderr"] = cli_stderr.getvalue()
        else:
            entry = ast.parse(Path(quality_gates.__file__).read_text(encoding="utf-8")).body[-1]
            stderr = io.StringIO()

            def failing_main():
                raise caught

            with contextlib.redirect_stderr(stderr):
                try:
                    exec(compile(ast.Module(body=[entry], type_ignores=[]), "quality_gates_cli", "exec"), {
                        "__name__": "__main__", "__package__": "", "main": failing_main, "sys": sys,
                    })
                except SystemExit as error:
                    result["operator_cli_exit_code"] = error.code
                except BaseException as error:
                    traceback.print_exception(error, file=stderr)
                    result["operator_cli_exit_code"] = 130 if isinstance(error, KeyboardInterrupt) else 1
            result["operator_cli_stderr"] = stderr.getvalue()
        result["error_notes"] = getattr(caught, "__notes__", [])
    finally:
        deadline = time.monotonic() + windows.TEARDOWN_SECONDS
        for job in jobs:
            if job.handle is not None:
                api.require(api.kernel.TerminateJobObject(job.handle, 97), "owned finalizer-test guardian termination")
        for reader in readers:
            reader.stop_delivery.set()
        for pin in pins + ([child] if child is not None else []):
            if not original_wait(pin.handle, max(0, deadline - time.monotonic())):
                raise RuntimeError("Owned finalizer-test process exit was not confirmed before cleanup")
        for reader in readers:
            reader.thread.join(max(0, deadline - time.monotonic()))
            if reader.thread.is_alive() or not reader.stream.closed:
                raise RuntimeError("Owned finalizer-test reader did not close its pipe")
        for process in processes:
            if not process._handle.closed:
                process.wait(timeout=0)
                process._handle.Close()
        for pin in pins + ([child] if child is not None else []):
            pin.close()
        for event in events:
            original_release_close(event)
        for resource in getattr(caught, "pending_resources", []):
            if isinstance(resource, windows.ProcessExits):
                resource.close()
        for job in jobs:
            if job.handle is not None:
                windows.WindowsJob.close(job)
        result["owner_recovery_exit_pipe_handle_confirmation"] = True
    real_print("WINDOWS_LIFECYCLE_RESULT=" + json.dumps(result), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leaf")
    parser.add_argument("--parent")
    parser.add_argument("--seconds", type=float, default=LEAF_SECONDS)
    parser.add_argument("--scenario")
    parser.add_argument("--actual-cli", action="store_true")
    parser.add_argument("--finalizer-mode", choices=(
        "event_oserror", "event_interrupt", "finish_wait_interrupt", "multiple_finalizers",
        "reader_finalizer_interrupt", "pin_close_secondary_interrupt",
    ))
    parser.add_argument("--mode", choices=("interrupt", "eof", "safe_case", "unsafe_pipe", "unsafe_exit", "refused_case"))
    args = parser.parse_args()
    if args.leaf:
        leaf(Path(args.leaf), args.seconds)
    elif args.parent:
        parent(Path(args.parent))
    elif args.scenario and args.finalizer_mode:
        finalizer_scenario(Path(args.scenario), args.finalizer_mode, args.actual_cli)
    elif args.scenario and args.mode:
        scenario(Path(args.scenario), args.mode)
    else:
        parser.error("Choose a finite leaf, parent or scenario")


if __name__ == "__main__":
    main()
