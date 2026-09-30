"""Check the prerequisites for sideloaded QA of the PenniLogic Android build on a connected device.

Read-only: the script only runs `adb` queries and prints a report; it never changes a device setting,
never touches the restricted-settings state and never installs anything. It makes the checklist in
docs/platform/sideloaded-qa-prerequisites.md executable (android#57, T-AND-07).

Usage:

    python scripts/qa_sideload_prerequisites.py                 # report for the single connected device
    python scripts/qa_sideload_prerequisites.py --serial emulator-5554
    python scripts/qa_sideload_prerequisites.py --package com.pennilogic.android.debug --json
    python scripts/qa_sideload_prerequisites.py --strict        # advisory findings also fail the exit code

Exit codes: 0 every blocking prerequisite is met (advisories may be listed); 1 a blocking prerequisite
is missing (no adb, no or ambiguous device, API below the minimum) or, with --strict, an advisory is
open; 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable

MIN_API = 26
TARGET_API = 36
QA_API_LEVELS = (31, 33, 35, 36)
RESTRICTED_SETTINGS_FROM_API = 33
DEBUG_PACKAGE = "com.pennilogic.android.debug"
RELEASE_PACKAGE = "com.pennilogic.android"
PLAY_STORE = "com.android.vending"
PACKAGE_INSTALLERS = {"com.google.android.packageinstaller", "com.android.packageinstaller"}
# PackageInstaller.PACKAGE_SOURCE_* (API 33) as dumpsys prints them.
PACKAGE_SOURCE_NAMES = {0: "unspecified", 1: "other", 2: "store", 3: "local_file", 4: "downloaded_file"}

Adb = Callable[[list[str]], str]


@dataclass
class Finding:
    level: str  # "ok", "advisory" or "blocking"
    check: str
    detail: str


@dataclass
class Report:
    serial: str | None = None
    api_level: int | None = None
    package: str = DEBUG_PACKAGE
    installed: bool = False
    installer: str | None = None
    package_source: str | None = None
    install_source: str | None = None
    restricted_settings_op: str | None = None
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, check: str, detail: str) -> None:
        self.findings.append(Finding(level, check, detail))

    @property
    def blocking(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.level == "blocking"]

    @property
    def advisories(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.level == "advisory"]

    def exit_code(self, strict: bool) -> int:
        if self.blocking:
            return 1
        if strict and self.advisories:
            return 1
        return 0


def adb_path() -> str | None:
    """Locate adb on PATH or in the Android SDK named by the environment; None when absent."""
    found = shutil.which("adb")
    if found:
        return found
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(variable)
        if sdk:
            candidate = os.path.join(sdk, "platform-tools", "adb.exe" if os.name == "nt" else "adb")
            if os.path.isfile(candidate):
                return candidate
    return None


def make_adb(binary: str, serial: str | None) -> Adb:
    def run(args: list[str]) -> str:
        command = [binary] + (["-s", serial] if serial else []) + args
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
        return completed.stdout
    return run


def parse_devices(output: str) -> list[str]:
    """Serials of devices in the `device` state from `adb devices -l` output."""
    serials = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def parse_int(output: str) -> int | None:
    match = re.search(r"-?\d+", output or "")
    return int(match.group()) if match else None


def parse_setting(output: str) -> str | None:
    value = (output or "").strip()
    return None if value in ("", "null") else value


def parse_installer(output: str) -> str | None:
    """Installer from `pm list packages -i <pkg>` (`package:<pkg>  installer=<name>`)."""
    match = re.search(r"installer=(\S+)", output or "")
    if not match:
        return None
    installer = match.group(1)
    return None if installer == "null" else installer


def parse_package_source(output: str) -> str | None:
    """Package source from `dumpsys package <pkg>` on API 33+, or None when the line is absent."""
    match = re.search(r"packageSource=(\d+)", output or "")
    if not match:
        return None
    return PACKAGE_SOURCE_NAMES.get(int(match.group(1)), match.group(1))


def parse_appop(output: str) -> str | None:
    """Mode of ACCESS_RESTRICTED_SETTINGS from `appops get`, or None when the op was never set."""
    match = re.search(r"ACCESS_RESTRICTED_SETTINGS:\s*(\w+)", output or "")
    return match.group(1) if match else None


def is_package_installer(package: str | None) -> bool:
    """The system package installer under any vendor package name (the intent-based sideload path)."""
    return package is not None and (package in PACKAGE_INSTALLERS or package.endswith(".packageinstaller"))


def classify_install_source(installer: str | None, package_source: str | None) -> str:
    """Mirror of InstallSourceClassifier (app/src/main/.../settings/InstallSourceProbe.kt)."""
    if package_source in ("local_file", "downloaded_file"):
        return "session_file"
    if installer == PLAY_STORE:
        return "play_store"
    if is_package_installer(installer):
        return "legacy_sideload"
    if installer is None:
        return "adb"
    return "session_store"


def collect(adb: Adb, package: str = DEBUG_PACKAGE, serial: str | None = None) -> Report:
    """Run every check against one device and return the report; pure apart from the adb calls."""
    report = Report(serial=serial, package=package)
    api_level = parse_int(adb(["shell", "getprop", "ro.build.version.sdk"]))
    report.api_level = api_level
    if api_level is None:
        report.add("blocking", "api_level", "could not read ro.build.version.sdk from the device")
        return report
    if api_level < MIN_API:
        report.add("blocking", "api_level", f"device API {api_level} is below the app minimum {MIN_API}")
    elif api_level in QA_API_LEVELS:
        report.add("ok", "api_level", f"API {api_level} is in the QA matrix {QA_API_LEVELS}")
    else:
        report.add("advisory", "api_level", f"API {api_level} is outside the QA matrix {QA_API_LEVELS}; results are informational")

    dev_settings = parse_setting(adb(["shell", "settings", "get", "global", "development_settings_enabled"]))
    if dev_settings == "1":
        report.add("ok", "developer_options", "developer options are on (needed for the power-user advanced flow and for adb)")
    else:
        report.add("advisory", "developer_options", "developer options are off; adb sideloading and the advanced flow need them")

    verify_adb = parse_setting(adb(["shell", "settings", "get", "global", "verifier_verify_adb_installs"]))
    if verify_adb == "0":
        report.add("ok", "play_protect_adb", "Play Protect does not verify adb installs on this device")
    else:
        report.add("advisory", "play_protect_adb", "Play Protect verifies adb installs; expect a one-time prompt, never disable Play Protect")

    play_installed = "package:" in adb(["shell", "pm", "list", "packages", PLAY_STORE])
    if play_installed:
        report.add(
            "advisory",
            "developer_verification",
            "Google Play is present: developer verification applies on certified devices (regional from 30 September 2026, "
            "global 2027). Unregistered packages install only through adb or the tester's one-time advanced flow.",
        )
    else:
        report.add("ok", "developer_verification", "no Google Play on this device (AOSP or google_apis image); verification does not apply")

    installed_line = adb(["shell", "pm", "list", "packages", "-i", package])
    report.installed = f"package:{package}" in installed_line
    if not report.installed:
        report.add("ok", "install_source", f"{package} is not installed; install with `adb install -r <apk>` so no restricted setting is locked")
        return report

    report.installer = parse_installer(installed_line)
    dumpsys = adb(["shell", "dumpsys", "package", package]) if api_level >= RESTRICTED_SETTINGS_FROM_API else ""
    report.package_source = parse_package_source(dumpsys)
    report.install_source = classify_install_source(report.installer, report.package_source)
    if report.install_source in ("adb", "play_store", "session_store"):
        report.add("ok", "install_source", f"{package} came from `{report.install_source}`; restricted settings do not lock it")
    else:
        report.add(
            "advisory",
            "install_source",
            f"{package} came from `{report.install_source}`; on API {RESTRICTED_SETTINGS_FROM_API}+ the notification listener is a "
            "restricted setting until the user allows it in App info, or reinstall the same APK with `adb install -r`",
        )

    if api_level >= RESTRICTED_SETTINGS_FROM_API:
        report.restricted_settings_op = parse_appop(adb(["shell", "appops", "get", package, "ACCESS_RESTRICTED_SETTINGS"]))
        mode = report.restricted_settings_op
        if mode in (None, "allow", "default"):
            report.add("ok", "restricted_settings", f"ACCESS_RESTRICTED_SETTINGS is {mode or 'unset'}; sensitive settings are not locked")
        else:
            report.add(
                "advisory",
                "restricted_settings",
                f"ACCESS_RESTRICTED_SETTINGS is {mode}: sensitive settings are locked; the supported unlock is Settings > Apps > "
                "PenniLogic > Allow restricted settings (this script never changes it)",
            )
    return report


def render(report: Report) -> str:
    lines = [
        f"device: {report.serial or 'default'}  api: {report.api_level}  package: {report.package}",
        f"installed: {report.installed}  installer: {report.installer}  package_source: {report.package_source}  "
        f"install_source: {report.install_source}  ACCESS_RESTRICTED_SETTINGS: {report.restricted_settings_op}",
    ]
    for finding in report.findings:
        lines.append(f"[{finding.level:8}] {finding.check}: {finding.detail}")
    lines.append(f"blocking: {len(report.blocking)}  advisory: {len(report.advisories)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", help="adb serial when more than one device is connected")
    parser.add_argument("--package", default=DEBUG_PACKAGE, help=f"application id to inspect (default {DEBUG_PACKAGE})")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--strict", action="store_true", help="advisory findings also produce exit code 1")
    args = parser.parse_args(argv)

    binary = adb_path()
    if binary is None:
        print("blocking: adb not found on PATH or under ANDROID_HOME/platform-tools", file=sys.stderr)
        return 1
    devices = parse_devices(make_adb(binary, None)(["devices", "-l"]))
    serial = args.serial
    if serial is None:
        if len(devices) != 1:
            print(f"blocking: expected exactly one connected device, found {devices}; use --serial", file=sys.stderr)
            return 1
        serial = devices[0]
    elif serial not in devices:
        print(f"blocking: device {serial} is not connected; connected: {devices}", file=sys.stderr)
        return 1

    report = collect(make_adb(binary, serial), package=args.package, serial=serial)
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(render(report))
    return report.exit_code(args.strict)


if __name__ == "__main__":
    sys.exit(main())
