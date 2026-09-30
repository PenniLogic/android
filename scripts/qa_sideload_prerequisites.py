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
open; 2 usage error (for example a value that is not an Android package name).

The report never contains a hardware serial: emulator serials are printed as they are, a physical
device is reported as `physical-device`, so the output can be pasted into a pull request.
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
ENHANCED_CONFIRMATION_FROM_API = 35
DEBUG_PACKAGE = "com.pennilogic.android.debug"
RELEASE_PACKAGE = "com.pennilogic.android"
PLAY_STORE = "com.android.vending"
SHELL_PACKAGE = "com.android.shell"
PACKAGE_INSTALLERS = {"com.google.android.packageinstaller", "com.android.packageinstaller"}
# PackageInstaller.PACKAGE_SOURCE_* (API 33) as dumpsys prints them.
PACKAGE_SOURCE_NAMES = {0: "unspecified", 1: "other", 2: "store", 3: "local_file", 4: "downloaded_file"}
# Android package-name grammar: dot-separated Java identifiers, at least two segments.
PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
EMULATOR_SERIAL = re.compile(r"^emulator-\d+$")

Adb = Callable[[list[str]], str]


@dataclass
class Finding:
    level: str  # "ok", "advisory" or "blocking"
    check: str
    detail: str


@dataclass
class Report:
    """The report; `device` is the emulator serial or `physical-device`, never a hardware serial."""

    device: str | None = None
    api_level: int | None = None
    package: str = DEBUG_PACKAGE
    installed: bool = False
    installer: str | None = None
    initiating_installer: str | None = None
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


def redact_serial(serial: str | None) -> str | None:
    """Emulator serials are not identifying; anything else is a hardware serial and is withheld."""
    if serial is None:
        return None
    return serial if EMULATOR_SERIAL.match(serial) else "physical-device"


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


def package_line(output: str, package: str) -> str | None:
    """The `package:<pkg>` line for exactly `package` in `pm list packages` output, or None.

    `pm list packages FILTER` is a substring filter, so `com.pennilogic.android` also lists
    `com.pennilogic.android.debug` and `com.pennilogic.android.debug.test`; only the exact line counts.
    """
    pattern = re.compile(r"^package:" + re.escape(package) + r"(\s|$)")
    for line in (output or "").splitlines():
        if pattern.match(line.strip()):
            return line.strip()
    return None


def parse_installer(line: str | None) -> str | None:
    """Installer from one exact `package:<pkg>  installer=<name>` line."""
    match = re.search(r"installer=(\S+)", line or "")
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


def parse_initiating_package(output: str) -> str | None:
    """`initiatingPackageName=` from `dumpsys package <pkg>` (API 30+), or None."""
    match = re.search(r"initiatingPackageName=(\S+)", output or "")
    if not match:
        return None
    value = match.group(1)
    return None if value == "null" else value


def parse_appop(output: str) -> str | None:
    """Mode of ACCESS_RESTRICTED_SETTINGS from `appops get`, or None when the op was never set."""
    match = re.search(r"ACCESS_RESTRICTED_SETTINGS:\s*(\w+)", output or "")
    return match.group(1) if match else None


def is_package_installer(package: str | None) -> bool:
    """The system package installer under any vendor package name (the intent-based sideload path)."""
    return package is not None and (package in PACKAGE_INSTALLERS or package.endswith(".packageinstaller"))


def classify_install_source(installer: str | None, initiating: str | None, package_source: str | None) -> str:
    """Mirror of InstallSourceClassifier (app/src/main/.../settings/InstallSourceProbe.kt): same inputs, same order."""
    if package_source in ("local_file", "downloaded_file"):
        return "session_file"
    if installer == PLAY_STORE:
        return "play_store"
    if is_package_installer(installer) or is_package_installer(initiating):
        return "legacy_sideload"
    if installer is None and initiating in (None, SHELL_PACKAGE):
        return "adb"
    if installer is None:
        return "legacy_sideload"
    return "session_store"


def collect(adb: Adb, package: str = DEBUG_PACKAGE, serial: str | None = None) -> Report:
    """Run every check against one device and return the report; pure apart from the adb calls."""
    if not PACKAGE_NAME.match(package):
        raise ValueError(f"not an Android package name: {package!r}")
    report = Report(device=redact_serial(serial), package=package)
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

    play_installed = package_line(adb(["shell", "pm", "list", "packages", PLAY_STORE]), PLAY_STORE) is not None
    if play_installed:
        report.add(
            "advisory",
            "developer_verification",
            "Google Play is present: developer verification applies on certified devices (regional from 30 September 2026, "
            "global 2027). Unregistered packages install only through adb or the tester's one-time advanced flow.",
        )
    else:
        report.add("ok", "developer_verification", "no Google Play on this device (AOSP or google_apis image); verification does not apply")

    exact_line = package_line(adb(["shell", "pm", "list", "packages", "-i", package]), package)
    report.installed = exact_line is not None
    if not report.installed:
        report.add("ok", "install_source", f"{package} is not installed; install with `adb install -r <apk>` so no restricted setting is locked")
        return report

    report.installer = parse_installer(exact_line)
    dumpsys = adb(["shell", "dumpsys", "package", package]) if api_level >= 30 else ""
    report.initiating_installer = parse_initiating_package(dumpsys)
    report.package_source = parse_package_source(dumpsys) if api_level >= RESTRICTED_SETTINGS_FROM_API else None
    report.install_source = classify_install_source(report.installer, report.initiating_installer, report.package_source)
    if report.install_source in ("adb", "play_store", "session_store"):
        report.add(
            "ok",
            "install_source",
            f"{package} came from `{report.install_source}`; the platform's restricted-settings lock does not apply to this "
            "source on an AOSP-default device",
        )
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
        if mode == "allow":
            report.add("ok", "restricted_settings", "ACCESS_RESTRICTED_SETTINGS is allow; the user has unlocked restricted settings for the app")
        elif mode in (None, "default"):
            if api_level >= ENHANCED_CONFIRMATION_FROM_API:
                report.add(
                    "ok",
                    "restricted_settings",
                    f"ACCESS_RESTRICTED_SETTINGS is {mode or 'unset'}: the platform has not marked this install yet; on API "
                    f"{ENHANCED_CONFIRMATION_FROM_API}+ the lock is decided from the install source (`install_source` above) when a "
                    "restricted setting is first toggled, so unset means undecided, not unlocked",
                )
            else:
                report.add(
                    "ok",
                    "restricted_settings",
                    f"ACCESS_RESTRICTED_SETTINGS is {mode or 'unset'}; on API 33/34 the platform sets it at install time for "
                    "file-sourced installs, so this install is not locked",
                )
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
        f"device: {report.device or 'default'}  api: {report.api_level}  package: {report.package}",
        f"installed: {report.installed}  installer: {report.installer}  initiating: {report.initiating_installer}  "
        f"package_source: {report.package_source}  install_source: {report.install_source}  "
        f"ACCESS_RESTRICTED_SETTINGS: {report.restricted_settings_op}",
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

    # Validated before anything reaches `adb shell`, which joins its arguments into one device shell line.
    if not PACKAGE_NAME.match(args.package):
        print(f"usage: --package must be an Android package name (dot-separated identifiers), got {args.package!r}", file=sys.stderr)
        return 2
    if args.serial is not None and not re.match(r"^[A-Za-z0-9._:-]+$", args.serial):
        print("usage: --serial must be an adb serial (letters, digits, '.', '_', ':' and '-')", file=sys.stderr)
        return 2

    binary = adb_path()
    if binary is None:
        print("blocking: adb not found on PATH or under ANDROID_HOME/platform-tools", file=sys.stderr)
        return 1
    devices = parse_devices(make_adb(binary, None)(["devices", "-l"]))
    serial = args.serial
    if serial is None:
        if len(devices) != 1:
            print(f"blocking: expected exactly one connected device, found {len(devices)}; use --serial", file=sys.stderr)
            return 1
        serial = devices[0]
    elif serial not in devices:
        print(f"blocking: device {redact_serial(serial)} is not connected ({len(devices)} device(s) connected)", file=sys.stderr)
        return 1

    report = collect(make_adb(binary, serial), package=args.package, serial=serial)
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(render(report))
    return report.exit_code(args.strict)


if __name__ == "__main__":
    sys.exit(main())
