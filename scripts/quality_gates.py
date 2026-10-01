"""Run the Android quality gates, publish pipeline metrics and self-test the gates on planted defects.

Usage (from a clean clone, JDK 21 and Android SDK 36 present, nothing else configured):

    python scripts/quality_gates.py build       # assembleDebug + assembleRelease
    python scripts/quality_gates.py test        # unit tests for debug and release
    python scripts/quality_gates.py lint        # Android lint (warnings are errors) + Spotless
    python scripts/quality_gates.py coverage    # unit tests with the JaCoCo coverage report
    python scripts/quality_gates.py all         # build, test, lint, coverage in that order
    python scripts/quality_gates.py self-test   # prove formatter scope and planted-defect refusals

Each gate appends a JSON record to build/quality-metrics.json (build duration, unit test count,
lint violation count) and, when GITHUB_STEP_SUMMARY is set, a Markdown table to that file.
The exit code is the Gradle exit code, or 1 when a green Gradle run is refused as evidence (below);
nothing is skipped or downgraded.

Unit-test results count as evidence only when the current run produced them (android#69). The
build cache is enabled in gradle.properties and the JUnit XML files are declared task outputs, so a
FROM-CACHE or UP-TO-DATE unit-test task restores a full green count without running a test. Every
unit-test task is therefore invoked with Gradle's `--rerun` task option, and the captured plain
console must show it executed: a FROM-CACHE, UP-TO-DATE, NO-SOURCE or SKIPPED outcome, or a task
Gradle never reported, fails the gate even when Gradle exits 0. `self-test` plants an up-to-date
run and a cached run and proves the gate refuses both.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
METRICS_FILE = ROOT / "build" / "quality-metrics.json"
COMMON_ARGS = ("--console=plain", "--no-daemon", "--stacktrace")

GATES: dict[str, tuple[str, ...]] = {
    "build": ("assembleDebug", "assembleRelease"),
    "test": ("testDebugUnitTest", "testReleaseUnitTest"),
    "lint": ("lintDebug", "lintRelease", "spotlessCheck"),
    # The report task alone would accept an up-to-date or cached testDebugUnitTest; naming the test
    # task lets --rerun force it and lets the console check see its outcome.
    "coverage": ("testDebugUnitTest", "createDebugUnitTestCoverageReport"),
}
ORDER = ("build", "test", "lint", "coverage")

# Tasks whose JUnit XML the gates consume as evidence. They are forced with --rerun and their
# console outcome is checked; anything but an execution (which prints a header without a label,
# plus FAILED when it fails) is refused, including labels this script does not know.
UNIT_TEST_TASKS = ("testDebugUnitTest", "testReleaseUnitTest")
EXECUTED = "executed"
NOT_RUN = "not run"
EXECUTED_LABELS = (EXECUTED, "FAILED")
# Gradle's own labels for a task whose outputs were reused or that did no work. Not an allow or deny
# list: the refusal is "anything outside EXECUTED_LABELS"; this tuple only words the refusal message
# as a known reuse versus a label this script has never seen (a Gradle change to investigate).
REUSED_LABELS = ("FROM-CACHE", "UP-TO-DATE", "NO-SOURCE", "SKIPPED")
# `> Task :app:testDebugUnitTest FROM-CACHE`; an executed task prints `> Task :app:testDebugUnitTest`,
# possibly several times when its output interleaves with another task's under parallel execution.
TASK_HEADER = re.compile(r"^> Task (?P<path>:\S+?)(?: (?P<label>[A-Z][A-Z-]*))?\s*$", re.MULTILINE)
UNIT_TEST_OUTPUTS = ("build/test-results/test{variant}UnitTest", "build/reports/tests/test{variant}UnitTest")

# Planted defects for the self-test. Each one must make exactly the named gate fail.
FAILING_TEST = APP / "src/test/kotlin/com/pennilogic/android/PlantedFailingTest.kt"
FAILING_TEST_SOURCE = """package com.pennilogic.android

import org.junit.Assert.fail
import org.junit.Test

class PlantedFailingTest {
    @Test
    fun `planted defect must fail the unit test gate`() {
        fail("planted defect")
    }
}
"""
FORMAT_VIOLATION = APP / "src/main/kotlin/com/pennilogic/android/PlantedFormatViolation.kt"
FORMAT_VIOLATION_SOURCE = "package com.pennilogic.android\n\nobject PlantedFormatViolation { val a=1 }\n"
LINT_VIOLATION = APP / "src/main/res/values/planted_lint_violation.xml"
LINT_VIOLATION_SOURCE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<resources>\n"
    '    <string name="planted_unused_string">planted lint violation</string>\n'
    "</resources>\n"
)


def gradle_command() -> list[str]:
    wrapper = ROOT / ("gradlew.bat" if os.name == "nt" else "gradlew")
    return [str(wrapper)]


class GradleRun(NamedTuple):
    exit_code: int
    duration_seconds: float
    console: str


def gradle_arguments(
    tasks: tuple[str, ...],
    extra: tuple[str, ...] = (),
    force_unit_tests: bool = True,
) -> list[str]:
    """The wrapper command line. Every unit-test task carries the `--rerun` task option so Gradle can
    neither call it up-to-date nor restore it from the build cache; only the self-test switches that
    off to plant a reused run."""
    arguments = gradle_command()
    for task in tasks:
        arguments.append(task)
        if force_unit_tests and task in UNIT_TEST_TASKS:
            arguments.append("--rerun")
    return arguments + list(COMMON_ARGS) + list(extra)


def run_gradle(
    tasks: tuple[str, ...],
    extra: tuple[str, ...] = (),
    force_unit_tests: bool = True,
) -> GradleRun:
    """Stream the wrapper and retain its console. Windows owns the tree before execution and
    confirms native exits and pipe release before returning or propagating an interruption.
    An unconfirmed Windows shutdown raises an unsafe error; callers must preserve its fixtures."""
    command = gradle_arguments(tasks, extra, force_unit_tests)
    print("$", " ".join(command), flush=True)
    started = time.monotonic()
    lines: list[str] = []

    def consume(line: str) -> None:
        print(line, end="", flush=True)
        lines.append(line)

    if os.name == "nt":
        if __package__:
            from .windows_processes import run_command
        else:
            from windows_processes import run_command

        exit_code = run_command(command, ROOT, consume)
    else:
        with subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    consume(line)
            except BaseException:
                process.kill()
                raise
        exit_code = process.returncode
    return GradleRun(exit_code, round(time.monotonic() - started, 3), "".join(lines))


def task_labels(console: str) -> dict[str, list[str]]:
    """Every label Gradle's plain console printed per task path, in order; a header without a label
    is recorded as `executed`."""
    labels: dict[str, list[str]] = {}
    for match in TASK_HEADER.finditer(console):
        labels.setdefault(match["path"], []).append(match["label"] or EXECUTED)
    return labels


def unit_test_outcomes(console: str, tasks: Iterable[str]) -> dict[str, str]:
    """One verdict per unit-test task in [tasks]: `executed`, the first label that is not an
    execution (FROM-CACHE, UP-TO-DATE, ...), or `not run` when Gradle never reported the task."""
    labels = task_labels(console)
    verdicts: dict[str, str] = {}
    for task in tasks:
        if task not in UNIT_TEST_TASKS:
            continue
        seen = [label for path, path_labels in labels.items() if path.endswith(f":{task}") for label in path_labels]
        reused = [label for label in seen if label not in EXECUTED_LABELS]
        verdicts[task] = reused[0] if reused else (EXECUTED if seen else NOT_RUN)
    return verdicts


def refused_unit_tests(verdicts: dict[str, str]) -> dict[str, str]:
    """The unit-test tasks whose results the current run did not produce."""
    return {task: verdict for task, verdict in verdicts.items() if verdict != EXECUTED}


def gate_exit_code(gradle_exit_code: int, verdicts: dict[str, str]) -> int:
    """Gradle's exit code, or 1 when Gradle was green but a consumed unit-test result was reused."""
    if gradle_exit_code:
        return gradle_exit_code
    return 1 if refused_unit_tests(verdicts) else 0


def refusal(verdicts: dict[str, str]) -> str:
    """One line naming each refused task and its verdict, qualifying anything that is neither a
    known Gradle reuse label nor a task Gradle never reported."""
    refused = refused_unit_tests(verdicts)
    return "unit-test results were not produced by this run: " + ", ".join(
        f"{task} {verdict}" + ("" if verdict in REUSED_LABELS or verdict == NOT_RUN else " (unknown task outcome)")
        for task, verdict in refused.items()
    )


def count_unit_tests(variant: str) -> dict[str, int]:
    """Aggregate JUnit XML results Gradle wrote for one unit test variant."""
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    results = APP / "build" / "test-results" / f"test{variant}UnitTest"
    for report in sorted(results.glob("TEST-*.xml")):
        for key, value in suite_totals(report).items():
            totals[key] += value
    return totals


def suite_totals(report: Path) -> dict[str, int]:
    """Counts from one JUnit XML suite report."""
    suite = ElementTree.parse(report).getroot()
    return {key: int(suite.attrib.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}


def count_lint_issues(variant: str) -> dict[str, int]:
    """Count issues by severity in the lint XML report for one variant."""
    counts = {"fatal": 0, "error": 0, "warning": 0, "informational": 0}
    report = APP / "build" / "reports" / f"lint-results-{variant}.xml"
    if not report.is_file():
        return counts
    for issue in ElementTree.parse(report).getroot().iter("issue"):
        severity = issue.attrib.get("severity", "").lower()
        if severity in counts:
            counts[severity] += 1
    return counts


def coverage_summary() -> dict[str, dict[str, int]]:
    """Read the JaCoCo XML report the AGP coverage task produced."""
    summary: dict[str, dict[str, int]] = {}
    reports = APP / "build" / "reports" / "coverage" / "test" / "debug"
    for report in reports.rglob("report.xml"):
        root = ElementTree.parse(report).getroot()
        for counter in root.findall("counter"):
            summary[counter.attrib["type"]] = {
                "missed": int(counter.attrib["missed"]),
                "covered": int(counter.attrib["covered"]),
            }
    return summary


def collect_metrics(gate: str, run: GradleRun) -> dict[str, object]:
    metrics: dict[str, object] = {
        "gate": gate,
        "exit_code": run.exit_code,
        "duration_seconds": run.duration_seconds,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if gate in {"test", "coverage"}:
        verdicts = unit_test_outcomes(run.console, GATES[gate])
        metrics["exit_code"] = gate_exit_code(run.exit_code, verdicts)
        if refused_unit_tests(verdicts):
            metrics["gradle_exit_code"] = run.exit_code
            metrics["refused"] = refusal(verdicts)
        metrics["unit_test_tasks"] = verdicts
        metrics["unit_tests"] = {
            "debug": count_unit_tests("Debug"),
            "release": count_unit_tests("Release") if gate == "test" else None,
        }
    if gate == "lint":
        metrics["lint_issues"] = {"debug": count_lint_issues("debug"), "release": count_lint_issues("release")}
    if gate == "coverage":
        metrics["coverage"] = coverage_summary()
    return metrics


def publish(metrics: dict[str, object]) -> None:
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, object]] = []
    if METRICS_FILE.is_file():
        existing = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    existing.append(metrics)
    METRICS_FILE.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2), flush=True)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(markdown_summary(metrics))


def markdown_summary(metrics: dict[str, object]) -> str:
    rows = [
        f"| gate | {metrics['gate']} |",
        f"| exit code | {metrics['exit_code']} |",
        f"| duration (s) | {metrics['duration_seconds']} |",
    ]
    if "refused" in metrics:
        rows.append(f"| refused | {metrics['refused']} |")
    unit_test_tasks = metrics.get("unit_test_tasks")
    if isinstance(unit_test_tasks, dict):
        rows.append(f"| unit test tasks | {json.dumps(unit_test_tasks)} |")
    unit_tests = metrics.get("unit_tests")
    if isinstance(unit_tests, dict):
        for variant, totals in unit_tests.items():
            if totals:
                rows.append(f"| unit tests ({variant}) | {json.dumps(totals)} |")
    lint_issues = metrics.get("lint_issues")
    if isinstance(lint_issues, dict):
        for variant, counts in lint_issues.items():
            rows.append(f"| lint issues ({variant}) | {json.dumps(counts)} |")
    return f"### Android quality gate: {metrics['gate']}\n\n| metric | value |\n| --- | --- |\n" + "\n".join(rows) + "\n\n"


def run_gate(gate: str) -> int:
    metrics = collect_metrics(gate, run_gradle(GATES[gate]))
    publish(metrics)
    exit_code = metrics["exit_code"]
    assert isinstance(exit_code, int)
    return exit_code


def plant(path: Path, source: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite an existing file for a planted defect: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def self_test_case(name: str, path: Path, source: str, tasks: tuple[str, ...], expect_output: Path | None) -> dict:
    """Plant one defect, run the gate, remove the defect, and report whether the gate failed.

    A planted unit-test failure counts only when the unit-test task executed in this run: a failure
    that comes from an earlier stage (compilation, configuration) is not the test gate biting.
    """
    if __package__:
        from .windows_processes import UnsafeProcessTreeError
    else:
        from windows_processes import UnsafeProcessTreeError

    plant(path, source)
    restoration_safe = True
    try:
        run = run_gradle(tasks)
    except UnsafeProcessTreeError as error:
        restoration_safe = False
        try:
            error.retain_fixture(path)
        except BaseException as mapping_error:
            error.record_failure("fixture.recovery_mapping", mapping_error)
        raise error from None
    finally:
        if restoration_safe:
            path.unlink(missing_ok=True)
    outcome = {
        "case": name,
        "exit_code": run.exit_code,
        "duration_seconds": run.duration_seconds,
        "failed_as_expected": run.exit_code != 0,
    }
    verdicts = unit_test_outcomes(run.console, tasks)
    if verdicts:
        outcome["unit_test_tasks"] = verdicts
        outcome["unit_tests_executed"] = not refused_unit_tests(verdicts)
        outcome["failed_as_expected"] = outcome["failed_as_expected"] and outcome["unit_tests_executed"]
    if expect_output is not None:
        outcome["report_present"] = expect_output.is_file()
    return outcome


def remove_unit_test_outputs(variant: str) -> None:
    """Delete the declared outputs of one unit-test task so Gradle cannot call it up-to-date; the
    build-cache entry for the same inputs survives, which is what the FROM-CACHE case plants. A
    directory that cannot be removed raises, so a locked file cannot masquerade as UP-TO-DATE."""
    for relative in UNIT_TEST_OUTPUTS:
        outputs = APP / relative.format(variant=variant)
        if outputs.exists():
            shutil.rmtree(outputs)


def reused_results_case(name: str, expected_outcome: str, clean_outputs: bool, extra: tuple[str, ...] = ()) -> dict:
    """Plant a reused unit-test run and require the gate to refuse it.

    The debug unit-test task is invoked without `--rerun` right after a run that executed it on the
    same tree. With the outputs left in place Gradle reports UP-TO-DATE; with `clean_outputs` the
    outputs are deleted first, so the only reuse left is a build-cache hit (`--build-cache` is passed
    in [extra] so the case also holds when caching is switched off in the environment). A cold cache
    executes the tests once and stores them, so one more round is allowed to reach FROM-CACHE. Gradle
    exits 0 in both plants; the case passes only when the gate's exit code is not 0 and the observed
    outcome is the planted one.
    """
    rounds: list[dict] = []
    while True:
        if clean_outputs:
            remove_unit_test_outputs("Debug")
        run = run_gradle(("testDebugUnitTest",), extra, force_unit_tests=False)
        verdict = unit_test_outcomes(run.console, ("testDebugUnitTest",))["testDebugUnitTest"]
        rounds.append({"gradle_exit_code": run.exit_code, "duration_seconds": run.duration_seconds, "outcome": verdict})
        # A cold cache executes the tests once and stores them; the next round must then restore them.
        if verdict != EXECUTED or not clean_outputs or len(rounds) == 2:
            break
    exit_code = gate_exit_code(run.exit_code, {"testDebugUnitTest": verdict})
    return {
        "case": name,
        "exit_code": exit_code,
        "gradle_exit_code": run.exit_code,
        "duration_seconds": round(sum(r["duration_seconds"] for r in rounds), 3),
        "expected_outcome": expected_outcome,
        "observed_outcome": verdict,
        "rounds": rounds,
        "failed_as_expected": run.exit_code == 0 and exit_code != 0 and verdict == expected_outcome,
    }


def run_formatter_input_scope() -> GradleRun:
    fixture = ROOT / "scripts" / "tests" / "fixtures" / "formatter_input_scope.init.gradle"
    return run_gradle(
        ("formatterInputScopeRegression",),
        ("--init-script", str(fixture), "--no-configuration-cache"),
    )


def self_test() -> int:
    """Assert source-only formatter inputs, planted-defect failures, clean recovery and refusal of
    unit-test results this run did not produce."""
    if any(path.exists() for path in (FAILING_TEST, FORMAT_VIOLATION, LINT_VIOLATION)):
        print("self-test: planted-defect paths already exist; remove them first", file=sys.stderr)
        return 2
    # Nothing may intervene between the clean recovery and the two planted reuse invocations.
    formatter_run = run_formatter_input_scope()
    planted_report = APP / "build/test-results/testDebugUnitTest/TEST-com.pennilogic.android.PlantedFailingTest.xml"
    results = [
        self_test_case(
            "failing unit test fails testDebugUnitTest",
            FAILING_TEST,
            FAILING_TEST_SOURCE,
            ("testDebugUnitTest",),
            planted_report,
        ),
        self_test_case(
            "formatting violation fails spotlessCheck",
            FORMAT_VIOLATION,
            FORMAT_VIOLATION_SOURCE,
            ("spotlessCheck",),
            None,
        ),
        self_test_case(
            "unused resource fails lintDebug",
            LINT_VIOLATION,
            LINT_VIOLATION_SOURCE,
            ("lintDebug",),
            APP / "build/reports/lint-results-debug.xml",
        ),
    ]
    planted_test_result = results[0]
    if planted_test_result.get("report_present"):
        # The planted suite itself must record a failure, not a skip; other suites may skip variant-specific tests.
        planted = suite_totals(planted_report)
        planted_test_result["planted_suite"] = planted
        planted_test_result["planted_test_counted_not_skipped"] = (
            planted["tests"] >= 1 and planted["failures"] >= 1 and planted["skipped"] == 0
        )
    if results[2].get("report_present"):
        results[2]["lint_issue_counts"] = count_lint_issues("debug")

    recovery_run = run_gradle(("testDebugUnitTest", "spotlessCheck", "lintDebug"))
    recovery_verdicts = unit_test_outcomes(recovery_run.console, ("testDebugUnitTest",))
    recovery: dict[str, object] = {
        "exit_code": gate_exit_code(recovery_run.exit_code, recovery_verdicts),
        "gradle_exit_code": recovery_run.exit_code,
        "duration_seconds": recovery_run.duration_seconds,
        "unit_test_tasks": recovery_verdicts,
    }
    if refused_unit_tests(recovery_verdicts):
        recovery["refused"] = refusal(recovery_verdicts)
    # The recovery run has just executed the debug unit tests on the clean tree: replaying the task
    # without --rerun is up-to-date, and replaying it after deleting its outputs is a build-cache hit.
    results.append(reused_results_case("up-to-date unit-test results are refused", "UP-TO-DATE", clean_outputs=False))
    results.append(
        reused_results_case("cached unit-test results are refused", "FROM-CACHE", clean_outputs=True, extra=("--build-cache",)),
    )
    report: dict[str, object] = {
        "gate": "self-test",
        "formatter_input_scope": {
            "exit_code": formatter_run.exit_code,
            "duration_seconds": formatter_run.duration_seconds,
        },
        "cases": results,
        "recovery": recovery,
    }
    passed = formatter_run.exit_code == 0 and all(case["failed_as_expected"] for case in results)
    passed = passed and recovery["exit_code"] == 0
    passed = passed and planted_test_result.get("planted_test_counted_not_skipped", False)
    report["passed"] = passed
    report["exit_code"] = 0 if passed else 1
    report["duration_seconds"] = round(
        formatter_run.duration_seconds + sum(c["duration_seconds"] for c in results) + recovery_run.duration_seconds,
        3,
    )
    publish(report)
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gate", choices=[*ORDER, "all", "self-test"])
    args = parser.parse_args(argv)
    # Gradle's console is re-emitted line by line; a redirected stdout must not choke on it.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    if args.gate == "self-test":
        return self_test()
    gates = ORDER if args.gate == "all" else (args.gate,)
    for gate in gates:
        exit_code = run_gate(gate)
        if exit_code:
            return exit_code
    return 0


if __name__ == "__main__":
    if __package__:
        from .windows_processes import UnsafeProcessTreeError
    else:
        from windows_processes import UnsafeProcessTreeError

    try:
        sys.exit(main())
    except UnsafeProcessTreeError as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
