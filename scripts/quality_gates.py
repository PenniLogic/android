"""Run the Android quality gates, publish pipeline metrics and self-test the gates on planted defects.

Usage (from a clean clone, JDK 21 and Android SDK 36 present, nothing else configured):

    python scripts/quality_gates.py build       # assembleDebug + assembleRelease
    python scripts/quality_gates.py test        # unit tests for debug and release
    python scripts/quality_gates.py lint        # Android lint (warnings are errors) + Spotless
    python scripts/quality_gates.py coverage    # unit tests with the JaCoCo coverage report
    python scripts/quality_gates.py all         # build, test, lint, coverage in that order
    python scripts/quality_gates.py self-test   # prove that planted defects fail each gate

Each gate appends a JSON record to build/quality-metrics.json (build duration, unit test count,
lint violation count) and, when GITHUB_STEP_SUMMARY is set, a Markdown table to that file.
The exit code is the Gradle exit code; nothing is skipped or downgraded.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
METRICS_FILE = ROOT / "build" / "quality-metrics.json"
COMMON_ARGS = ("--console=plain", "--no-daemon", "--stacktrace")

GATES: dict[str, tuple[str, ...]] = {
    "build": ("assembleDebug", "assembleRelease"),
    "test": ("testDebugUnitTest", "testReleaseUnitTest"),
    "lint": ("lintDebug", "lintRelease", "spotlessCheck"),
    "coverage": ("createDebugUnitTestCoverageReport",),
}
ORDER = ("build", "test", "lint", "coverage")

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


def run_gradle(tasks: tuple[str, ...], extra: tuple[str, ...] = ()) -> tuple[int, float]:
    command = gradle_command() + list(tasks) + list(COMMON_ARGS) + list(extra)
    print("$", " ".join(command), flush=True)
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return completed.returncode, round(time.monotonic() - started, 3)


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


def collect_metrics(gate: str, exit_code: int, duration: float) -> dict[str, object]:
    metrics: dict[str, object] = {
        "gate": gate,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if gate in {"test", "coverage"}:
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
    exit_code, duration = run_gradle(GATES[gate])
    publish(collect_metrics(gate, exit_code, duration))
    return exit_code


def plant(path: Path, source: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite an existing file for a planted defect: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def self_test_case(name: str, path: Path, source: str, tasks: tuple[str, ...], expect_output: Path | None) -> dict:
    """Plant one defect, run the gate, remove the defect, and report whether the gate failed."""
    plant(path, source)
    try:
        exit_code, duration = run_gradle(tasks)
    finally:
        path.unlink(missing_ok=True)
    outcome = {"case": name, "exit_code": exit_code, "duration_seconds": duration, "failed_as_expected": exit_code != 0}
    if expect_output is not None:
        outcome["report_present"] = expect_output.is_file()
    return outcome


def self_test() -> int:
    """Assert that each gate fails on a planted defect and passes again once it is removed."""
    if any(path.exists() for path in (FAILING_TEST, FORMAT_VIOLATION, LINT_VIOLATION)):
        print("self-test: planted-defect paths already exist; remove them first", file=sys.stderr)
        return 2
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

    recovery_code, recovery_duration = run_gradle(("testDebugUnitTest", "spotlessCheck", "lintDebug"))
    report = {
        "gate": "self-test",
        "cases": results,
        "recovery": {"exit_code": recovery_code, "duration_seconds": recovery_duration},
    }
    passed = all(case["failed_as_expected"] for case in results) and recovery_code == 0
    passed = passed and planted_test_result.get("planted_test_counted_not_skipped", False)
    report["passed"] = passed
    report["exit_code"] = 0 if passed else 1
    report["duration_seconds"] = round(sum(c["duration_seconds"] for c in results) + recovery_duration, 3)
    publish(report)
    return report["exit_code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gate", choices=[*ORDER, "all", "self-test"])
    args = parser.parse_args(argv)
    if args.gate == "self-test":
        return self_test()
    gates = ORDER if args.gate == "all" else (args.gate,)
    for gate in gates:
        exit_code = run_gate(gate)
        if exit_code:
            return exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
