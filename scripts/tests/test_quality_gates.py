"""Unit tests for scripts/quality_gates.py that do not run Gradle."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quality_gates  # noqa: E402


JUNIT_REPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<testsuite name="com.pennilogic.android.ExampleTest" tests="{tests}" skipped="{skipped}" '
    'failures="{failures}" errors="{errors}" timestamp="2026-09-29T00:00:00" hostname="ci" time="0.1">\n'
    "</testsuite>\n"
)
LINT_REPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<issues format="6" by="lint 9.4.1">\n'
    '    <issue id="UnusedResources" severity="Warning" message="m" category="Performance" priority="3" '
    'summary="s" explanation="e" errorLine1="" errorLine2=""><location file="a.xml" line="1" column="1"/></issue>\n'
    '    <issue id="HardcodedText" severity="Error" message="m" category="I18n" priority="5" '
    'summary="s" explanation="e"><location file="b.xml" line="1" column="1"/></issue>\n'
    "</issues>\n"
)
COVERAGE_REPORT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<report name="debug">\n'
    '    <counter type="INSTRUCTION" missed="3" covered="97"/>\n'
    '    <counter type="LINE" missed="1" covered="40"/>\n'
    "</report>\n"
)


class QualityGatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        patches = [
            mock.patch.object(quality_gates, "ROOT", self.root),
            mock.patch.object(quality_gates, "APP", self.app),
            mock.patch.object(quality_gates, "METRICS_FILE", self.root / "build" / "quality-metrics.json"),
            # The planted-defect paths are derived from APP at import time; point them at the sandbox too.
            mock.patch.object(quality_gates, "FAILING_TEST", self.app / "src/test/kotlin/com/pennilogic/android/PlantedFailingTest.kt"),
            mock.patch.object(quality_gates, "FORMAT_VIOLATION", self.app / "src/main/kotlin/com/pennilogic/android/PlantedFormatViolation.kt"),
            mock.patch.object(quality_gates, "LINT_VIOLATION", self.app / "src/main/res/values/planted_lint_violation.xml"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def write(self, relative: str, content: str) -> Path:
        path = self.app / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_count_unit_tests_sums_all_suites(self) -> None:
        self.write(
            "build/test-results/testDebugUnitTest/TEST-a.xml",
            JUNIT_REPORT.format(tests=4, skipped=0, failures=1, errors=0),
        )
        self.write(
            "build/test-results/testDebugUnitTest/TEST-b.xml",
            JUNIT_REPORT.format(tests=6, skipped=2, failures=0, errors=1),
        )

        self.assertEqual(
            {"tests": 10, "failures": 1, "errors": 1, "skipped": 2},
            quality_gates.count_unit_tests("Debug"),
        )

    def test_count_unit_tests_without_results_is_zero(self) -> None:
        self.assertEqual({"tests": 0, "failures": 0, "errors": 0, "skipped": 0}, quality_gates.count_unit_tests("Release"))

    def test_count_lint_issues_by_severity(self) -> None:
        self.write("build/reports/lint-results-debug.xml", LINT_REPORT)

        self.assertEqual(
            {"fatal": 0, "error": 1, "warning": 1, "informational": 0},
            quality_gates.count_lint_issues("debug"),
        )
        self.assertEqual(
            {"fatal": 0, "error": 0, "warning": 0, "informational": 0},
            quality_gates.count_lint_issues("release"),
        )

    def test_coverage_summary_reads_jacoco_counters(self) -> None:
        self.write("build/reports/coverage/test/debug/report.xml", COVERAGE_REPORT)

        self.assertEqual(
            {"INSTRUCTION": {"missed": 3, "covered": 97}, "LINE": {"missed": 1, "covered": 40}},
            quality_gates.coverage_summary(),
        )

    def test_publish_appends_metrics_and_step_summary(self) -> None:
        summary = self.root / "summary.md"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            quality_gates.publish({"gate": "build", "exit_code": 0, "duration_seconds": 1.5})
            quality_gates.publish(
                {
                    "gate": "test",
                    "exit_code": 1,
                    "duration_seconds": 2.0,
                    "unit_tests": {"debug": {"tests": 3, "failures": 1, "errors": 0, "skipped": 0}, "release": None},
                }
            )

        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(["build", "test"], [record["gate"] for record in records])
        text = summary.read_text(encoding="utf-8")
        self.assertIn("### Android quality gate: build", text)
        self.assertIn("| unit tests (debug) |", text)
        self.assertNotIn("unit tests (release)", text)

    def test_publish_without_step_summary_only_writes_metrics(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            quality_gates.publish({"gate": "lint", "exit_code": 0, "duration_seconds": 0.1})
        self.assertTrue(quality_gates.METRICS_FILE.is_file())

    def test_plant_refuses_to_overwrite_existing_file(self) -> None:
        existing = self.write("src/main/kotlin/Existing.kt", "object Existing\n")

        with self.assertRaises(FileExistsError):
            quality_gates.plant(existing, "replacement")
        self.assertEqual("object Existing\n", existing.read_text(encoding="utf-8"))

    def test_self_test_case_removes_planted_defect_even_when_gradle_raises(self) -> None:
        planted = self.app / "src/test/kotlin/Planted.kt"

        with mock.patch.object(quality_gates, "run_gradle", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                quality_gates.self_test_case("case", planted, "class Planted", ("testDebugUnitTest",), None)
        self.assertFalse(planted.exists())

    def test_self_test_case_reports_failure_as_expected(self) -> None:
        planted = self.app / "src/test/kotlin/Planted.kt"
        report = self.write("build/test-results/testDebugUnitTest/TEST-planted.xml", JUNIT_REPORT.format(tests=1, skipped=0, failures=1, errors=0))

        with mock.patch.object(quality_gates, "run_gradle", return_value=(1, 2.5)) as run:
            outcome = quality_gates.self_test_case("case", planted, "class Planted", ("testDebugUnitTest",), report)

        run.assert_called_once_with(("testDebugUnitTest",))
        self.assertEqual(
            {"case": "case", "exit_code": 1, "duration_seconds": 2.5, "failed_as_expected": True, "report_present": True},
            outcome,
        )
        self.assertFalse(planted.exists())

    def test_self_test_fails_when_a_gate_passes_on_a_planted_defect(self) -> None:
        # The unit-test gate "passes" (exit 0) despite the planted failure: the self-test must fail.
        def fake_run(tasks, extra=()):
            if tasks == ("testDebugUnitTest",):
                self.write(
                    "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.PlantedFailingTest.xml",
                    JUNIT_REPORT.format(tests=1, skipped=1, failures=0, errors=0),
                )
                return 0, 1.0
            return 1, 1.0

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            exit_code = quality_gates.self_test()

        self.assertEqual(1, exit_code)
        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        self.assertFalse(records[-1]["passed"])
        self.assertFalse(records[-1]["cases"][0]["failed_as_expected"])

    def test_self_test_passes_when_every_planted_defect_fails_and_recovery_passes(self) -> None:
        def fake_run(tasks, extra=()):
            if tasks == ("testDebugUnitTest",):
                self.write(
                    "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.PlantedFailingTest.xml",
                    JUNIT_REPORT.format(tests=1, skipped=0, failures=1, errors=0),
                )
                # Another suite legitimately skips a variant-specific test; that must not fail the self-test.
                self.write(
                    "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.contract.BuildConfigContractTest.xml",
                    JUNIT_REPORT.format(tests=4, skipped=1, failures=0, errors=0),
                )
                return 1, 1.0
            if tasks == ("lintDebug",):
                self.write("build/reports/lint-results-debug.xml", LINT_REPORT)
                return 1, 1.0
            if tasks == ("spotlessCheck",):
                return 1, 1.0
            return 0, 3.0  # recovery run

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            exit_code = quality_gates.self_test()

        self.assertEqual(0, exit_code)
        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        self.assertTrue(records[-1]["passed"])
        self.assertTrue(records[-1]["cases"][0]["planted_test_counted_not_skipped"])
        self.assertEqual({"tests": 1, "failures": 1, "errors": 0, "skipped": 0}, records[-1]["cases"][0]["planted_suite"])
        self.assertEqual({"fatal": 0, "error": 1, "warning": 1, "informational": 0}, records[-1]["cases"][2]["lint_issue_counts"])
        for planted in (quality_gates.FAILING_TEST, quality_gates.FORMAT_VIOLATION, quality_gates.LINT_VIOLATION):
            self.assertFalse(planted.exists())

    def test_self_test_refuses_when_planted_paths_already_exist(self) -> None:
        quality_gates.FAILING_TEST.parent.mkdir(parents=True, exist_ok=True)
        quality_gates.FAILING_TEST.write_text("existing", encoding="utf-8")

        with mock.patch.object(quality_gates, "run_gradle") as run:
            self.assertEqual(2, quality_gates.self_test())
        run.assert_not_called()

    def test_main_stops_at_first_failing_gate(self) -> None:
        calls: list[str] = []

        def fake_run_gate(gate):
            calls.append(gate)
            return 1 if gate == "test" else 0

        with mock.patch.object(quality_gates, "run_gate", side_effect=fake_run_gate):
            self.assertEqual(1, quality_gates.main(["all"]))
        self.assertEqual(["build", "test"], calls)

    def test_planted_paths_live_inside_the_sandbox(self) -> None:
        for planted in (quality_gates.FAILING_TEST, quality_gates.FORMAT_VIOLATION, quality_gates.LINT_VIOLATION):
            self.assertTrue(planted.is_relative_to(self.app), planted)
            self.assertFalse(planted.exists())

    def test_gate_task_lists_are_the_documented_ones(self) -> None:
        self.assertEqual(("assembleDebug", "assembleRelease"), quality_gates.GATES["build"])
        self.assertEqual(("testDebugUnitTest", "testReleaseUnitTest"), quality_gates.GATES["test"])
        self.assertEqual(("lintDebug", "lintRelease", "spotlessCheck"), quality_gates.GATES["lint"])
        self.assertEqual(("createDebugUnitTestCoverageReport",), quality_gates.GATES["coverage"])


if __name__ == "__main__":
    unittest.main()
