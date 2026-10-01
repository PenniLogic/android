"""Unit tests for scripts/quality_gates.py that do not run Gradle."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quality_gates  # noqa: E402
from quality_gates import GradleRun  # noqa: E402


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
CI_TEST_INVOCATION = "0123456789abcdef0123456789abcdef"
CI_COVERAGE_REPORT = COVERAGE_REPORT.replace(
    '<report name="debug">', f'<report name="debug"><sessioninfo id="{CI_TEST_INVOCATION}" start="100" dump="200"/>',
).replace("</report>", '    <counter type="BRANCH" missed="1" covered="9"/>\n</report>')
# Plain-console excerpts as Gradle 9 prints them (taken from CI job 36685475654 on main).
EXECUTED_BOTH = (
    "> Task :app:compileDebugUnitTestKotlin FROM-CACHE\n"
    "> Task :app:testReleaseUnitTest\n"
    "ExampleTest > passes PASSED\n"
    "> Task :app:testDebugUnitTest\n"
    "ExampleTest > passes PASSED\n"
    "> Task :app:testReleaseUnitTest\n"
    "BUILD SUCCESSFUL in 1m 21s\n"
)
EXECUTED_AND_FAILED = (
    "> Task :app:testDebugUnitTest\n"
    "PlantedFailingTest > planted defect must fail the unit test gate FAILED\n"
    "FAILURE: Build failed with an exception.\n"
    "> Task :app:testDebugUnitTest FAILED\n"
    "BUILD FAILED in 37s\n"
)
RELEASE_FROM_CACHE = "> Task :app:testDebugUnitTest\n> Task :app:testReleaseUnitTest FROM-CACHE\nBUILD SUCCESSFUL in 20s\n"
DEBUG_UP_TO_DATE = "> Task :app:testDebugUnitTest UP-TO-DATE\n> Task :app:createDebugUnitTestCoverageReport\nBUILD SUCCESSFUL\n"
DEBUG_FROM_CACHE = "> Task :app:packageDebugUnitTestForUnitTest UP-TO-DATE\n> Task :app:testDebugUnitTest FROM-CACHE\nBUILD SUCCESSFUL\n"


def gradle_run(exit_code: int, console: str, duration: float = 1.0) -> GradleRun:
    return GradleRun(exit_code, duration, console)


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

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(1, EXECUTED_AND_FAILED, 2.5)) as run:
            outcome = quality_gates.self_test_case("case", planted, "class Planted", ("testDebugUnitTest",), report)

        run.assert_called_once_with(("testDebugUnitTest",))
        self.assertEqual(
            {
                "case": "case",
                "exit_code": 1,
                "duration_seconds": 2.5,
                "failed_as_expected": True,
                "unit_test_tasks": {"testDebugUnitTest": "executed"},
                "unit_tests_executed": True,
                "report_present": True,
            },
            outcome,
        )
        self.assertFalse(planted.exists())

    def test_self_test_case_does_not_count_a_failure_whose_unit_tests_never_ran(self) -> None:
        # Compilation of the planted file failed: Gradle is red, but the test gate did not bite.
        planted = self.app / "src/test/kotlin/Planted.kt"
        console = "> Task :app:compileDebugUnitTestKotlin FAILED\nBUILD FAILED in 9s\n"

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(1, console)):
            outcome = quality_gates.self_test_case("case", planted, "class Planted", ("testDebugUnitTest",), None)

        self.assertFalse(outcome["failed_as_expected"])
        self.assertEqual({"testDebugUnitTest": "not run"}, outcome["unit_test_tasks"])

    def test_self_test_case_without_unit_test_tasks_only_checks_the_exit_code(self) -> None:
        planted = self.app / "src/main/kotlin/Planted.kt"

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(1, "BUILD FAILED\n")):
            outcome = quality_gates.self_test_case("case", planted, "object Planted", ("spotlessCheck",), None)

        self.assertEqual({"case": "case", "exit_code": 1, "duration_seconds": 1.0, "failed_as_expected": True}, outcome)

    def test_self_test_fails_when_a_gate_passes_on_a_planted_defect(self) -> None:
        # The unit-test gate "passes" (exit 0) despite the planted failure: the self-test must fail.
        def fake_run(tasks, extra=(), force_unit_tests=True):
            if tasks == ("formatterInputScopeRegression",):
                return gradle_run(0, "native formatter scope passed\n")
            if tasks == ("testDebugUnitTest",) and force_unit_tests:
                self.write(
                    "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.PlantedFailingTest.xml",
                    JUNIT_REPORT.format(tests=1, skipped=1, failures=0, errors=0),
                )
                return gradle_run(0, "> Task :app:testDebugUnitTest\nBUILD SUCCESSFUL\n")
            return gradle_run(1, "BUILD FAILED\n")

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            exit_code = quality_gates.self_test()

        self.assertEqual(1, exit_code)
        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        self.assertFalse(records[-1]["passed"])
        self.assertFalse(records[-1]["cases"][0]["failed_as_expected"])

    def scripted_gradle(self, *, up_to_date_plant: str = "UP-TO-DATE", cache_rounds: tuple[str, ...] = ("executed", "FROM-CACHE"), recovery_console: str = "> Task :app:testDebugUnitTest\nBUILD SUCCESSFUL\n", formatter_scope_exit: int = 0):
        """Play the native scope probe, planted failure, spotless, lint, recovery and reuse plants."""
        calls: list[tuple] = []
        cache_calls = iter(cache_rounds)
        results_dir = self.app / "build/test-results/testDebugUnitTest"

        def fake_run(tasks, extra=(), force_unit_tests=True):
            calls.append((tasks, tuple(extra), force_unit_tests))
            if tasks == ("formatterInputScopeRegression",):
                return gradle_run(formatter_scope_exit, "native formatter scope probe\n")
            if tasks == ("testDebugUnitTest",) and force_unit_tests:
                self.write(
                    "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.PlantedFailingTest.xml",
                    JUNIT_REPORT.format(tests=1, skipped=0, failures=1, errors=0),
                )
                # Another suite legitimately skips a variant-specific test; that must not fail the self-test.
                self.write(
                    "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.contract.BuildConfigContractTest.xml",
                    JUNIT_REPORT.format(tests=4, skipped=1, failures=0, errors=0),
                )
                return gradle_run(1, EXECUTED_AND_FAILED)
            if tasks == ("lintDebug",):
                self.write("build/reports/lint-results-debug.xml", LINT_REPORT)
                return gradle_run(1, "BUILD FAILED\n")
            if tasks == ("spotlessCheck",):
                return gradle_run(1, "BUILD FAILED\n")
            if tasks == ("testDebugUnitTest", "spotlessCheck", "lintDebug"):
                self.write("build/test-results/testDebugUnitTest/TEST-a.xml", JUNIT_REPORT.format(tests=2, skipped=0, failures=0, errors=0))
                self.write("build/reports/tests/testDebugUnitTest/index.html", "<html/>")
                return gradle_run(0, recovery_console, 3.0)
            self.assertEqual(("testDebugUnitTest",), tasks)
            self.assertFalse(force_unit_tests, "a planted reuse must not carry --rerun")
            if extra == ():
                self.assertTrue(results_dir.is_dir(), "the up-to-date plant keeps the outputs in place")
                return gradle_run(0, f"> Task :app:testDebugUnitTest {up_to_date_plant}\nBUILD SUCCESSFUL\n")
            self.assertEqual(("--build-cache",), tuple(extra))
            self.assertFalse(results_dir.exists(), "the cached plant deletes the task outputs first")
            outcome = next(cache_calls)
            if outcome == "executed":
                self.write("build/test-results/testDebugUnitTest/TEST-a.xml", JUNIT_REPORT.format(tests=2, skipped=0, failures=0, errors=0))
                return gradle_run(0, "> Task :app:testDebugUnitTest\nBUILD SUCCESSFUL\n")
            return gradle_run(0, f"> Task :app:testDebugUnitTest {outcome}\nBUILD SUCCESSFUL\n")

        return fake_run, calls

    def test_self_test_passes_when_every_planted_defect_fails_and_recovery_passes(self) -> None:
        fake_run, calls = self.scripted_gradle()

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            exit_code = quality_gates.self_test()

        self.assertEqual(0, exit_code)
        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        report = records[-1]
        self.assertTrue(report["passed"])
        self.assertEqual({"exit_code": 0, "duration_seconds": 1.0}, report["formatter_input_scope"])
        self.assertEqual(10.0, report["duration_seconds"])
        cases = report["cases"]
        self.assertEqual(
            [
                "failing unit test fails testDebugUnitTest",
                "formatting violation fails spotlessCheck",
                "unused resource fails lintDebug",
                "up-to-date unit-test results are refused",
                "cached unit-test results are refused",
            ],
            [case["case"] for case in cases],
        )
        self.assertTrue(all(case["failed_as_expected"] for case in cases))
        self.assertTrue(cases[0]["planted_test_counted_not_skipped"])
        self.assertTrue(cases[0]["unit_tests_executed"])
        self.assertEqual({"tests": 1, "failures": 1, "errors": 0, "skipped": 0}, cases[0]["planted_suite"])
        self.assertEqual({"fatal": 0, "error": 1, "warning": 1, "informational": 0}, cases[2]["lint_issue_counts"])
        self.assertEqual({"exit_code": 0, "gradle_exit_code": 0, "duration_seconds": 3.0, "unit_test_tasks": {"testDebugUnitTest": "executed"}}, report["recovery"])
        # Gradle was green for both plants; the gate was not.
        self.assertEqual(("UP-TO-DATE", "UP-TO-DATE", 0, 1), (cases[3]["expected_outcome"], cases[3]["observed_outcome"], cases[3]["gradle_exit_code"], cases[3]["exit_code"]))
        self.assertEqual(("FROM-CACHE", "FROM-CACHE", 0, 1), (cases[4]["expected_outcome"], cases[4]["observed_outcome"], cases[4]["gradle_exit_code"], cases[4]["exit_code"]))
        self.assertEqual(["executed", "FROM-CACHE"], [r["outcome"] for r in cases[4]["rounds"]])
        self.assertEqual(
            [
                (
                    ("formatterInputScopeRegression",),
                    (
                        "--init-script",
                        str(self.root / "scripts" / "tests" / "fixtures" / "formatter_input_scope.init.gradle"),
                        "--no-configuration-cache",
                    ),
                    True,
                ),
                (("testDebugUnitTest",), (), True),
                (("spotlessCheck",), (), True),
                (("lintDebug",), (), True),
                (("testDebugUnitTest", "spotlessCheck", "lintDebug"), (), True),
                (("testDebugUnitTest",), (), False),
                (("testDebugUnitTest",), ("--build-cache",), False),
                (("testDebugUnitTest",), ("--build-cache",), False),
            ],
            calls,
        )
        for planted in (quality_gates.FAILING_TEST, quality_gates.FORMAT_VIOLATION, quality_gates.LINT_VIOLATION):
            self.assertFalse(planted.exists())

    def test_self_test_takes_a_warm_cache_hit_in_one_round(self) -> None:
        fake_run, calls = self.scripted_gradle(cache_rounds=("FROM-CACHE",))

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(0, quality_gates.self_test())

        self.assertEqual(7, len(calls))
        cached = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]["cases"][4]
        self.assertEqual(["FROM-CACHE"], [r["outcome"] for r in cached["rounds"]])

    def test_self_test_refuses_a_failed_formatter_scope_probe_even_when_all_five_plants_pass(self) -> None:
        fake_run, _ = self.scripted_gradle(formatter_scope_exit=1)

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.self_test())

        report = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertEqual(1, report["formatter_input_scope"]["exit_code"])
        self.assertFalse(report["passed"])
        self.assertEqual(5, len(report["cases"]))
        self.assertTrue(all(case["failed_as_expected"] for case in report["cases"]))
        self.assertEqual(0, report["recovery"]["exit_code"])

    def test_self_test_fails_when_the_gate_accepts_an_up_to_date_run(self) -> None:
        # The plant was not refused: Gradle said UP-TO-DATE but the fake gate saw an execution. Simulated by
        # a console without a label, which is what a gate that lost its outcome check would accept.
        fake_run, _ = self.scripted_gradle(up_to_date_plant="")

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.self_test())

        report = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertFalse(report["passed"])
        self.assertFalse(report["cases"][3]["failed_as_expected"])
        self.assertEqual("executed", report["cases"][3]["observed_outcome"])

    def test_self_test_fails_when_the_cache_plant_keeps_executing(self) -> None:
        fake_run, _ = self.scripted_gradle(cache_rounds=("executed", "executed"))

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.self_test())

        cached = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]["cases"][4]
        self.assertFalse(cached["failed_as_expected"])
        self.assertEqual(2, len(cached["rounds"]))
        self.assertEqual("executed", cached["observed_outcome"])

    def test_self_test_fails_when_the_recovery_run_reused_unit_tests(self) -> None:
        fake_run, _ = self.scripted_gradle(recovery_console=DEBUG_FROM_CACHE)

        with mock.patch.object(quality_gates, "run_gradle", side_effect=fake_run), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.self_test())

        report = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertFalse(report["passed"])
        self.assertEqual(1, report["recovery"]["exit_code"])
        self.assertEqual(0, report["recovery"]["gradle_exit_code"])
        self.assertIn("testDebugUnitTest FROM-CACHE", report["recovery"]["refused"])

    # A plant is a refusal only when all three legs hold: Gradle green, gate red, observed == planted.
    # Each leg is pinned by its own negative so that `exit_code != 0` alone can never pass for it.

    def test_reused_results_case_is_not_a_refusal_when_gradle_itself_failed(self) -> None:
        red = gradle_run(1, "> Task :app:testDebugUnitTest\n> Task :app:testDebugUnitTest FAILED\nBUILD FAILED\n")

        with mock.patch.object(quality_gates, "run_gradle", return_value=red) as run:
            outcome = quality_gates.reused_results_case("plant", "UP-TO-DATE", clean_outputs=False)

        run.assert_called_once_with(("testDebugUnitTest",), (), force_unit_tests=False)
        self.assertEqual(
            {
                "case": "plant",
                "exit_code": 1,
                "gradle_exit_code": 1,
                "duration_seconds": 1.0,
                "expected_outcome": "UP-TO-DATE",
                "observed_outcome": "executed",
                "rounds": [{"gradle_exit_code": 1, "duration_seconds": 1.0, "outcome": "executed"}],
                "failed_as_expected": False,
            },
            outcome,
        )

    def test_reused_results_case_is_not_a_refusal_when_gradle_failed_without_running_the_task(self) -> None:
        red = gradle_run(1, "> Task :app:compileDebugUnitTestKotlin FAILED\nBUILD FAILED\n")

        with mock.patch.object(quality_gates, "run_gradle", return_value=red):
            outcome = quality_gates.reused_results_case("plant", "UP-TO-DATE", clean_outputs=False)

        # The gate would refuse this run too (exit 1, `not run`), but a red Gradle is not a planted reuse.
        self.assertEqual((1, 1, "not run", False), (outcome["exit_code"], outcome["gradle_exit_code"], outcome["observed_outcome"], outcome["failed_as_expected"]))

    def test_reused_results_case_is_not_a_refusal_when_gradle_is_red_despite_the_planted_label(self) -> None:
        # Gradle reported the reuse and then failed for another reason: label and gate exit match the
        # plant, but the gate did not refuse a green run, which is the only thing the plant proves.
        red = gradle_run(1, "> Task :app:testDebugUnitTest UP-TO-DATE\nFAILURE: Build failed with an exception.\nBUILD FAILED\n")

        with mock.patch.object(quality_gates, "run_gradle", return_value=red):
            outcome = quality_gates.reused_results_case("plant", "UP-TO-DATE", clean_outputs=False)

        self.assertEqual((1, 1, "UP-TO-DATE", False), (outcome["gradle_exit_code"], outcome["exit_code"], outcome["observed_outcome"], outcome["failed_as_expected"]))

    def test_reused_results_case_reports_what_the_gate_decided_not_what_it_expected(self) -> None:
        # A gate that (wrongly) accepted the planted label returns 0; the plant must then say "not refused".
        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, DEBUG_UP_TO_DATE)):
            with mock.patch.object(quality_gates, "gate_exit_code", return_value=0) as gate:
                outcome = quality_gates.reused_results_case("plant", "UP-TO-DATE", clean_outputs=False)

        gate.assert_called_once_with(0, {"testDebugUnitTest": "UP-TO-DATE"})
        self.assertEqual((0, 0, "UP-TO-DATE", False), (outcome["gradle_exit_code"], outcome["exit_code"], outcome["observed_outcome"], outcome["failed_as_expected"]))

    def test_reused_results_case_is_not_a_refusal_for_a_label_other_than_the_planted_one(self) -> None:
        cached = gradle_run(0, DEBUG_FROM_CACHE)

        with mock.patch.object(quality_gates, "run_gradle", return_value=cached):
            outcome = quality_gates.reused_results_case("plant", "UP-TO-DATE", clean_outputs=False)

        self.assertEqual(0, outcome["gradle_exit_code"])
        self.assertEqual(1, outcome["exit_code"])
        self.assertEqual("FROM-CACHE", outcome["observed_outcome"])
        self.assertFalse(outcome["failed_as_expected"])

    def test_reused_results_case_is_a_refusal_only_for_the_planted_label(self) -> None:
        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, DEBUG_UP_TO_DATE)):
            outcome = quality_gates.reused_results_case("plant", "UP-TO-DATE", clean_outputs=False)

        self.assertEqual((0, 1, "UP-TO-DATE", True), (outcome["gradle_exit_code"], outcome["exit_code"], outcome["observed_outcome"], outcome["failed_as_expected"]))

    def test_reused_results_case_cache_plant_stops_after_two_executed_rounds(self) -> None:
        executed = gradle_run(0, "> Task :app:testDebugUnitTest\nBUILD SUCCESSFUL\n")

        with mock.patch.object(quality_gates, "run_gradle", return_value=executed) as run:
            outcome = quality_gates.reused_results_case("plant", "FROM-CACHE", clean_outputs=True, extra=("--build-cache",))

        self.assertEqual(2, run.call_count)
        run.assert_called_with(("testDebugUnitTest",), ("--build-cache",), force_unit_tests=False)
        self.assertEqual(["executed", "executed"], [r["outcome"] for r in outcome["rounds"]])
        self.assertEqual((0, 0, "executed", False), (outcome["gradle_exit_code"], outcome["exit_code"], outcome["observed_outcome"], outcome["failed_as_expected"]))

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
        self.assertEqual(("testDebugUnitTest", "createDebugUnitTestCoverageReport"), quality_gates.GATES["coverage"])

    # --- unit-test results must come from the current run (android#69) ---

    def test_gradle_arguments_force_every_unit_test_task_with_rerun(self) -> None:
        wrapper = quality_gates.gradle_command()
        self.assertEqual(
            wrapper + ["testDebugUnitTest", "--rerun", "testReleaseUnitTest", "--rerun", *quality_gates.COMMON_ARGS],
            quality_gates.gradle_arguments(("testDebugUnitTest", "testReleaseUnitTest")),
        )
        self.assertEqual(
            wrapper + ["testDebugUnitTest", "--rerun", "createDebugUnitTestCoverageReport", *quality_gates.COMMON_ARGS],
            quality_gates.gradle_arguments(quality_gates.GATES["coverage"]),
        )
        self.assertEqual(
            wrapper + ["lintDebug", "lintRelease", "spotlessCheck", *quality_gates.COMMON_ARGS],
            quality_gates.gradle_arguments(quality_gates.GATES["lint"]),
        )
        self.assertEqual(
            wrapper + ["testDebugUnitTest", *quality_gates.COMMON_ARGS, "--build-cache"],
            quality_gates.gradle_arguments(("testDebugUnitTest",), ("--build-cache",), force_unit_tests=False),
        )
        for gate in quality_gates.ORDER:
            arguments = quality_gates.gradle_arguments(quality_gates.GATES[gate])
            for task in quality_gates.UNIT_TEST_TASKS:
                if task in quality_gates.GATES[gate]:
                    self.assertEqual("--rerun", arguments[arguments.index(task) + 1], gate)

    def test_unit_test_outcomes_read_the_plain_console(self) -> None:
        both = ("testDebugUnitTest", "testReleaseUnitTest")
        # Interleaved headers under parallel execution are still one execution per task.
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "executed"}, quality_gates.unit_test_outcomes(EXECUTED_BOTH, both))
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "FROM-CACHE"}, quality_gates.unit_test_outcomes(RELEASE_FROM_CACHE, both))
        self.assertEqual({"testDebugUnitTest": "UP-TO-DATE"}, quality_gates.unit_test_outcomes(DEBUG_UP_TO_DATE, quality_gates.GATES["coverage"]))
        self.assertEqual({"testDebugUnitTest": "FROM-CACHE"}, quality_gates.unit_test_outcomes(DEBUG_FROM_CACHE, ("testDebugUnitTest",)))
        # An executed task that failed is still an execution; a task Gradle never reported is not.
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "not run"}, quality_gates.unit_test_outcomes(EXECUTED_AND_FAILED, both))
        self.assertEqual({"testDebugUnitTest": "not run"}, quality_gates.unit_test_outcomes("", ("testDebugUnitTest",)))
        # Only unit-test tasks are judged; other tasks in the same list are ignored.
        self.assertEqual({}, quality_gates.unit_test_outcomes(DEBUG_UP_TO_DATE, ("createDebugUnitTestCoverageReport",)))
        # A label this script does not know is refused rather than trusted, as is every reuse label Gradle prints.
        self.assertEqual({"testDebugUnitTest": "RESTORED"}, quality_gates.unit_test_outcomes("> Task :app:testDebugUnitTest RESTORED\n", ("testDebugUnitTest",)))
        self.assertEqual(("FROM-CACHE", "UP-TO-DATE", "NO-SOURCE", "SKIPPED"), quality_gates.REUSED_LABELS)
        for label in quality_gates.REUSED_LABELS:
            verdicts = quality_gates.unit_test_outcomes(f"> Task :app:testDebugUnitTest {label}\n", ("testDebugUnitTest",))
            self.assertEqual({"testDebugUnitTest": label}, verdicts)
            self.assertEqual(1, quality_gates.gate_exit_code(0, verdicts), label)
        # Windows line endings and a same-suffix task of another name do not confuse the check.
        self.assertEqual({"testDebugUnitTest": "UP-TO-DATE"}, quality_gates.unit_test_outcomes("> Task :app:testDebugUnitTest UP-TO-DATE\r\n", ("testDebugUnitTest",)))
        self.assertEqual({"testDebugUnitTest": "not run"}, quality_gates.unit_test_outcomes("> Task :app:packageDebugUnitTestForUnitTest UP-TO-DATE\n> Task :app:compileDebugUnitTestKotlin\n", ("testDebugUnitTest",)))

    def test_gate_exit_code_refuses_a_green_gradle_run_with_reused_results(self) -> None:
        self.assertEqual(0, quality_gates.gate_exit_code(0, {"testDebugUnitTest": "executed"}))
        self.assertEqual(1, quality_gates.gate_exit_code(0, {"testDebugUnitTest": "executed", "testReleaseUnitTest": "FROM-CACHE"}))
        self.assertEqual(1, quality_gates.gate_exit_code(0, {"testDebugUnitTest": "not run"}))
        self.assertEqual(3, quality_gates.gate_exit_code(3, {"testDebugUnitTest": "UP-TO-DATE"}))
        self.assertEqual(
            "unit-test results were not produced by this run: testReleaseUnitTest FROM-CACHE",
            quality_gates.refusal({"testDebugUnitTest": "executed", "testReleaseUnitTest": "FROM-CACHE"}),
        )
        # A known reuse label and a never-reported task are named as they are; anything else is flagged as unknown.
        self.assertEqual(
            "unit-test results were not produced by this run: testDebugUnitTest not run, testReleaseUnitTest RESTORED (unknown task outcome)",
            quality_gates.refusal({"testDebugUnitTest": "not run", "testReleaseUnitTest": "RESTORED"}),
        )
        for label in quality_gates.REUSED_LABELS:
            self.assertNotIn("unknown", quality_gates.refusal({"testDebugUnitTest": label}), label)

    def write_unit_test_results(self) -> None:
        self.write("build/test-results/testDebugUnitTest/TEST-a.xml", JUNIT_REPORT.format(tests=5, skipped=0, failures=0, errors=0))
        self.write("build/test-results/testReleaseUnitTest/TEST-a.xml", JUNIT_REPORT.format(tests=5, skipped=1, failures=0, errors=0))

    def test_test_gate_refuses_a_cached_variant_even_when_gradle_is_green(self) -> None:
        self.write_unit_test_results()

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, RELEASE_FROM_CACHE)) as run, mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_gate("test"))

        run.assert_called_once_with(("testDebugUnitTest", "testReleaseUnitTest"))
        record = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertEqual(1, record["exit_code"])
        self.assertEqual(0, record["gradle_exit_code"])
        self.assertEqual("unit-test results were not produced by this run: testReleaseUnitTest FROM-CACHE", record["refused"])
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "FROM-CACHE"}, record["unit_test_tasks"])
        # The restored counts are still recorded for diagnosis, under a red exit code.
        self.assertEqual({"tests": 5, "failures": 0, "errors": 0, "skipped": 1}, record["unit_tests"]["release"])

    def test_test_gate_accepts_results_it_produced(self) -> None:
        self.write_unit_test_results()

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, EXECUTED_BOTH)), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(0, quality_gates.run_gate("test"))

        record = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertEqual(0, record["exit_code"])
        self.assertNotIn("refused", record)
        self.assertNotIn("gradle_exit_code", record)
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "executed"}, record["unit_test_tasks"])

    def test_test_gate_keeps_gradle_failure_code_when_a_test_failed(self) -> None:
        console = "> Task :app:testReleaseUnitTest\n> Task :app:testDebugUnitTest\n> Task :app:testDebugUnitTest FAILED\nBUILD FAILED\n"

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(1, console)), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_gate("test"))

        record = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertNotIn("refused", record)
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "executed"}, record["unit_test_tasks"])

    def test_coverage_gate_refuses_an_up_to_date_test_task(self) -> None:
        self.write("build/test-results/testDebugUnitTest/TEST-a.xml", JUNIT_REPORT.format(tests=5, skipped=0, failures=0, errors=0))
        self.write("build/reports/coverage/test/debug/report.xml", COVERAGE_REPORT)

        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, DEBUG_UP_TO_DATE)) as run, mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_gate("coverage"))

        run.assert_called_once_with(("testDebugUnitTest", "createDebugUnitTestCoverageReport"))
        record = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertEqual("unit-test results were not produced by this run: testDebugUnitTest UP-TO-DATE", record["refused"])
        self.assertEqual({"testDebugUnitTest": "UP-TO-DATE"}, record["unit_test_tasks"])
        self.assertIn("INSTRUCTION", record["coverage"])

    def test_gates_without_unit_tests_do_not_judge_task_outcomes(self) -> None:
        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, "> Task :app:lintDebug UP-TO-DATE\n")), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(0, quality_gates.run_gate("lint"))
            self.assertEqual(0, quality_gates.run_gate("build"))

        for record in json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8")):
            self.assertNotIn("unit_test_tasks", record)
            self.assertNotIn("refused", record)

    def test_markdown_summary_shows_the_refusal_and_task_outcomes(self) -> None:
        text = quality_gates.markdown_summary(
            {
                "gate": "test",
                "exit_code": 1,
                "duration_seconds": 2.0,
                "refused": "unit-test results were not produced by this run: testReleaseUnitTest FROM-CACHE",
                "unit_test_tasks": {"testDebugUnitTest": "executed", "testReleaseUnitTest": "FROM-CACHE"},
                "unit_tests": {"debug": {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}, "release": None},
            }
        )
        self.assertIn("| refused | unit-test results were not produced by this run: testReleaseUnitTest FROM-CACHE |", text)
        self.assertIn('| unit test tasks | {"testDebugUnitTest": "executed", "testReleaseUnitTest": "FROM-CACHE"} |', text)

    def test_remove_unit_test_outputs_deletes_only_that_variant(self) -> None:
        debug = self.write("build/test-results/testDebugUnitTest/TEST-a.xml", "<x/>")
        debug_html = self.write("build/reports/tests/testDebugUnitTest/index.html", "<html/>")
        release = self.write("build/test-results/testReleaseUnitTest/TEST-a.xml", "<x/>")

        quality_gates.remove_unit_test_outputs("Debug")
        quality_gates.remove_unit_test_outputs("Debug")  # idempotent

        self.assertFalse(debug.parent.exists())
        self.assertFalse(debug_html.parent.exists())
        self.assertTrue(release.is_file())

    def test_remove_unit_test_outputs_raises_when_a_directory_cannot_be_removed(self) -> None:
        # An undeletable output directory must surface, not be ignored and later read back as UP-TO-DATE.
        results = self.write("build/test-results/testDebugUnitTest/TEST-a.xml", "<x/>").parent
        self.write("build/reports/tests/testDebugUnitTest/index.html", "<html/>")

        with mock.patch.object(quality_gates.shutil, "rmtree", side_effect=PermissionError("locked")) as rmtree:
            with self.assertRaises(PermissionError):
                quality_gates.remove_unit_test_outputs("Debug")

        rmtree.assert_called_once_with(results)
        self.assertTrue(results.is_dir())

    def test_reused_results_case_propagates_an_undeletable_output_directory(self) -> None:
        self.write("build/test-results/testDebugUnitTest/TEST-a.xml", "<x/>")

        with mock.patch.object(quality_gates.shutil, "rmtree", side_effect=PermissionError("locked")):
            with mock.patch.object(quality_gates, "run_gradle") as run:
                with self.assertRaises(PermissionError):
                    quality_gates.reused_results_case("plant", "FROM-CACHE", clean_outputs=True, extra=("--build-cache",))
        run.assert_not_called()

    def stand_in_wrapper(self, lines: list[str], exit_code: int) -> Path:
        """Write a gradlew stand-in into the sandbox root that prints [lines] and exits with [exit_code]."""
        if os.name == "nt":
            wrapper = self.root / "gradlew.bat"
            body = "@echo off\r\n" + "".join(f"echo {line.replace('>', '^>')}\r\n" for line in lines) + f"exit /b {exit_code}\r\n"
            wrapper.write_text(body, encoding="ascii")
        else:
            wrapper = self.root / "gradlew"
            body = "#!/bin/sh\n" + "".join(f'echo "{line}"\n' for line in lines) + f"exit {exit_code}\n"
            wrapper.write_text(body, encoding="ascii")
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        return wrapper

    def test_run_gradle_streams_and_captures_the_console_and_exit_code(self) -> None:
        # A stand-in wrapper in the sandbox root prints a cached outcome and exits 3.
        self.stand_in_wrapper(["> Task :app:testDebugUnitTest FROM-CACHE", "BUILD SUCCESSFUL in 1s"], 3)
        printed = io.StringIO()

        with contextlib.redirect_stdout(printed):
            run = quality_gates.run_gradle(("testDebugUnitTest",))

        self.assertEqual(3, run.exit_code)
        self.assertEqual({"testDebugUnitTest": "FROM-CACHE"}, quality_gates.unit_test_outcomes(run.console, ("testDebugUnitTest",)))
        self.assertIn("BUILD SUCCESSFUL in 1s", run.console)
        self.assertIn("> Task :app:testDebugUnitTest FROM-CACHE", printed.getvalue())
        self.assertIn("testDebugUnitTest --rerun --console=plain", printed.getvalue().splitlines()[0])
        self.assertGreaterEqual(run.duration_seconds, 0.0)

    def test_run_gradle_kills_the_wrapper_when_the_console_consumer_is_interrupted(self) -> None:
        if os.name == "nt":
            from windows_process_support import finite_scenario

            result = finite_scenario("interrupt")
            self.assertTrue(result["keyboard_interrupt_preserved"])
            self.assertTrue(result["child_alive_at_callback"], "the child acknowledged startup before interruption")
            self.assertTrue(result["process_ids_creation_times_retained"])
            self.assertTrue(result["root_signaled_before_source_return"])
            self.assertTrue(result["child_signaled_before_source_return"])
            self.assertEqual(1, result["child_native_exit_code_at_return"], "the child was killed, not awaited naturally")
            self.assertTrue(result["source_read_pipe_closed"])
            self.assertTrue(result["source_control_pipe_closed"])
            self.assertTrue(result["source_process_handle_closed"])
            self.assertEqual({"removed": True}, result["held_file_removal"])
            return

        # The same contract subprocess.run had: an interrupted gate does not leave Gradle running. The
        # interrupt is raised from the console consumer (print) while the wrapper is still alive.
        self.stand_in_wrapper(["> Task :app:testDebugUnitTest", "BUILD SUCCESSFUL in 1s"], 0)
        real_popen = quality_gates.subprocess.Popen
        processes: list = []

        def recording_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            process.kill = mock.Mock(wraps=process.kill)  # type: ignore[method-assign]
            processes.append(process)
            return process

        def interrupting_print(*args, **kwargs):
            if args and str(args[0]).startswith("> Task"):
                raise KeyboardInterrupt
            return None

        with mock.patch.object(quality_gates.subprocess, "Popen", side_effect=recording_popen):
            with mock.patch("builtins.print", side_effect=interrupting_print):
                with self.assertRaises(KeyboardInterrupt):
                    quality_gates.run_gradle(("testDebugUnitTest",))

        self.assertEqual(1, len(processes))
        processes[0].kill.assert_called_once_with()
        self.assertIsNotNone(processes[0].returncode, "the context manager waited for the killed process")

    def ci_console(self) -> str:
        return "".join(
            f"> Task {path}\n" for path in (
                ":app:assembleDebug", ":app:assembleRelease",
                ":app:testDebugUnitTest", ":app:testReleaseUnitTest",
                ":app:lintDebug", ":app:lintRelease", ":spotlessCheck",
                ":app:lintReportDebug", ":app:lintReportRelease", ":app:createDebugUnitTestCoverageReport",
            )
        ) + "BUILD SUCCESSFUL\n"

    def write_ci_reports(self) -> None:
        for variant, skipped in (("Debug", 1), ("Release", 9)):
            cases = "".join(
                f'<testcase name="case-{index}"><skipped/></testcase>\n' if index < skipped
                else f'<testcase name="case-{index}"/>\n'
                for index in range(198)
            )
            self.write(
                f"build/test-results/test{variant}UnitTest/TEST-current.xml",
                JUNIT_REPORT.format(tests=198, skipped=skipped, failures=0, errors=0).replace("</testsuite>", cases + "</testsuite>"),
            )
            self.write(f"build/reports/lint-results-{variant.lower()}.xml", "<issues/>")
        execution = self.write("build/outputs/unit_test_code_coverage/debugUnitTest/testDebugUnitTest.exec", "")
        identity = CI_TEST_INVOCATION.encode("ascii")
        execution.write_bytes(
            b"\x01\xc0\xc0\x10\x07\x10" + len(identity).to_bytes(2, "big") + identity
            + (100).to_bytes(8, "big") + (200).to_bytes(8, "big")
        )
        self.write(
            "build/reports/coverage/test/debug/report.xml",
            CI_COVERAGE_REPORT,
        )

    def ci_proofs(self, invocation: str = CI_TEST_INVOCATION) -> str:
        lines = []
        for task, paths in quality_gates.ci_producer_paths().items():
            fingerprints = {}
            for path in paths:
                with path.open("rb") as data:
                    fingerprints[path.relative_to(self.root).as_posix()] = quality_gates.hashlib.file_digest(data, "sha256").hexdigest()
            lines.append("CI_NATIVE_EVIDENCE " + json.dumps({"invocation": invocation, "task": task, "files": fingerprints}))
        return "\n".join(lines) + "\n"

    def assert_ci_native_call(self, run) -> None:
        run.assert_called_once()
        tasks, extra = run.call_args.args
        self.assertEqual(quality_gates.ci_tasks(), tasks)
        self.assertEqual("--init-script", extra[0])
        fixture = Path(extra[1])
        self.assertTrue(fixture.is_relative_to(self.app / "build"))
        self.assertFalse(fixture.exists(), "the owned init fixture is removed only after safe native return")

    def invoke_ci(self, console: str | None = None, exit_code: int = 0, tamper=None, proof_tamper=None) -> dict:
        def native(tasks, extra):
            self.assertEqual(quality_gates.ci_tasks(), tasks)
            self.assertTrue(Path(extra[1]).is_file())
            self.assertTrue(all(not path.exists() for path, _ in quality_gates.ci_evidence_paths()))
            self.write_ci_reports()
            proofs = self.ci_proofs()
            if proof_tamper is not None:
                proofs = proof_tamper(proofs)
            if tamper is not None:
                tamper()
            return gradle_run(exit_code, (self.ci_console() if console is None else console) + proofs, 37.5)

        with mock.patch.object(quality_gates, "run_gradle", side_effect=native) as run, \
                mock.patch.object(quality_gates.time, "monotonic", side_effect=(100.0, 140.0)), \
                mock.patch.object(quality_gates.uuid, "uuid4", return_value=quality_gates.uuid.UUID(hex=CI_TEST_INVOCATION)), \
                mock.patch.dict(os.environ, {}, clear=True):
            result = quality_gates.main(["ci"])
        self.assert_ci_native_call(run)
        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(1, len(records))
        self.assertEqual(result, records[0]["exit_code"])
        return records[0]

    def test_ci_shares_only_one_fresh_complete_test_invocation_and_one_wall_time(self) -> None:
        record = self.invoke_ci()
        self.assertEqual(0, record["exit_code"])
        self.assertEqual("ci", record["gate"])
        self.assertEqual(list(quality_gates.ORDER), record["gates"])
        self.assertEqual((40.0, 37.5), (record["duration_seconds"], record["native_duration_seconds"]))
        self.assertEqual(CI_TEST_INVOCATION, record["invocation_id"])
        self.assertEqual({"testDebugUnitTest": "executed", "testReleaseUnitTest": "executed"}, record["unit_test_tasks"])
        self.assertEqual({"tests": 198, "failures": 0, "errors": 0, "skipped": 1}, record["unit_tests"]["debug"])
        self.assertEqual({"tests": 198, "failures": 0, "errors": 0, "skipped": 9}, record["unit_tests"]["release"])
        self.assertEqual({"missed": 1, "covered": 9}, record["coverage"]["BRANCH"])
        self.assertTrue(all(outcome == "executed" for outcome in record["native_task_outcomes"].values()))
        self.assertNotIn("refused", record)

    def test_ci_forces_both_full_test_variants_and_report_producers_only(self) -> None:
        arguments = quality_gates.gradle_arguments(quality_gates.ci_tasks())
        for task in (*quality_gates.UNIT_TEST_TASKS, *quality_gates.CI_REPORT_TASKS):
            self.assertEqual(1, arguments.count(task), task)
            self.assertEqual("--rerun", arguments[arguments.index(task) + 1], task)
        for gate in quality_gates.ORDER:
            for task in quality_gates.GATES[gate]:
                self.assertIn(task, arguments)
        self.assertNotIn("--tests", arguments)
        self.assertNotIn("--rerun-tasks", arguments)
        self.assertNotIn("--no-build-cache", arguments)
        self.assertEqual(1, arguments.count("--no-daemon"))

    def test_ci_retains_real_cached_non_test_task_labels_without_claiming_execution(self) -> None:
        console = self.ci_console().replace(
            "> Task :app:assembleDebug\n", "> Task :app:assembleDebug UP-TO-DATE\n",
        ).replace("> Task :spotlessCheck\n", "> Task :spotlessCheck FROM-CACHE\n")
        record = self.invoke_ci(console)
        self.assertEqual(0, record["exit_code"])
        self.assertEqual("UP-TO-DATE", record["native_task_outcomes"][":app:assembleDebug"])
        self.assertEqual("FROM-CACHE", record["native_task_outcomes"][":spotlessCheck"])

    def test_ci_refuses_every_missing_native_gate_task(self) -> None:
        for line in self.ci_console().splitlines():
            if not line.startswith("> Task "):
                continue
            with self.subTest(task=line):
                record = self.invoke_ci(self.ci_console().replace(line + "\n", ""))
                self.assertEqual(1, record["exit_code"])
                self.assertIn(line.removeprefix("> Task ") + " not run", record["refused"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_foreign_module_task_even_with_complete_current_reports(self) -> None:
        console = self.ci_console().replace(":app:testDebugUnitTest", ":other:testDebugUnitTest")
        record = self.invoke_ci(console)
        self.assertEqual(1, record["exit_code"])
        self.assertEqual("not run", record["unit_test_tasks"]["testDebugUnitTest"])

    def test_ci_refuses_reused_unknown_and_missing_source_test_or_report_outcomes(self) -> None:
        for task in (*quality_gates.UNIT_TEST_TASKS, *quality_gates.CI_REPORT_TASKS):
            for label in (*quality_gates.REUSED_LABELS, "RESTORED"):
                with self.subTest(task=task, label=label):
                    console = self.ci_console().replace(f"> Task :app:{task}\n", f"> Task :app:{task} {label}\n")
                    record = self.invoke_ci(console)
                    self.assertEqual(1, record["exit_code"])
                    self.assertEqual(0, record["gradle_exit_code"])
                    self.assertIn(f":app:{task} {label}", record["refused"])
                    quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_a_reused_header_even_after_an_executed_header(self) -> None:
        record = self.invoke_ci(self.ci_console() + "> Task :app:testDebugUnitTest FROM-CACHE\n")
        self.assertEqual(1, record["exit_code"])
        self.assertEqual("FROM-CACHE", record["unit_test_tasks"]["testDebugUnitTest"])

    def test_ci_preserves_native_failure_code_despite_valid_reports(self) -> None:
        record = self.invoke_ci(exit_code=7)
        self.assertEqual((7, 7), (record["exit_code"], record["gradle_exit_code"]))

    def test_ci_refuses_failed_task_labels_despite_a_successful_native_exit(self) -> None:
        for task in (":app:testDebugUnitTest", ":app:createDebugUnitTestCoverageReport", ":spotlessCheck"):
            with self.subTest(task=task):
                record = self.invoke_ci(self.ci_console().replace(f"> Task {task}\n", f"> Task {task} FAILED\n"))
                self.assertEqual(1, record["exit_code"])
                self.assertIn("FAILED despite a successful native exit", record["refused"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_invalidates_old_evidence_but_preserves_sibling_outputs_and_previous_metrics(self) -> None:
        self.write_ci_reports()
        old_suite = self.write("build/test-results/testDebugUnitTest/TEST-old.xml", "stale")
        sibling = self.write("build/reports/coverage/test/release/sentinel.txt", "preserve")
        metrics = {"gate": "test", "exit_code": 0, "duration_seconds": 1.0}
        quality_gates.publish(metrics)
        with mock.patch.object(quality_gates, "run_gradle", return_value=gradle_run(0, self.ci_console())) as run, \
                mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_ci())
        self.assert_ci_native_call(run)
        self.assertFalse(old_suite.exists())
        self.assertEqual("preserve", sibling.read_text(encoding="utf-8"))
        records = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(metrics, records[0])
        self.assertEqual(1, records[1]["exit_code"], "an earlier green test record is never freshness evidence")
        self.assertIn("missing", records[1]["refused"])

    def test_ci_refuses_each_missing_consumed_output(self) -> None:
        for index in range(len(quality_gates.ci_evidence_paths())):
            def remove():
                path, directory = quality_gates.ci_evidence_paths()[index]
                shutil = quality_gates.shutil
                if directory:
                    shutil.rmtree(path)
                else:
                    path.unlink()
            with self.subTest(output=index):
                record = self.invoke_ci(tamper=remove)
                self.assertEqual(1, record["exit_code"])
                self.assertIn("missing", record["refused"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_malformed_wrong_shape_empty_skipped_and_failing_junit_evidence(self) -> None:
        reports = (
            "", "<issues/>", "<testsuite/>",
            JUNIT_REPORT.format(tests=0, skipped=0, failures=0, errors=0),
            JUNIT_REPORT.format(tests=3, skipped=3, failures=0, errors=0),
            JUNIT_REPORT.format(tests=3, skipped=0, failures=1, errors=0),
            JUNIT_REPORT.format(tests=3, skipped=0, failures=0, errors=1),
            JUNIT_REPORT.format(tests=-1, skipped=0, failures=0, errors=0),
        )
        for source in reports:
            with self.subTest(source=source):
                record = self.invoke_ci(tamper=lambda: self.write("build/test-results/testDebugUnitTest/TEST-current.xml", source))
                self.assertEqual(1, record["exit_code"])
                self.assertIn("refused", record)
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_empty_malformed_duplicate_or_missing_coverage_counters(self) -> None:
        reports = (
            "", "<report/>", "<issues/>",
            CI_COVERAGE_REPORT.replace('<counter type="BRANCH" missed="1" covered="9"/>', ""),
            CI_COVERAGE_REPORT.replace('type="LINE" missed="1"', 'type="LINE" missed="-1"'),
            CI_COVERAGE_REPORT.replace('type="LINE" missed="1"', 'type="LINE" missed="x"'),
            CI_COVERAGE_REPORT.replace("</report>", '<counter type="LINE" missed="1" covered="2"/></report>'),
            CI_COVERAGE_REPORT.replace('type="INSTRUCTION" missed="3" covered="97"', 'type="INSTRUCTION" missed="0" covered="0"'),
        )
        for source in reports:
            with self.subTest(source=source):
                record = self.invoke_ci(tamper=lambda: self.write("build/reports/coverage/test/debug/report.xml", source))
                self.assertEqual(1, record["exit_code"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_previous_run_session_identity_or_metadata_replayed_into_a_fresh_report(self) -> None:
        for original, replacement in (
            (CI_TEST_INVOCATION, "previous-native-session"),
            ('start="100"', 'start="99"'),
            ('dump="200"', 'dump="199"'),
        ):
            def replay():
                path = self.app / "build/reports/coverage/test/debug/report.xml"
                path.write_text(path.read_text(encoding="utf-8").replace(original, replacement), encoding="utf-8")
            with self.subTest(field=original):
                record = self.invoke_ci(tamper=replay)
                self.assertEqual(1, record["exit_code"])
                self.assertIn("does not describe the current debug execution-data session", record["refused"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_unsupported_or_truncated_exec_metadata_even_with_good_xml(self) -> None:
        for data in (
            b"", b"old execution data", b"\x01\xc0\xc0\x10\x07\x10",
            b"\x01\xc0\xc0\x10\x07\x10\x00\x05abc",
            b"\x01\xc0\xc0\x10\x07\x10\x00\x00" + b"\x00" * 16,
        ):
            def tamper():
                quality_gates.ci_evidence_paths()[4][0].write_bytes(data)
            with self.subTest(data=data):
                record = self.invoke_ci(tamper=tamper)
                self.assertEqual(1, record["exit_code"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_junit_attribute_counts_without_matching_real_case_elements(self) -> None:
        record = self.invoke_ci(
            tamper=lambda: self.write(
                "build/test-results/testDebugUnitTest/TEST-current.xml",
                JUNIT_REPORT.format(tests=198, skipped=1, failures=0, errors=0),
            ),
        )
        self.assertEqual(1, record["exit_code"])
        self.assertIn("do not describe the current test cases", record["refused"])

    def test_ci_refuses_both_old_exec_and_old_xml_even_when_their_sessions_agree(self) -> None:
        old_identity = b"previous-native-session"
        def replay():
            quality_gates.ci_evidence_paths()[4][0].write_bytes(
                b"\x01\xc0\xc0\x10\x07\x10" + len(old_identity).to_bytes(2, "big") + old_identity
                + (100).to_bytes(8, "big") + (200).to_bytes(8, "big")
            )
            path = self.app / "build/reports/coverage/test/debug/report.xml"
            path.write_text(CI_COVERAGE_REPORT.replace(CI_TEST_INVOCATION, old_identity.decode()), encoding="utf-8")
        record = self.invoke_ci(tamper=replay)
        self.assertEqual(1, record["exit_code"])
        self.assertIn("does not describe the current debug execution-data session", record["refused"])

    def test_ci_refuses_missing_wrong_run_duplicate_or_malformed_native_producer_evidence(self) -> None:
        mutations = (
            lambda proof: "",
            lambda proof: proof.replace(CI_TEST_INVOCATION, "previous-invocation"),
            lambda proof: proof + proof.splitlines()[0] + "\n",
            lambda proof: "CI_NATIVE_EVIDENCE invalid-json\n",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                record = self.invoke_ci(proof_tamper=mutation)
                self.assertEqual(1, record["exit_code"])
                self.assertIn("native producer", record["refused"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_valid_reports_replaced_after_the_native_producer_observed_them(self) -> None:
        for relative in (
            "build/test-results/testReleaseUnitTest/TEST-current.xml",
            "build/reports/lint-results-debug.xml",
            "build/reports/coverage/test/debug/report.xml",
        ):
            def replace():
                path = self.app / relative
                path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.subTest(relative=relative):
                record = self.invoke_ci(tamper=replace)
                self.assertEqual(1, record["exit_code"])
                self.assertIn("replaced after its current native producer", record["refused"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_a_replayed_pair_retagged_to_the_current_session_after_producer_observation(self) -> None:
        def replace():
            path = quality_gates.ci_evidence_paths()[4][0]
            path.write_bytes(path.read_bytes() + b"previous execution payload")
            report = self.app / "build/reports/coverage/test/debug/report.xml"
            report.write_text(CI_COVERAGE_REPORT + "\n", encoding="utf-8")
        record = self.invoke_ci(tamper=replace)
        self.assertEqual(1, record["exit_code"])
        self.assertIn("replaced after its current native producer", record["refused"])

    def test_ci_init_settings_are_data_not_groovy_interpolation(self) -> None:
        def native(tasks, extra):
            source = Path(extra[1]).read_text(encoding="utf-8")
            self.assertNotIn(str(self.root), source)
            self.assertNotIn("__SETTINGS__", source)
            self.write_ci_reports()
            return gradle_run(0, self.ci_console() + self.ci_proofs())
        with mock.patch.object(quality_gates, "run_gradle", side_effect=native), \
                mock.patch.object(quality_gates.uuid, "uuid4", return_value=quality_gates.uuid.UUID(hex=CI_TEST_INVOCATION)), \
                mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(0, quality_gates.run_ci())

    def test_ci_refuses_empty_execution_data_and_ambiguous_coverage_report(self) -> None:
        for relative, source in (
            ("build/outputs/unit_test_code_coverage/debugUnitTest/testDebugUnitTest.exec", ""),
            ("build/reports/coverage/test/debug/other/report.xml", COVERAGE_REPORT),
        ):
            with self.subTest(relative=relative):
                record = self.invoke_ci(tamper=lambda: self.write(relative, source))
                self.assertEqual(1, record["exit_code"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_refuses_malformed_unknown_severity_and_failing_lint_reports(self) -> None:
        for source in ("", "<report/>", '<issues><issue severity="Unknown"/></issues>', LINT_REPORT):
            with self.subTest(source=source):
                record = self.invoke_ci(tamper=lambda: self.write("build/reports/lint-results-release.xml", source))
                self.assertEqual(1, record["exit_code"])
                quality_gates.METRICS_FILE.unlink()

    def test_ci_checks_all_paths_before_deleting_any_and_never_launches_after_preparation_failure(self) -> None:
        self.write_ci_reports()
        wrong_type = quality_gates.ci_evidence_paths()[3][0]
        wrong_type.unlink()
        wrong_type.mkdir()
        with mock.patch.object(quality_gates, "run_gradle") as run, mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_ci())
        run.assert_not_called()
        self.assertTrue(quality_gates.ci_evidence_paths()[0][0].exists())
        record = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertIn("unexpected evidence output type", record["refused"])

    def test_ci_surfaces_undeletable_evidence_without_launching_gradle(self) -> None:
        self.write_ci_reports()
        with mock.patch.object(quality_gates.shutil, "rmtree", side_effect=PermissionError("locked current output")), \
                mock.patch.object(quality_gates, "run_gradle") as run, mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_ci())
        run.assert_not_called()
        record = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]
        self.assertIn("locked current output", record["refused"])

    def test_ci_refuses_outside_paths_and_mocked_reparse_ancestors(self) -> None:
        with self.assertRaises(quality_gates.InvalidCIEvidence):
            quality_gates.check_ci_evidence_path(self.root / "outside" / "report.xml")
        directory = self.app / "build" / "reports"
        directory.mkdir(parents=True)
        original = Path.lstat
        attributes = mock.Mock(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        def lstat(path):
            return attributes if path == directory else original(path)
        with mock.patch.object(Path, "lstat", lstat):
            with self.assertRaisesRegex(quality_gates.InvalidCIEvidence, "link/reparse"):
                quality_gates.check_ci_evidence_path(directory / "current.xml")

    def test_ci_refuses_literal_link_ancestors_and_preserves_outside_sentinels(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        self.write_ci_reports()
        link = quality_gates.ci_evidence_paths()[5][0] / "alias"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                self.skipTest("Windows symbolic-link privilege unavailable; no literal-link evidence")
            raise
        with mock.patch.object(quality_gates, "run_gradle") as run, mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(1, quality_gates.run_ci())
        run.assert_not_called()
        self.assertEqual("preserve", sentinel.read_text(encoding="utf-8"))
        self.assertTrue(quality_gates.ci_evidence_paths()[0][0].exists(), "preflight refused before any deletion")

    def test_ci_does_not_restore_or_delete_evidence_after_an_unsafe_native_exit(self) -> None:
        from windows_processes import UnsafeProcessTreeError
        unsafe = UnsafeProcessTreeError("fixture", self.root, [])
        with mock.patch.object(quality_gates, "prepare_ci_evidence") as prepare, \
                mock.patch.object(quality_gates, "run_gradle", side_effect=unsafe):
            with self.assertRaises(UnsafeProcessTreeError) as raised:
                quality_gates.run_ci()
        self.assertIs(unsafe, raised.exception)
        prepare.assert_called_once_with()
        self.assertFalse(quality_gates.METRICS_FILE.exists())
        self.assertEqual(1, len(unsafe.retained_fixtures))
        self.assertTrue(all((self.root / path).is_file() for path in unsafe.retained_fixtures))


if __name__ == "__main__":
    unittest.main()
