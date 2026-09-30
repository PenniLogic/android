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


def device_answers(
    api: int,
    installed_line: str = "",
    dumpsys: str = "",
    appop: str = "",
    dev_settings: str = "1",
    verify_adb: str = "0",
    play: bool = False,
) -> dict[str, str]:
    return {
        "shell getprop ro.build.version.sdk": f"{api}\n",
        "shell settings get global development_settings_enabled": f"{dev_settings}\n",
        "shell settings get global verifier_verify_adb_installs": f"{verify_adb}\n",
        "shell pm list packages com.android.vending": "package:com.android.vending\n" if play else "",
        f"shell pm list packages -i {qa.DEBUG_PACKAGE}": installed_line,
        f"shell dumpsys package {qa.DEBUG_PACKAGE}": dumpsys,
        f"shell appops get {qa.DEBUG_PACKAGE} ACCESS_RESTRICTED_SETTINGS": appop,
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

    def test_installer_and_package_source(self) -> None:
        self.assertEqual("com.android.vending", qa.parse_installer("package:com.pennilogic.android.debug  installer=com.android.vending"))
        self.assertIsNone(qa.parse_installer("package:com.pennilogic.android.debug  installer=null"))
        self.assertIsNone(qa.parse_installer(""))
        self.assertEqual("downloaded_file", qa.parse_package_source("  installerPackageName=x\n  packageSource=4\n"))
        self.assertEqual("store", qa.parse_package_source("packageSource=2"))
        self.assertIsNone(qa.parse_package_source("no such line"))

    def test_appop_modes(self) -> None:
        self.assertEqual("deny", qa.parse_appop("ACCESS_RESTRICTED_SETTINGS: deny\n"))
        self.assertEqual("allow", qa.parse_appop("ACCESS_RESTRICTED_SETTINGS: allow; time=+1d2h\n"))
        self.assertIsNone(qa.parse_appop("No operations.\n"))

    def test_install_source_classification_mirrors_the_app(self) -> None:
        self.assertEqual("play_store", qa.classify_install_source("com.android.vending", "store"))
        self.assertEqual("adb", qa.classify_install_source(None, None))
        self.assertEqual("legacy_sideload", qa.classify_install_source("com.google.android.packageinstaller", "unspecified"))
        self.assertEqual("legacy_sideload", qa.classify_install_source("com.miui.packageinstaller", "other"))
        self.assertEqual("session_file", qa.classify_install_source("com.android.chrome", "downloaded_file"))
        self.assertEqual("session_file", qa.classify_install_source("com.android.vending", "local_file"))
        self.assertEqual("session_store", qa.classify_install_source("org.fdroid.fdroid", "store"))


class CollectTest(unittest.TestCase):
    def test_api36_emulator_with_adb_install_has_no_blocking_or_lock(self) -> None:
        adb = FakeAdb(
            device_answers(
                36,
                installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=null\n",
                dumpsys="  installerPackageName=null\n  packageSource=0\n",
                appop="No operations.\n",
            )
        )
        report = qa.collect(adb, serial="emulator-5554")
        self.assertEqual(36, report.api_level)
        self.assertTrue(report.installed)
        self.assertEqual("adb", report.install_source)
        self.assertEqual([], report.blocking)
        self.assertEqual([], report.advisories)
        self.assertEqual(0, report.exit_code(strict=True))
        self.assertIn("shell appops get com.pennilogic.android.debug ACCESS_RESTRICTED_SETTINGS", adb.calls)

    def test_legacy_sideload_on_api36_reports_the_lock_and_the_supported_unlock(self) -> None:
        adb = FakeAdb(
            device_answers(
                36,
                installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=com.google.android.packageinstaller\n",
                dumpsys="  packageSource=4\n",
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

    def test_api31_device_has_no_restricted_settings_check(self) -> None:
        adb = FakeAdb(device_answers(31, installed_line=f"package:{qa.DEBUG_PACKAGE}  installer=com.google.android.packageinstaller\n"))
        report = qa.collect(adb)
        self.assertEqual(31, report.api_level)
        self.assertEqual("legacy_sideload", report.install_source)
        self.assertIsNone(report.restricted_settings_op)
        self.assertFalse(any(call.startswith("shell appops") for call in adb.calls))
        self.assertFalse(any(call.startswith("shell dumpsys") for call in adb.calls))

    def test_api_below_minimum_is_blocking(self) -> None:
        report = qa.collect(FakeAdb(device_answers(25)))
        self.assertEqual(["api_level"], [finding.check for finding in report.blocking])
        self.assertEqual(1, report.exit_code(strict=False))

    def test_unreadable_api_level_is_blocking(self) -> None:
        report = qa.collect(FakeAdb({}))
        self.assertEqual(1, report.exit_code(strict=False))
        self.assertEqual("api_level", report.blocking[0].check)

    def test_not_installed_points_at_adb_install(self) -> None:
        report = qa.collect(FakeAdb(device_answers(35)))
        self.assertFalse(report.installed)
        self.assertIn("adb install -r", [finding.detail for finding in report.findings if finding.check == "install_source"][0])

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

    def test_ambiguous_devices_need_a_serial(self) -> None:
        fake = FakeAdb({"devices -l": "List of devices attached\na device\nb device\n"})
        with mock.patch.object(qa, "adb_path", return_value="adb"), mock.patch.object(qa, "make_adb", return_value=fake):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(1, qa.main([]))
            self.assertIn("--serial", err.getvalue())

    def test_json_output_and_exit_code(self) -> None:
        answers = {"devices -l": "List of devices attached\nemulator-5554 device\n"}
        answers.update(device_answers(36))
        fake = FakeAdb(answers)
        with mock.patch.object(qa, "adb_path", return_value="adb"), mock.patch.object(qa, "make_adb", return_value=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, qa.main(["--json"]))
        self.assertIn('"api_level": 36', out.getvalue())
        self.assertIn('"serial": "emulator-5554"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
