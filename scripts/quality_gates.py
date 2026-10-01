"""Run the Android quality gates, publish pipeline metrics and self-test the gates on planted defects.

Usage (from a clean clone, JDK 21 and Android SDK 36 present, nothing else configured):

    python scripts/quality_gates.py build       # assembleDebug + assembleRelease
    python scripts/quality_gates.py test        # unit tests for debug and release
    python scripts/quality_gates.py lint        # Android lint (warnings are errors) + Spotless
    python scripts/quality_gates.py coverage    # unit tests with the JaCoCo coverage report
    python scripts/quality_gates.py all         # build, test, lint, coverage in that order
    python scripts/quality_gates.py ci          # one fresh native graph for those four gates
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
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
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
CI_REPORT_TASKS = ("lintReportDebug", "lintReportRelease", "createDebugUnitTestCoverageReport")
CI_PROVENANCE_SCRIPT = r"""
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.Path
import java.nio.file.attribute.BasicFileAttributes
import java.security.MessageDigest

class CIProducerEvidence implements Action<Task>, Serializable {
    String invocation
    String root
    String taskPath
    List<String> directories
    List<String> files
    String executionData

    Path checked(String name, boolean directory) {
        def path = new File(name).toPath().toAbsolutePath().normalize()
        if (!path.startsWith(new File(root).toPath().resolve("app").resolve("build"))) {
            throw new GradleException("CI producer output is outside this checkout")
        }
        for (def ancestor = path; ancestor != null; ancestor = ancestor.parent) {
            if (Files.exists(ancestor, LinkOption.NOFOLLOW_LINKS)) {
                def attrs = Files.readAttributes(ancestor, BasicFileAttributes, LinkOption.NOFOLLOW_LINKS)
                if (Files.isSymbolicLink(ancestor) || attrs.isOther() || ancestor.toRealPath() != ancestor) {
                    throw new GradleException("CI producer link/reparse output is refused")
                }
            }
        }
        if (directory ? !Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS) :
                        !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
            throw new GradleException("CI producer output is missing or has the wrong type")
        }
        path
    }

    void execute(Task ignored) {
        if (ignored.path != taskPath) {
            throw new GradleException("CI evidence action ran on an unexpected native task")
        }
        def paths = files.collect { checked(it, false) }
        directories.each { name ->
            def directory = checked(name, true).toFile()
            def reports = directory.listFiles()
            if (reports == null) {
                throw new GradleException("CI producer cannot enumerate JUnit reports")
            }
            paths.addAll(reports.findAll { it.name.startsWith("TEST-") && it.name.endsWith(".xml") }
                .collect { checked(it.absolutePath, false) })
        }
        if (paths.isEmpty()) {
            throw new GradleException("CI producer supplied no evidence")
        }
        def fingerprints = [:]
        paths.sort { it.toString() }.each { path ->
            def digest = MessageDigest.getInstance("SHA-256")
            Files.newInputStream(path).withCloseable { input ->
                byte[] buffer = new byte[8192]
                for (int count; (count = input.read(buffer)) != -1;) {
                    digest.update(buffer, 0, count)
                }
            }
            def relative = new File(root).toPath().relativize(path).toString().replace(File.separator, "/")
            fingerprints[relative] = digest.digest().encodeHex().toString()
        }
        if (executionData != null) {
            Files.newInputStream(checked(executionData, false)).withCloseable { input ->
                def data = new DataInputStream(input)
                if (data.readUnsignedByte() != 1 || data.readUnsignedShort() != 0xc0c0 ||
                    data.readUnsignedShort() != 0x1007 || data.readUnsignedByte() != 0x10 ||
                    data.readUTF() != invocation) {
                    throw new GradleException("CI producer did not create this invocation's JaCoCo session")
                }
            }
        }
        println("CI_NATIVE_EVIDENCE " + JsonOutput.toJson([
            invocation: invocation, task: taskPath, files: fingerprints,
        ]))
    }
}

def settings = new JsonSlurper().parseText(new String(Base64.decoder.decode("__SETTINGS__"), "UTF-8"))
gradle.projectsEvaluated {
    def app = gradle.rootProject.project(":app")
    settings.producers.each { producer ->
        def task = app.tasks.named(producer.task).get()
        def declared = task.outputs.files.files.collect { it.toPath().toAbsolutePath().normalize().toString() }
        if (!declared.containsAll(producer.declared)) {
            throw new GradleException("CI native task output contract changed: " + task.path)
        }
        if (producer.executionData != null) {
            def jacoco = task.extensions.getByName("jacoco")
            if (jacoco.destinationFile.toPath().toAbsolutePath().normalize().toString() != producer.executionData) {
                throw new GradleException("CI debug execution-data destination changed")
            }
            jacoco.sessionId = settings.invocation
        }
        task.doLast(new CIProducerEvidence(
            invocation: settings.invocation, root: settings.root, taskPath: task.path,
            directories: producer.directories, files: producer.files, executionData: producer.executionData,
        ))
    }
}
"""

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


class InvalidCIEvidence(ValueError):
    """The grouped invocation cannot prove its own native results."""


def ci_tasks() -> tuple[str, ...]:
    tasks = tuple(dict.fromkeys(task for gate in ORDER for task in GATES[gate] if task not in CI_REPORT_TASKS))
    return tasks + tuple(argument for task in CI_REPORT_TASKS for argument in (task, "--rerun"))


def ci_evidence_paths() -> tuple[tuple[Path, bool], ...]:
    build = APP / "build"
    return (
        (build / "test-results" / "testDebugUnitTest", True),
        (build / "test-results" / "testReleaseUnitTest", True),
        (build / "reports" / "lint-results-debug.xml", False),
        (build / "reports" / "lint-results-release.xml", False),
        (build / "outputs" / "unit_test_code_coverage" / "debugUnitTest" / "testDebugUnitTest.exec", False),
        (build / "reports" / "coverage" / "test" / "debug", True),
    )


def check_ci_evidence_path(path: Path) -> None:
    lexical = path.absolute()
    if APP.absolute() != (ROOT / "app").absolute() or not lexical.is_relative_to((APP / "build").absolute()):
        raise InvalidCIEvidence(f"evidence path is outside this checkout's app build: {path}")
    for ancestor in (lexical, *lexical.parents):
        try:
            attributes = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise InvalidCIEvidence(f"link/reparse evidence path is refused: {ancestor}")
        if not (stat.S_ISDIR(attributes.st_mode) or (ancestor == lexical and stat.S_ISREG(attributes.st_mode))):
            raise InvalidCIEvidence(f"unsupported evidence path: {ancestor}")
    if lexical.resolve() != lexical:
        raise InvalidCIEvidence(f"aliased evidence path is refused: {path}")
    if lexical.is_dir():
        for child in lexical.iterdir():
            check_ci_evidence_path(child)


def prepare_ci_evidence() -> None:
    # Check every consumed output before removing any; fresh files, not mtimes, prove provenance.
    outputs = ci_evidence_paths()
    for path, directory in outputs:
        check_ci_evidence_path(path)
        if path.exists() and path.is_dir() != directory:
            raise InvalidCIEvidence(f"unexpected evidence output type: {path}")
    for path, directory in outputs:
        if path.exists():
            if directory:
                shutil.rmtree(path)
            else:
                path.unlink()


def run_ci_gradle(invocation: str) -> GradleRun:
    if __package__:
        from .windows_processes import UnsafeProcessTreeError
    else:
        from windows_processes import UnsafeProcessTreeError

    outputs = ci_evidence_paths()
    producers: list[dict[str, object]] = []
    for index, task in enumerate(UNIT_TEST_TASKS):
        directories = [str(outputs[index][0])]
        files = [str(outputs[4][0])] if index == 0 else []
        producers.append({
            "task": task, "directories": directories, "files": files,
            "declared": directories + files, "executionData": files[0] if files else None,
        })
    for index, task in enumerate(CI_REPORT_TASKS):
        path = outputs[index + 2][0] if index < 2 else outputs[5][0] / "report.xml"
        producers.append({
            "task": task, "directories": [], "files": [str(path)],
            "declared": [str(path if index < 2 else path.parent)], "executionData": None,
        })
    settings = base64.b64encode(json.dumps({
        "invocation": invocation, "root": str(ROOT), "producers": producers,
    }).encode("utf-8")).decode("ascii")
    build = APP / "build"
    check_ci_evidence_path(build / "ci-native-provenance-parent")
    build.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="ci-native-provenance-", dir=build))
    fixture = directory / "producer.init.gradle"
    restoration_safe = True
    try:
        check_ci_evidence_path(fixture)
        fixture.write_text(CI_PROVENANCE_SCRIPT.replace("__SETTINGS__", settings), encoding="utf-8")
        return run_gradle(ci_tasks(), ("--init-script", str(fixture)))
    except UnsafeProcessTreeError as error:
        restoration_safe = False
        try:
            error.retain_fixture(fixture)
        except BaseException as mapping_error:
            error.record_failure("fixture.recovery_mapping", mapping_error)
        raise error from None
    finally:
        if restoration_safe:
            check_ci_evidence_path(fixture)
            fixture.unlink(missing_ok=True)
            directory.rmdir()


def ci_producer_paths() -> dict[str, tuple[Path, ...]]:
    outputs = ci_evidence_paths()
    result: dict[str, tuple[Path, ...]] = {}
    for index, task in enumerate(UNIT_TEST_TASKS):
        reports = tuple(sorted(outputs[index][0].glob("TEST-*.xml")))
        result[f":app:{task}"] = reports + ((outputs[4][0],) if index == 0 else ())
    for index, task in enumerate(CI_REPORT_TASKS):
        result[f":app:{task}"] = (outputs[index + 2][0] if index < 2 else outputs[5][0] / "report.xml",)
    return result


def validate_ci_producers(console: str, invocation: str) -> None:
    expected = ci_producer_paths()
    proofs: dict[str, dict[str, str]] = {}
    for line in console.splitlines():
        if not line.startswith("CI_NATIVE_EVIDENCE "):
            continue
        try:
            proof = json.loads(line.removeprefix("CI_NATIVE_EVIDENCE "))
        except json.JSONDecodeError as error:
            raise InvalidCIEvidence("malformed current native producer evidence") from error
        if not isinstance(proof, dict) or set(proof) != {"invocation", "task", "files"}:
            raise InvalidCIEvidence("invalid current native producer evidence shape")
        if proof.get("invocation") != invocation:
            raise InvalidCIEvidence("native producer evidence belongs to another invocation")
        task, fingerprints = proof.get("task"), proof.get("files")
        if not isinstance(task, str) or task not in expected or task in proofs or not isinstance(fingerprints, dict):
            raise InvalidCIEvidence("missing, unexpected or duplicate native producer evidence")
        names = {path.relative_to(ROOT).as_posix() for path in expected[task]}
        if set(fingerprints) != names:
            raise InvalidCIEvidence(f"native producer evidence does not describe the current outputs: {task}")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in fingerprints.values()):
            raise InvalidCIEvidence(f"invalid native producer fingerprint: {task}")
        proofs[task] = fingerprints
    if set(proofs) != set(expected):
        raise InvalidCIEvidence("current native producer evidence is missing")
    for task, paths in expected.items():
        for path in paths:
            check_ci_evidence_path(path)
            with path.open("rb") as data:
                fingerprint = hashlib.file_digest(data, "sha256").hexdigest()
            if proofs[task][path.relative_to(ROOT).as_posix()] != fingerprint:
                raise InvalidCIEvidence(f"evidence was replaced after its current native producer: {task}")


def ci_xml(path: Path, tag: str) -> ElementTree.Element:
    check_ci_evidence_path(path)
    if not path.is_file():
        raise InvalidCIEvidence(f"current {tag} report is missing: {path}")
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as error:
        raise InvalidCIEvidence(f"invalid current XML report {path}: {error}") from error
    if root.tag != tag:
        raise InvalidCIEvidence(f"expected {tag} report at {path}, found {root.tag}")
    return root


def ci_jacoco_session(path: Path) -> tuple[str, int, int]:
    """Read the pinned JaCoCo header and leading session block, not file timestamps."""
    check_ci_evidence_path(path)
    with path.open("rb") as data:
        if data.read(6) != b"\x01\xc0\xc0\x10\x07\x10":
            raise InvalidCIEvidence("unsupported current JaCoCo execution-data header/session layout")
        size = data.read(2)
        if len(size) != 2:
            raise InvalidCIEvidence("truncated current JaCoCo session")
        length = int.from_bytes(size, "big")
        encoded = data.read(length)
        metadata = data.read(16)
        if len(encoded) != length or len(metadata) != 16:
            raise InvalidCIEvidence("truncated current JaCoCo session")
        try:
            identity = encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidCIEvidence("unsupported current JaCoCo session identity encoding") from error
        if not identity:
            raise InvalidCIEvidence("empty current JaCoCo session identity")
        return identity, int.from_bytes(metadata[:8], "big"), int.from_bytes(metadata[8:], "big")


def validate_ci_reports(invocation: str) -> None:
    for path, directory in ci_evidence_paths():
        check_ci_evidence_path(path)
        if not path.exists() or path.is_dir() != directory:
            raise InvalidCIEvidence(f"current evidence output is missing or has the wrong type: {path}")
    for variant in ("Debug", "Release"):
        reports = sorted((APP / "build" / "test-results" / f"test{variant}UnitTest").glob("TEST-*.xml"))
        if not reports:
            raise InvalidCIEvidence(f"current {variant} JUnit reports are missing")
        for report in reports:
            suite = ci_xml(report, "testsuite")
            try:
                counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
            except (KeyError, ValueError) as error:
                raise InvalidCIEvidence(f"invalid JUnit counts in {report}") from error
            if any(value < 0 for value in counts.values()) or counts["skipped"] > counts["tests"]:
                raise InvalidCIEvidence(f"inconsistent JUnit counts in {report}")
            cases = suite.findall("testcase")
            observed = {
                "tests": len(cases),
                "failures": sum(case.find("failure") is not None for case in cases),
                "errors": sum(case.find("error") is not None for case in cases),
                "skipped": sum(case.find("skipped") is not None for case in cases),
            }
            if counts != observed:
                raise InvalidCIEvidence(f"JUnit counts do not describe the current test cases in {report}")
        totals = count_unit_tests(variant)
        if totals["tests"] <= totals["skipped"] or totals["failures"] or totals["errors"]:
            raise InvalidCIEvidence(f"current {variant} tests did not pass with executed cases: {totals}")
        lint = ci_xml(APP / "build" / "reports" / f"lint-results-{variant.lower()}.xml", "issues")
        if any(issue.attrib.get("severity", "").lower() not in {"fatal", "error", "warning", "informational"} for issue in lint.iter("issue")):
            raise InvalidCIEvidence(f"unknown lint severity in current {variant} report")
        issues = count_lint_issues(variant.lower())
        if any(issues[severity] for severity in ("fatal", "error", "warning")):
            raise InvalidCIEvidence(f"current {variant} lint did not pass: {issues}")
    execution_data = ci_evidence_paths()[4][0]
    reports = list(ci_evidence_paths()[5][0].rglob("report.xml"))
    if len(reports) != 1:
        raise InvalidCIEvidence("expected exactly one current JaCoCo XML report")
    report = ci_xml(reports[0], "report")
    try:
        sessions = [
            (session.attrib["id"], int(session.attrib["start"]), int(session.attrib["dump"]))
            for session in report.findall("sessioninfo")
        ]
    except (KeyError, ValueError) as error:
        raise InvalidCIEvidence("invalid current JaCoCo XML session metadata") from error
    session = ci_jacoco_session(execution_data)
    if session[0] != invocation or sessions != [session]:
        raise InvalidCIEvidence("JaCoCo XML does not describe the current debug execution-data session")
    counters: dict[str, tuple[int, int]] = {}
    for counter in report.findall("counter"):
        try:
            kind = counter.attrib["type"]
            missed, covered = int(counter.attrib["missed"]), int(counter.attrib["covered"])
        except (KeyError, ValueError) as error:
            raise InvalidCIEvidence("invalid current JaCoCo counter") from error
        if kind in counters or missed < 0 or covered < 0:
            raise InvalidCIEvidence("duplicate or negative current JaCoCo counter")
        counters[kind] = (missed, covered)
    if any(kind not in counters or sum(counters[kind]) == 0 for kind in ("INSTRUCTION", "BRANCH", "LINE")):
        raise InvalidCIEvidence("current JaCoCo report lacks nonempty instruction, branch or line counters")


def run_ci() -> int:
    """Share only this invocation's fresh debug execution between test and coverage."""
    started = time.monotonic()
    metrics: dict[str, object] = {"gate": "ci", "gates": list(ORDER), "exit_code": 1}
    try:
        prepare_ci_evidence()
    except (InvalidCIEvidence, OSError) as error:
        metrics["refused"] = f"CI evidence preparation failed: {error}"
        metrics["duration_seconds"] = round(time.monotonic() - started, 3)
        publish(metrics)
        return 1

    invocation = uuid.uuid4().hex
    run = run_ci_gradle(invocation)
    labels = task_labels(run.console)
    forced = {f":app:{task}" for task in (*UNIT_TEST_TASKS, *CI_REPORT_TASKS)}
    required = {f":app:{task}" for gate in ORDER for task in GATES[gate] if task != "spotlessCheck"}
    required.update(forced | {":spotlessCheck"})
    outcomes: dict[str, str] = {}
    refusals: list[str] = []
    for task in sorted(required):
        seen = labels.get(task, [])
        allowed = EXECUTED_LABELS if task in forced else (*EXECUTED_LABELS, "UP-TO-DATE", "FROM-CACHE")
        rejected = [label for label in seen if label not in allowed]
        outcomes[task] = rejected[0] if rejected else (seen[-1] if seen else NOT_RUN)
        if not seen or rejected:
            refusals.append(f"{task} {outcomes[task]}")
        elif run.exit_code == 0 and "FAILED" in seen:
            refusals.append(f"{task} FAILED despite a successful native exit")
    metrics.update(
        invocation_id=invocation,
        native_duration_seconds=run.duration_seconds,
        native_task_outcomes=outcomes,
        unit_test_tasks={
            task: EXECUTED if outcomes[f":app:{task}"] in EXECUTED_LABELS else outcomes[f":app:{task}"]
            for task in UNIT_TEST_TASKS
        },
        gradle_exit_code=run.exit_code,
    )
    try:
        validate_ci_reports(invocation)
        validate_ci_producers(run.console, invocation)
    except (InvalidCIEvidence, OSError) as error:
        refusals.append(str(error))
    else:
        current = collect_metrics("test", run)
        metrics["unit_tests"] = current["unit_tests"]
        metrics["lint_issues"] = {variant: count_lint_issues(variant) for variant in ("debug", "release")}
        metrics["coverage"] = coverage_summary()
    exit_code = run.exit_code or (1 if refusals else 0)
    metrics["exit_code"] = exit_code
    if refusals:
        metrics["refused"] = "CI evidence was not produced by this invocation: " + "; ".join(refusals)
    metrics["duration_seconds"] = round(time.monotonic() - started, 3)
    publish(metrics)
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
    parser.add_argument("gate", choices=[*ORDER, "all", "ci", "self-test"])
    args = parser.parse_args(argv)
    # Gradle's console is re-emitted line by line; a redirected stdout must not choke on it.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    if args.gate == "self-test":
        return self_test()
    if args.gate == "ci":
        return run_ci()
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
