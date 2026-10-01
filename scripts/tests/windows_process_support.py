"""One owned finite worker per lifecycle case; restoration follows confirmed outer shutdown."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import windows_processes as windows


WORKER = Path(__file__).with_name("windows_process_worker.py")


def fixture_environment(root: Path) -> dict[str, str]:
    system = Path(os.environ["SystemRoot"])
    return {
        "SystemRoot": str(system), "WINDIR": str(system),
        "COMSPEC": str(system / "System32" / "cmd.exe"),
        "PATH": str(Path(sys.executable).parent) + ";" + str(system / "System32"),
        "TEMP": str(root), "TMP": str(root),
    }


def finite_scenario(mode: str, *, finalizer: bool = False, actual_cli: bool = False) -> dict:
    root = Path(tempfile.mkdtemp(prefix="android-owned-windows-"))
    output: list[str] = []
    restoration_safe = True
    try:
        try:
            code = windows.run_command(
                [sys.executable, "-I", "-S", "-B", "-W", "error::ResourceWarning", "-u",
                 str(WORKER), "--scenario", str(root), "--finalizer-mode" if finalizer else "--mode", mode,
                 *(["--actual-cli"] if actual_cli else [])],
                root, output.append, env=fixture_environment(root),
            )
        except windows.UnsafeProcessTreeError as error:
            restoration_safe = False
            error.retain_fixture(root)
            raise
        if code != 0:
            raise AssertionError(f"Finite owned worker failed with {code}:\n{''.join(output)}")
        records = [
            line.removeprefix("WINDOWS_LIFECYCLE_RESULT=")
            for line in output if line.startswith("WINDOWS_LIFECYCLE_RESULT=")
        ]
        if len(records) != 1:
            raise AssertionError(f"Expected one finite worker result:\n{''.join(output)}")
        result = json.loads(records[0])
        print("FINITE_WINDOWS_CASE=" + json.dumps(result), flush=True)
        return result
    finally:
        if restoration_safe:
            shutil.rmtree(root)
