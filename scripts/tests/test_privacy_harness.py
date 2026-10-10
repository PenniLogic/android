"""Runner composition checks; fixture results are not native or privacy-capture evidence."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import privacy_traffic_harness as harness


def fixture_case(module: str, name: str, executed: list[str], outcome: str = "pass") -> unittest.TestCase:
    def test_run(case: unittest.TestCase) -> None:
        executed.append(case.id())
        if outcome == "failure":
            case.fail("planted runner failure")
        if outcome == "error":
            raise RuntimeError("planted runner error")
        if outcome == "skip":
            case.skipTest("runner fixture skip")
        if outcome == "expected_failure":
            case.fail("expected runner failure")

    if outcome in ("expected_failure", "unexpected_success"):
        test_run = unittest.expectedFailure(test_run)
    case_type = type(name, (unittest.TestCase,), {"__module__": module, "test_run": test_run})
    return case_type("test_run")


class PrivacyHarnessTest(unittest.TestCase):
    def run_self_test(self, arguments: list[str], cases: list[unittest.TestCase]) -> tuple[int, dict, str]:
        suite = unittest.TestSuite([unittest.TestSuite([case]) for case in cases])
        loader = unittest.TestLoader()
        stdout, stderr = io.StringIO(), io.StringIO()
        captures = [{"scenario": "runner_fixture", "proxy_shutdown_confirmed": True}]
        observations = [{"finding": "runner_fixture", "owned_threads_joined": True}]
        modules = {
            "test_privacy_traffic": SimpleNamespace(CAPTURE_RUNS=captures),
            "test_privacy_traffic_boundaries": SimpleNamespace(BOUNDARY_OBSERVATIONS=observations),
        }
        with mock.patch.object(harness.unittest, "defaultTestLoader", loader):
            with mock.patch.object(loader, "discover", return_value=suite) as discover:
                with mock.patch.dict(sys.modules, modules):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = harness.main(["self-test", *arguments])
        pattern = "test_*.py" if arguments else "test_privacy_traffic*.py"
        discover.assert_called_once_with(str(harness.ROOT / "scripts" / "tests"), pattern=pattern)
        summary = json.loads(stdout.getvalue())
        self.assertEqual("source_self_test", summary["scope"])
        self.assertEqual(captures, summary["capture_runs"])
        self.assertEqual(observations, summary["boundary_observations"])
        self.assertIs(summary["release_qualified"], False)
        self.assertIs(summary["approved_release_signer_present"], False)
        return code, summary, stderr.getvalue()

    def test_focused_default_keeps_its_discovery_and_summary(self) -> None:
        executed: list[str] = []
        case = fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed)
        code, summary, stderr = self.run_self_test([], [case])
        self.assertEqual(0, code)
        self.assertEqual([case.id()], executed)
        self.assertEqual(1, summary["tests"])
        self.assertEqual(0, summary["failures"])
        self.assertEqual(0, summary["errors"])
        self.assertEqual([], summary["skipped"])
        self.assertEqual({
            "scope", "tests", "failures", "errors", "skipped", "capture_runs",
            "boundary_observations", "release_qualified", "approved_release_signer_present",
        }, set(summary))
        self.assertIn("Ran 1 test", stderr)

    def test_combined_runs_every_discovered_case_once_with_privacy_report(self) -> None:
        executed: list[str] = []
        cases = [
            fixture_case("test_privacy_traffic_fixture", "PrivacyFirst", executed),
            fixture_case("test_privacy_traffic_boundaries", "PrivacySecond", executed),
            fixture_case("test_script_fixture", "OtherCase", executed),
            fixture_case("test_script_fixture", "SkippedCase", executed, "skip"),
        ]
        code, summary, stderr = self.run_self_test(["--all-scripts"], cases)
        self.assertEqual(0, code)
        self.assertEqual([case.id() for case in cases], executed)
        self.assertEqual(len(set(executed)), len(executed))
        self.assertEqual(4, summary["tests"])
        self.assertEqual(0, summary["failures"])
        self.assertEqual(0, summary["errors"])
        self.assertEqual([{"test": cases[-1].id(), "reason": "runner fixture skip"}], summary["skipped"])
        self.assertIn("Ran 4 tests", stderr)

    def test_combined_failure_in_either_subset_fails_without_short_circuit(self) -> None:
        for failed_module in ("test_privacy_traffic_fixture", "test_script_fixture"):
            with self.subTest(module=failed_module):
                executed: list[str] = []
                cases = [
                    fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed,
                                 "failure" if failed_module == "test_privacy_traffic_fixture" else "pass"),
                    fixture_case("test_script_fixture", "OtherCase", executed,
                                 "failure" if failed_module == "test_script_fixture" else "pass"),
                    fixture_case("test_script_fixture", "FollowingCase", executed),
                ]
                code, summary, stderr = self.run_self_test(["--all-scripts"], cases)
                self.assertEqual(1, code)
                self.assertEqual([case.id() for case in cases], executed)
                self.assertEqual(3, summary["tests"])
                self.assertEqual(1, summary["failures"])
                self.assertEqual(0, summary["errors"])
                self.assertIn("planted runner failure", stderr)

    def test_combined_error_in_either_subset_is_reported_and_fails(self) -> None:
        for error_module in ("test_privacy_traffic_fixture", "test_script_fixture"):
            with self.subTest(module=error_module):
                executed: list[str] = []
                cases = [
                    fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed,
                                 "error" if error_module == "test_privacy_traffic_fixture" else "pass"),
                    fixture_case("test_script_fixture", "OtherCase", executed,
                                 "error" if error_module == "test_script_fixture" else "pass"),
                ]
                code, summary, stderr = self.run_self_test(["--all-scripts"], cases)
                self.assertEqual(1, code)
                self.assertEqual([case.id() for case in cases], executed)
                self.assertEqual(2, summary["tests"])
                self.assertEqual(0, summary["failures"])
                self.assertEqual(1, summary["errors"])
                self.assertIn("planted runner error", stderr)

    def test_unexpected_success_cannot_pass_combined_mode(self) -> None:
        executed: list[str] = []
        cases = [
            fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed),
            fixture_case("test_script_fixture", "OtherCase", executed, "unexpected_success"),
        ]
        code, summary, stderr = self.run_self_test(["--all-scripts"], cases)
        self.assertEqual(1, code)
        self.assertEqual([case.id() for case in cases], executed)
        self.assertEqual(2, summary["tests"])
        self.assertIn("unexpected successes=1", stderr)

    def test_expected_failure_keeps_standard_unittest_semantics(self) -> None:
        executed: list[str] = []
        cases = [
            fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed),
            fixture_case("test_script_fixture", "OtherCase", executed, "expected_failure"),
        ]
        code, summary, stderr = self.run_self_test(["--all-scripts"], cases)
        self.assertEqual(0, code)
        self.assertEqual([case.id() for case in cases], executed)
        self.assertEqual(2, summary["tests"])
        self.assertIn("expected failures=1", stderr)

    def test_empty_discovery_never_passes_either_mode(self) -> None:
        for arguments in ([], ["--all-scripts"]):
            with self.subTest(arguments=arguments):
                code, summary, stderr = self.run_self_test(arguments, [])
                self.assertEqual(1, code)
                self.assertEqual(0, summary["tests"])
                self.assertIn("Ran 0 tests", stderr)

    def test_other_passing_tests_cannot_hide_a_missing_privacy_suite(self) -> None:
        executed: list[str] = []
        case = fixture_case("test_script_fixture", "OtherCase", executed)
        code, summary, _ = self.run_self_test(["--all-scripts"], [case])
        self.assertEqual(1, code)
        self.assertEqual([case.id()], executed)
        self.assertEqual(1, summary["tests"])
        self.assertEqual("privacy_self_tests_missing", summary["refused"])

    def test_class_setup_skip_cannot_hide_zero_privacy_execution(self) -> None:
        def skip_class(_: type) -> None:
            raise unittest.SkipTest("privacy class fixture skip")

        executed: list[str] = []
        privacy = fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed)
        privacy.__class__.setUpClass = classmethod(skip_class)
        other = fixture_case("test_script_fixture", "OtherCase", executed)
        code, summary, _ = self.run_self_test(["--all-scripts"], [privacy, other])
        self.assertEqual(1, code)
        self.assertEqual([other.id()], executed)
        self.assertEqual(1, summary["tests"])
        self.assertEqual("privacy_self_tests_missing", summary["refused"])
        self.assertEqual("privacy class fixture skip", summary["skipped"][0]["reason"])

    def test_import_failure_stays_an_error_in_the_combined_summary(self) -> None:
        executed: list[str] = []
        case = fixture_case("test_privacy_traffic_fixture", "PrivacyCase", executed)
        failed = unittest.TestLoader().loadTestsFromName("pennilogic_nonexistent_runner_fixture")
        cases = [case, *failed]
        code, summary, stderr = self.run_self_test(["--all-scripts"], cases)
        self.assertEqual(1, code)
        self.assertEqual([case.id()], executed)
        self.assertEqual(2, summary["tests"])
        self.assertEqual(1, summary["errors"])
        self.assertIn("ModuleNotFoundError", stderr)

    def test_all_scripts_option_is_not_accepted_by_other_commands(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                harness.main(["source-probe", "--all-scripts"])
        self.assertEqual(2, caught.exception.code)


class PrivacyHarnessWarningPolicyTest(unittest.TestCase):
    def run_warning_policy(
        self, *, environment_policy: str | None = None,
        option_policy: str | None = None, focused: bool = False,
    ) -> list[dict]:
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() not in {"PYTHONWARNINGS", "PYTHONDEVMODE", "PYTHONPATH"}
        }
        if environment_policy is not None:
            environment["PYTHONWARNINGS"] = environment_policy
        environment["PYTHON_COLORS"] = "0"
        options = ["-W", option_policy] if option_policy is not None else []
        expected_options = [value for value in (environment_policy, option_policy) if value is not None]
        privacy_module = "test_privacy_traffic_warning_fixture"
        script_module = "test_warning_policy_fixture"
        warning_module = privacy_module if focused else script_module
        records = []
        with tempfile.TemporaryDirectory(prefix="privacy-warning-policy-") as temporary:
            root = Path(temporary)
            tests = root / "scripts" / "tests"
            tests.mkdir(parents=True)
            for module in (privacy_module, script_module):
                body = (
                    "        with warnings.catch_warnings(record=True) as captured:\n"
                    "            warnings.warn('owned warning-policy negative', ResourceWarning)\n"
                    "        self.assertEqual([], captured, 'resource warning must fail the negative')\n"
                    if module == warning_module else
                    f"        self.assertEqual({privacy_module!r}, __name__)\n"
                )
                (tests / f"{module}.py").write_text(
                    "import json\nimport sys\nimport unittest\nimport warnings\n\n"
                    "class WarningPolicyFixture(unittest.TestCase):\n"
                    "    def test_warning_policy(self):\n"
                    "        print('WARNING_POLICY_TEST ' + json.dumps({"
                    "'id': self.id(), 'warnoptions': sys.warnoptions}))\n" + body,
                    encoding="ascii",
                )
            for mode in (("focused",) if focused else ("unittest", "combined")):
                command = [sys.executable, "-B", "-S", *options]
                if mode == "unittest":
                    command.extend(["-m", "unittest", "discover", "-s", str(tests), "-p", "test_*.py", "-v"])
                else:
                    command.extend([
                        "-c", "import sys; from pathlib import Path; "
                        "sys.path.insert(0, sys.argv[1]); import privacy_traffic_harness as harness; "
                        "harness.ROOT = Path(sys.argv[2]); sys.exit(harness.main(sys.argv[3:]))",
                        str(harness.ROOT / "scripts"), str(root), "self-test",
                    ])
                    if mode == "combined":
                        command.append("--all-scripts")
                result = subprocess.run(
                    command, cwd=root, env=environment, capture_output=True, text=True, timeout=30,
                )
                records.append({
                    "mode": mode, "command": command, "exit_code": result.returncode,
                    "stdout": result.stdout, "stderr": result.stderr,
                })
        print("WARNING_POLICY_PAIR " + json.dumps({
            "environment_policy": environment_policy, "option_policy": option_policy, "runs": records,
        }))
        expected_ids = {
            f"{module}.WarningPolicyFixture.test_warning_policy"
            for module in ((privacy_module,) if focused else (privacy_module, script_module))
        }
        for record in records:
            observed = [
                json.loads(line.removeprefix("WARNING_POLICY_TEST "))
                for line in record["stdout"].splitlines() if line.startswith("WARNING_POLICY_TEST ")
            ]
            self.assertEqual(len(expected_ids), len(observed), record)
            self.assertEqual(expected_ids, {entry["id"] for entry in observed}, record)
            self.assertTrue(all(entry["warnoptions"] == expected_options for entry in observed), record)
            self.assertIn(f"Ran {len(expected_ids)} test", record["stderr"], record)
        return records

    def assert_warning_outcome(self, records: list[dict], code: int, *, failures: int = 0, errors: int = 0) -> None:
        for record in records:
            self.assertEqual(code, record["exit_code"], record)
            outcome = f"FAILED (failures={failures})" if failures else f"FAILED (errors={errors})" if errors else "OK"
            self.assertIn(outcome, record["stderr"], record)
            if record["mode"] != "unittest":
                summaries = [
                    json.loads(line) for line in record["stdout"].splitlines() if line.startswith("{")
                ]
                self.assertEqual(1, len(summaries))
                summary = summaries[0]
                self.assertEqual("source_self_test", summary["scope"])
                self.assertEqual(1 if record["mode"] == "focused" else 2, summary["tests"])
                self.assertEqual(failures, summary["failures"])
                self.assertEqual(errors, summary["errors"])
                self.assertEqual([], summary["skipped"])
                self.assertNotIn("refused", summary)
                self.assertIs(summary["release_qualified"], False)
                self.assertIs(summary["approved_release_signer_present"], False)

    def test_default_warning_policy_matches_unittest_subprocess(self) -> None:
        self.assert_warning_outcome(self.run_warning_policy(), 1, failures=1)

    def test_explicit_warning_policy_matches_unittest_subprocess(self) -> None:
        for source in ("environment_policy", "option_policy"):
            for policy, code, failures, errors in (
                ("default", 1, 1, 0), ("ignore", 0, 0, 0), ("error", 1, 0, 1),
                ("ignore::DeprecationWarning", 0, 0, 0),
            ):
                with self.subTest(source=source, policy=policy):
                    records = self.run_warning_policy(**{source: policy})
                    self.assert_warning_outcome(records, code, failures=failures, errors=errors)

    def test_command_line_warning_policy_takes_precedence_over_environment(self) -> None:
        for environment_policy, option_policy, code, errors in (
            ("error", "ignore", 0, 0), ("ignore", "error", 1, 1),
        ):
            with self.subTest(environment_policy=environment_policy, option_policy=option_policy):
                records = self.run_warning_policy(
                    environment_policy=environment_policy, option_policy=option_policy,
                )
                self.assert_warning_outcome(records, code, errors=errors)

    def test_focused_warning_policy_remains_unchanged_in_subprocess(self) -> None:
        for policy, code, failures, errors in (
            (None, 0, 0, 0), ("default", 1, 1, 0), ("ignore", 0, 0, 0), ("error", 1, 0, 1),
        ):
            with self.subTest(policy=policy):
                records = self.run_warning_policy(environment_policy=policy, focused=True)
                self.assert_warning_outcome(records, code, failures=failures, errors=errors)


if __name__ == "__main__":
    unittest.main()
