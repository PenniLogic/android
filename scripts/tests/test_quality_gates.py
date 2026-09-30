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

    def scripted_gradle(self, *, up_to_date_plant: str = "UP-TO-DATE", cache_rounds: tuple[str, ...] = ("executed", "FROM-CACHE"), recovery_console: str = "> Task :app:testDebugUnitTest\nBUILD SUCCESSFUL\n"):
        """A run_gradle stand-in that plays the self-test's sequence: planted failure, spotless, lint,
        recovery, the up-to-date plant, then the cached plant round by round."""
        calls: list[tuple] = []
        cache_calls = iter(cache_rounds)
        results_dir = self.app / "build/test-results/testDebugUnitTest"

        def fake_run(tasks, extra=(), force_unit_tests=True):
            calls.append((tasks, tuple(extra), force_unit_tests))
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

        self.assertEqual(6, len(calls))
        cached = json.loads(quality_gates.METRICS_FILE.read_text(encoding="utf-8"))[-1]["cases"][4]
        self.assertEqual(["FROM-CACHE"], [r["outcome"] for r in cached["rounds"]])

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

    def test_run_gradle_streams_and_captures_the_console_and_exit_code(self) -> None:
        # A stand-in wrapper in the sandbox root prints a cached outcome and exits 3.
        if os.name == "nt":
            wrapper = self.root / "gradlew.bat"
            wrapper.write_text(
                "@echo off\r\necho ^> Task :app:testDebugUnitTest FROM-CACHE\r\necho BUILD SUCCESSFUL in 1s\r\nexit /b 3\r\n",
                encoding="ascii",
            )
        else:
            wrapper = self.root / "gradlew"
            wrapper.write_text('#!/bin/sh\necho "> Task :app:testDebugUnitTest FROM-CACHE"\necho "BUILD SUCCESSFUL in 1s"\nexit 3\n', encoding="ascii")
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        printed = io.StringIO()

        with contextlib.redirect_stdout(printed):
            run = quality_gates.run_gradle(("testDebugUnitTest",))

        self.assertEqual(3, run.exit_code)
        self.assertEqual({"testDebugUnitTest": "FROM-CACHE"}, quality_gates.unit_test_outcomes(run.console, ("testDebugUnitTest",)))
        self.assertIn("BUILD SUCCESSFUL in 1s", run.console)
        self.assertIn("> Task :app:testDebugUnitTest FROM-CACHE", printed.getvalue())
        self.assertIn("testDebugUnitTest --rerun --console=plain", printed.getvalue().splitlines()[0])
        self.assertGreaterEqual(run.duration_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
