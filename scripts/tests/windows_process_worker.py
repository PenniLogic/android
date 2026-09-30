"""Finite owned subprocess fixtures for the Windows lifecycle regressions."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quality_gates  # noqa: E402
import windows_processes as windows  # noqa: E402


LEAF_SECONDS = 4.0
STARTUP_SECONDS = 3.0


def leaf(root: Path) -> None:
    api = windows.native()
    created = api.created(api.kernel.GetCurrentProcess())
    with (root / "owned.txt").open("w", encoding="ascii") as held:
        held.write("synthetic held file\n")
        held.flush()
        (root / "leaf-ready.json").write_text(
            json.dumps({"pid": os.getpid(), "created": created}), encoding="ascii",
        )
        print("> Task :app:testDebugUnitTest", flush=True)
        time.sleep(LEAF_SECONDS)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leaf")
    parser.add_argument("--parent")
    parser.add_argument("--scenario")
    parser.add_argument("--mode", choices=("interrupt", "eof", "safe_case", "unsafe_pipe", "unsafe_exit", "refused_case"))
    args = parser.parse_args()
    if args.leaf:
        leaf(Path(args.leaf))
    elif args.parent:
        parent(Path(args.parent))
    elif args.scenario and args.mode:
        scenario(Path(args.scenario), args.mode)
    else:
        parser.error("Choose a finite leaf, parent or scenario")


if __name__ == "__main__":
    main()
