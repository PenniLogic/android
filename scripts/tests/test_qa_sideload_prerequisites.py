"""Unit tests for scripts/qa_sideload_prerequisites.py with a fake adb; no device is needed."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import qa_sideload_prerequisites as qa  # noqa: E402


class FakeAdb:
    """Answers adb queries from a dict keyed by the joined argument list; records every call."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, args: list[str]) -> str:
        key = " ".join(args)
        self.calls.append(key)
        for prefix, answer in self.answers.items():
            if key.startswith(prefix):
                return answer
        return ""


# What `adb install -r app-debug.apk` produces on the Pixel_8_API36 emulator (round-1 review evidence):
# no installing package, initiating package com.android.shell, packageSource=1 (OTHER).
ADB_DUMPSYS = "  installerPackageName=null\n  initiatingPackageName=com.android.shell\n  packageSource=1\n"


def device_answers(
    api: int,
    installed_line: str = "",
    dumpsys: str = "",
    appop: str = "",
    dev_settings: str = "1",
    verify_adb: str = "0",
    play: bool = False,
    package: str = qa.DEBUG_PACKAGE,
) -> dict[str, str]:
    return {
        "shell getprop ro.build.version.sdk": f"{api}\n",
        "shell settings get global development_settings_enabled": f"{dev_settings}\n",
        "shell settings get global verifier_verify_adb_installs": f"{verify_adb}\n",
        "shell pm list packages com.android.vending": "package:com.android.vending\n" if play else "",
        f"shell pm list packages -i {package}": installed_line,
        f"shell dumpsys package {package}": dumpsys,
        f"shell appops get {package} ACCESS_RESTRICTED_SETTINGS": appop,
    }


class ParsingTest(unittest.TestCase):
    def test_devices_in_device_state_only(self) -> None:
        output = (
            "List of devices attached\n"
            "emulator-5554          device product:sdk_gphone64_x86_64 model:sdk_gphone64_x86_64\n"
            "0A1B2C3D               unauthorized\n"
            "192.168.1.10:5555      offline\n"
        )
        self.assertEqual(["emulator-5554"], qa.parse_devices(output))
        self.assertEqual([], qa.parse_devices("List of devices attached\n"))

    def test_package_line_is_an_exact_match_not_a_substring(self) -> None:
        listing = (
            "package:com.pennilogic.android.debug.test  installer=null\n"
            "package:com.pennilogic.android.debug  installer=com.android.vending\n"
        )
        self.assertIsNone(qa.package_line(listing, "com.pennilogic.android"))
        self.assertIsNone(qa.package_line(listing, "com.pennilogic"))
        self.assertEqual(
            "package:com.pennilogic.android.debug  installer=com.android.vending",
            qa.package_line(listing, "com.pennilogic.android.debug"),
        )
        self.assertEqual("package:com.pennilogic.android.debug.test  installer=null", qa.package_line(listing, "com.pennilogic.android.debug.test"))
        self.assertEqual("package:com.android.vending", qa.package_line("package:com.android.vending\n", "com.android.vending"))
        self.assertIsNone(qa.package_line("package:com.android.vending.extra\n", "com.android.vending"))

    def test_installer_initiating_and_package_source(self) -> None:
        self.assertEqual("com.android.vending", qa.parse_installer("package:com.pennilogic.android.debug  installer=com.android.vending"))
        self.assertIsNone(qa.parse_installer("package:com.pennilogic.android.debug  installer=null"))
        self.assertIsNone(qa.parse_installer(None))
        self.assertEqual("com.android.shell", qa.parse_initiating_package(ADB_DUMPSYS))
        self.assertIsNone(qa.parse_initiating_package("  initiatingPackageName=null\n"))
        self.assertIsNone(qa.parse_initiating_package("no such line"))
        self.assertEqual("other", qa.parse_package_source(ADB_DUMPSYS))
        self.assertEqual("downloaded_file", qa.parse_package_source("  installerPackageName=x\n  packageSource=4\n"))
        self.assertEqual("store", qa.parse_package_source("packageSource=2"))
        self.assertIsNone(qa.parse_package_source("no such line"))

    def test_appop_modes(self) -> None:
        self.assertEqual("deny", qa.parse_appop("ACCESS_RESTRICTED_SETTINGS: deny\n"))
        self.assertEqual("allow", qa.parse_appop("ACCESS_RESTRICTED_SETTINGS: allow; time=+1d2h\n"))
        self.assertEqual("errored", qa.parse_appop("ACCESS_RESTRICTED_SETTINGS: errored\n"))
        self.assertIsNone(qa.parse_appop("No operations.\n"))

    def test_install_source_classification_mirrors_the_app(self) -> None:
        self.assertEqual("play_store", qa.classify_install_source("com.android.vending", "com.android.vending", "store"))
        self.assertEqual("adb", qa.classify_install_source(None, "com.android.shell", "other"))
        self.assertEqual("adb", qa.classify_install_source(None, None, None))
        self.assertEqual("legacy_sideload", qa.classify_install_source("com.google.android.packageinstaller", None, "unspecified"))
        self.assertEqual("legacy_sideload", qa.classify_install_source("com.miui.packageinstaller", None, "other"))
        self.assertEqual("legacy_sideload", qa.classify_install_source(None, "com.google.android.packageinstaller", None))
        # An installer that disappeared: no installing package, an initiating package that is not the shell.
        self.assertEqual("legacy_sideload", qa.classify_install_source(None, "com.example.installer", "unspecified"))
        self.assertEqual("session_file", qa.classify_install_source("com.android.chrome", "com.android.chrome", "downloaded_file"))
        self.assertEqual("session_file", qa.classify_install_source("com.android.vending", "com.android.vending", "local_file"))
        self.assertEqual("session_store", qa.classify_install_source("org.fdroid.fdroid", "org.fdroid.fdroid", "store"))

    def test_serials_are_redacted_unless_they_belong_to_an_emulator(self) -> None:
        self.assertEqual("emulator-5554", qa.redact_serial("emulator-5554"))
        self.assertEqual("physical-device", qa.redact_serial("2A051FDH200XYZ"))
        self.assertEqual("physical-device", qa.redact_serial("192.168.1.10:5555"))
        self.assertIsNone(qa.redact_serial(None))


class CollectTest(unittest.TestCase):
    def test_api36_emulator_with_adb_install_has_no_blocking_and_no_lock_claim(self) -> None:
        adb = FakeAdb(
            device_answers(
                36,
                installed_line=f"package:{qa.DEBUG_PACKAGE}.test  installer=null\npackage:{qa.DEBUG_PACKAGE}  installer=null\n",
                dumpsys=ADB_DUMPSYS,
                appop="No operations.\n",
            )
        )
        report = qa.collect(adb, serial="emulator-5554")
        self.assertEqual("emulator-5554", report.device)
        self.assertEqual(36, report.api_level)
        self.assertTrue(report.installed)
        self.assertEqual("com.android.shell", report.initiating_installer)
        self.assertEqual("other", report.package_source)
        self.assertEqual("adb", report.install_source)
        self.assertEqual([], report.blocking)
        self.assertEqual([], report.advisories)
        restricted = [finding for finding in report.findings if finding.check == "restricted_settings"][0]
        self.assertIn("undecided, not unlocked", restricted.detail)
        self.assertEqual(0, report.exit_code(strict=True))
        self.assertIn("shell appops get com.pennilogic.android.debug ACCESS_RESTRICTED_SETTINGS", adb.calls)

    def test_release_package_is_not_installed_when_only_the_debug_build_is_present(self) -> None:
        listing = f"package:{qa.DEBUG_PACKAGE}  installer=null\npackage:{qa.DEBUG_PACKAGE}.test  installer=null\n"
        adb = FakeAdb(device_answers(36, installed_line=listing, dumpsys=ADB_DUMPSYS, package=qa.RELEASE_PACKAGE))
        report = qa.collect(adb, package=qa.RELEASE_PACKAGE)
        self.assertFalse(report.installed)
        self.assertIsNone(report.install_source)
        self.assertFalse(any(call.startswith("shell dumpsys") for call in adb.calls))
        self.assertIn("is not installed", [finding.detail for finding in report.findings if finding.check == "install_source"][0])

    def test_installer_is_read_from_the_exact_line_even_when_the_test_apk_is_listed_first(self) -> None:
        listing = f"package:{qa.DEBUG_PACKAGE}.test  installer=null\npackage:{qa.DEBUG_PACKAGE}  installer=com.android.vending\n"
        adb = FakeAdb(device_answers(36, installed_line=listing, dumpsys="  initiatingPackageName=com.android.vending\n  packageSource=2\n"))
        report = qa.collect(adb)
        self.assertTrue(report.installed)
        self.assertEqual("com.android.vending", report.installer)
        self.assertEqual("play_store", report.install_source)

    def test_play_presence_needs_the_exact_vending_line(self) -> None:
        answers = device_answers(36)
        answers["shell pm list packages com.android.vending"] = "package:com.android.vending.extra\n"
        report = qa.collect(FakeAdb(answers))
        self.assertIn("no Google Play", [finding.detail for finding in report.findings if finding.check == "developer_verification"][0])

    def test_legacy_sideload_on_api36_reports_the_lock_and_the_supported_unlock(self) -> None:
        adb = FakeAdb(
            device_answers(
                36,
                installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=com.google.android.packageinstaller\n",
                dumpsys="  initiatingPackageName=com.google.android.packageinstaller\n  packageSource=4\n",
                appop="ACCESS_RESTRICTED_SETTINGS: deny\n",
                play=True,
            )
        )
        report = qa.collect(adb)
        self.assertEqual("session_file", report.install_source)
        self.assertEqual("deny", report.restricted_settings_op)
        checks = {finding.check for finding in report.advisories}
        self.assertEqual({"developer_verification", "install_source", "restricted_settings"}, checks)
        details = " ".join(finding.detail for finding in report.advisories)
        self.assertIn("Allow restricted settings", details)
        self.assertIn("adb install -r", details)
        self.assertNotIn("appops set", details)
        self.assertEqual(0, report.exit_code(strict=False))
        self.assertEqual(1, report.exit_code(strict=True))

    def test_disappeared_installer_is_a_sideload_not_adb(self) -> None:
        adb = FakeAdb(
            device_answers(
                36,
                installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=null\n",
                dumpsys="  installerPackageName=null\n  initiatingPackageName=com.example.installer\n  packageSource=0\n",
                appop="ACCESS_RESTRICTED_SETTINGS: errored\n",
            )
        )
        report = qa.collect(adb)
        self.assertEqual("legacy_sideload", report.install_source)
        self.assertEqual({"install_source", "restricted_settings"}, {finding.check for finding in report.advisories})

    def test_api33_unset_op_means_not_locked_while_api35_unset_means_undecided(self) -> None:
        for api, expected in ((33, "not locked"), (34, "not locked"), (35, "undecided"), (36, "undecided")):
            adb = FakeAdb(device_answers(api, installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=null\n", dumpsys=ADB_DUMPSYS, appop="No operations.\n"))
            report = qa.collect(adb)
            detail = [finding.detail for finding in report.findings if finding.check == "restricted_settings"][0]
            self.assertIn(expected, detail, f"api {api}")
        allowed = qa.collect(
            FakeAdb(device_answers(36, installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=null\n", dumpsys=ADB_DUMPSYS, appop="ACCESS_RESTRICTED_SETTINGS: allow\n"))
        )
        self.assertIn("has unlocked", [finding.detail for finding in allowed.findings if finding.check == "restricted_settings"][0])

    def test_api31_device_has_no_restricted_settings_check_but_reads_dumpsys(self) -> None:
        adb = FakeAdb(
            device_answers(
                31,
                installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=com.google.android.packageinstaller\n",
                dumpsys="  initiatingPackageName=com.google.android.packageinstaller\n",
            )
        )
        report = qa.collect(adb)
        self.assertEqual(31, report.api_level)
        self.assertEqual("legacy_sideload", report.install_source)
        self.assertIsNone(report.package_source)
        self.assertIsNone(report.restricted_settings_op)
        self.assertFalse(any(call.startswith("shell appops") for call in adb.calls))

    def test_api_below_minimum_is_blocking(self) -> None:
        report = qa.collect(FakeAdb(device_answers(25)))
        self.assertEqual(["api_level"], [finding.check for finding in report.blocking])
        self.assertEqual(1, report.exit_code(strict=False))

    def test_unreadable_api_level_is_blocking(self) -> None:
        report = qa.collect(FakeAdb({}))
        self.assertEqual(1, report.exit_code(strict=False))
        self.assertEqual("api_level", report.blocking[0].check)

    def test_a_malformed_package_never_reaches_the_device_shell(self) -> None:
        adb = FakeAdb(device_answers(36))
        with self.assertRaises(ValueError):
            qa.collect(adb, package="com.pennilogic.android; settings put global adb_enabled 0")
        self.assertEqual([], adb.calls)

    def test_physical_device_serial_is_withheld_from_the_report(self) -> None:
        report = qa.collect(FakeAdb(device_answers(36)), serial="2A051FDH200XYZ")
        self.assertEqual("physical-device", report.device)
        self.assertNotIn("2A051FDH200XYZ", qa.render(report))

    def test_render_lists_every_finding(self) -> None:
        report = qa.collect(FakeAdb(device_answers(36, dev_settings="0", verify_adb="1")))
        text = qa.render(report)
        self.assertIn("[advisory] developer_options", text)
        self.assertIn("[advisory] play_protect_adb", text)
        self.assertIn("never disable Play Protect", text)


class MainTest(unittest.TestCase):
    def test_missing_adb_is_blocking(self) -> None:
        with mock.patch.object(qa, "adb_path", return_value=None):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(1, qa.main([]))
        self.assertIn("adb not found", err.getvalue())

    def test_malformed_package_is_a_usage_error_before_adb_is_touched(self) -> None:
        with mock.patch.object(qa, "adb_path") as adb_path:
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(2, qa.main(["--package", "x; settings put global adb_enabled 0"]))
            adb_path.assert_not_called()
        self.assertIn("must be an Android package name", err.getvalue())
        with mock.patch.object(qa, "adb_path") as adb_path:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, qa.main(["--package", "nodots"]))
            adb_path.assert_not_called()

    def test_ambiguous_devices_need_a_serial_and_serials_are_not_echoed(self) -> None:
        fake = FakeAdb({"devices -l": "List of devices attached\n2A051FDH200XYZ device\nemulator-5554 device\n"})
        with mock.patch.object(qa, "adb_path", return_value="adb"), mock.patch.object(qa, "make_adb", return_value=fake):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(1, qa.main([]))
            self.assertIn("--serial", err.getvalue())
            self.assertNotIn("2A051FDH200XYZ", err.getvalue())

    def test_json_output_and_exit_code(self) -> None:
        answers = {"devices -l": "List of devices attached\nemulator-5554 device\n"}
        answers.update(device_answers(36))
        fake = FakeAdb(answers)
        with mock.patch.object(qa, "adb_path", return_value="adb"), mock.patch.object(qa, "make_adb", return_value=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, qa.main(["--json"]))
        self.assertIn('"api_level": 36', out.getvalue())
        self.assertIn('"device": "emulator-5554"', out.getvalue())
        self.assertNotIn('"serial"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
