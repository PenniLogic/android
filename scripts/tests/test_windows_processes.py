"""Real, finite Windows process-tree regressions; no Gradle or SDK is invoked."""

from __future__ import annotations

import os
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from windows_process_support import finite_scenario  # noqa: E402
import quality_gates  # noqa: E402
import windows_processes as windows  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows Job/process-object lifecycle")
class WindowsLifecycleTest(unittest.TestCase):
    def scenario(self, mode: str) -> dict:
        result = finite_scenario(mode)
        self.assertTrue(result["child_alive_at_callback"])
        self.assertTrue(result["process_ids_creation_times_retained"])
        self.assertTrue(result["root_signaled_before_source_return"])
        self.assertTrue(result["source_read_pipe_closed"])
        self.assertTrue(result["source_control_pipe_closed"])
        return result

    def test_interrupted_gate_confirms_descendant_exit_and_file_release_before_return(self) -> None:
        result = self.scenario("interrupt")
        self.assertTrue(result["keyboard_interrupt_preserved"])
        self.assertTrue(result["child_signaled_before_source_return"], result)
        self.assertEqual(1, result["child_native_exit_code_at_return"], result)
        self.assertFalse(result["child_completed_naturally"])
        self.assertEqual({"removed": True}, result["held_file_removal"], result)
        self.assertTrue(result["source_process_handle_closed"], result)

    def test_completed_wrapper_does_not_wait_for_a_surviving_inherited_writer(self) -> None:
        result = self.scenario("eof")
        self.assertEqual(0, result["exit_code"])
        self.assertEqual("> Task :app:testDebugUnitTest\n", result["console"])
        self.assertTrue(result["child_signaled_before_source_return"], result)
        self.assertEqual(1, result["child_native_exit_code_at_return"], result)
        self.assertFalse(result["child_completed_naturally"])
        self.assertLess(result["delay_after_root_exit_seconds"], 2.0, result)
        self.assertEqual({"removed": True}, result["held_file_removal"], result)
        self.assertTrue(result["source_process_handle_closed"], result)

    def test_confirmed_interrupt_allows_the_planted_fixture_to_be_removed(self) -> None:
        result = self.scenario("safe_case")
        self.assertTrue(result["keyboard_interrupt_preserved"])
        self.assertTrue(result["child_signaled_before_source_return"])
        self.assertEqual(1, result["child_native_exit_code_at_return"])
        self.assertTrue(result["planted_fixture_removed"])

    def test_unconfirmed_pipe_release_retains_fixture_and_recovery_mapping_with_one_budget(self) -> None:
        result = self.scenario("unsafe_pipe")
        self.assertTrue(result["child_signaled_before_source_return"])
        self.assertEqual(1, result["child_native_exit_code_at_return"])
        self.assertEqual("synthetic planted fixture\n", result["planted_fixture_retained"])
        self.assertEqual(1, len(result["retained_fixtures"]))
        self.assertTrue(next(iter(result["retained_fixtures"])).endswith("planted.kt"))
        self.assertTrue(result["recovery_processes"])
        self.assertIn("recovery directory", result["unsafe_error"])
        self.assertIn("pipes released=False", result["unsafe_error"])
        self.assertLess(result["delay_after_root_exit_seconds"], windows.TEARDOWN_SECONDS + 1.0)

    def test_refused_termination_is_not_hidden_even_when_fallback_exit_is_confirmed(self) -> None:
        result = self.scenario("refused_case")
        self.assertTrue(result["child_signaled_before_source_return"])
        self.assertEqual(0, result["child_native_exit_code_at_return"], "Windows kill-on-close uses exit code zero")
        self.assertFalse(result["child_completed_naturally"])
        self.assertLess(result["source_return_seconds"], 2.0, "the four-second child must be terminated, not drained")
        self.assertTrue(result["planted_fixture_removed"])
        self.assertIn("job termination failed", result["safe_known_lifecycle_error"])
        self.assertIn("pinned exits and pipe closure confirmed", result["safe_known_lifecycle_error"])

    def test_job_emptiness_and_returncode_do_not_replace_native_exit_confirmation(self) -> None:
        result = self.scenario("unsafe_exit")
        self.assertTrue(result["root_signaled_before_source_return"])
        self.assertTrue(result["child_signaled_before_source_return"])
        self.assertIn("native exits=False", result["unsafe_error"])
        self.assertIn("active members=0", result["unsafe_error"])
        self.assertEqual("synthetic planted fixture\n", result["planted_fixture_retained"])
        self.assertEqual(1, len(result["retained_fixtures"]))
        self.assertLess(result["delay_after_root_exit_seconds"], windows.TEARDOWN_SECONDS + 1.0)


@unittest.skipUnless(os.name == "nt", "Windows pre-execution ownership")
class WindowsOwnershipTest(unittest.TestCase):
    def test_job_creation_refusal_starts_nothing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-ownership-") as temporary:
            with mock.patch.object(windows, "WindowsJob", side_effect=windows.ProcessOwnershipError("synthetic refusal")), \
                    mock.patch.object(windows.subprocess, "Popen") as start:
                with self.assertRaises(windows.ProcessOwnershipError):
                    windows.run_command([sys.executable, "-c", "raise AssertionError"], Path(temporary), lambda line: None)
            start.assert_not_called()

    def refused_assignment(self, error: BaseException) -> None:
        with tempfile.TemporaryDirectory(prefix="android-ownership-") as temporary:
            root = Path(temporary)
            marker = root / "consumer-started"
            real_popen = windows.subprocess.Popen
            processes = []
            pins = []

            def recording(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                pins.append(windows.ProcessPin.from_process(process))
                return process

            try:
                with mock.patch.object(windows.WindowsJob, "assign", side_effect=error), \
                        mock.patch.object(windows.subprocess, "Popen", side_effect=recording):
                    with self.assertRaises(type(error)):
                        windows.run_command(
                            [sys.executable, "-I", "-S", "-B", "-c",
                             "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')",
                             str(marker)],
                            root, lambda line: None,
                        )
                self.assertFalse(marker.exists(), "consumer code ran before ownership admission")
                self.assertEqual(1, len(pins))
                self.assertTrue(pins[0].signaled())
                self.assertTrue(processes[0]._handle.closed)
                self.assertTrue(processes[0].stdout.closed)
                self.assertTrue(processes[0].stderr.closed)
            finally:
                for pin in pins:
                    pin.close()

    def test_assignment_refusal_reaps_only_the_idle_launcher(self) -> None:
        self.refused_assignment(windows.ProcessOwnershipError("synthetic assignment refusal"))

    def test_interrupt_during_assignment_never_releases_the_consumer(self) -> None:
        self.refused_assignment(KeyboardInterrupt())

    def test_consumer_start_is_gated_until_assignment_finishes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-ownership-") as temporary:
            root = Path(temporary)
            marker = root / "consumer-started"
            original = windows.WindowsJob.assign

            def observing(job, handle):
                self.assertFalse(marker.exists())
                original(job, handle)
                self.assertFalse(marker.exists(), "assignment alone must not release consumer code")

            with mock.patch.object(windows.WindowsJob, "assign", new=observing):
                result = windows.run_command(
                    [sys.executable, "-I", "-S", "-B", "-c",
                     "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')",
                     str(marker)],
                    root, lambda line: None,
                )
            self.assertEqual(0, result)
            self.assertEqual("executed", marker.read_text())

    def test_callback_keeps_utf8_replacement_universal_newlines_and_merged_stderr(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-output-") as temporary:
            lines = []
            result = windows.run_command(
                [sys.executable, "-I", "-S", "-B", "-c",
                 "import os,sys; os.write(1,b'caf\\xc3\\xa9\\r\\n'); os.write(2,b'bad\\xff\\n'); sys.exit(7)"],
                Path(temporary), lines.append,
            )
            self.assertEqual(7, result)
            self.assertEqual(["caf\u00e9\n", "bad\ufffd\n"], lines)

    def test_consumer_argv_remains_a_sequence_not_a_shell_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-argv-") as temporary:
            lines = []
            arguments = ["argument with spaces", 'quoted "argument"', "%PATH%", "&|<>^"]
            result = windows.run_command(
                [sys.executable, "-I", "-S", "-B", "-c",
                 "import json,sys; print(json.dumps(sys.argv[1:]))", *arguments],
                Path(temporary), lines.append,
            )
            self.assertEqual(0, result)
            self.assertEqual(arguments, json.loads("".join(lines)))

    def test_missing_consumer_raises_the_original_os_error_after_safe_shutdown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-missing-") as temporary:
            root = Path(temporary)
            with self.assertRaises(FileNotFoundError) as raised:
                windows.run_command([str(root / "missing.exe")], root, lambda line: None)
            self.assertEqual(2, raised.exception.winerror)

    def test_invalid_consumer_argument_is_not_a_success_shaped_launcher_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-invalid-") as temporary:
            with self.assertRaises(ValueError):
                windows.run_command([sys.executable, "invalid\0argument"], Path(temporary), lambda line: None)

    def test_bounded_reader_drains_the_complete_console_without_dropping_queued_lines(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-output-") as temporary:
            lines = []
            result = windows.run_command(
                [sys.executable, "-I", "-S", "-B", "-u", "-c",
                 "import sys; sys.stdout.write(''.join(str(i)+'\\n' for i in range(1024)))"],
                Path(temporary), lines.append,
            )
            self.assertEqual(0, result)
            self.assertEqual([f"{index}\n" for index in range(1024)], lines)

    def test_release_admission_does_not_consume_or_replace_inherited_stdin(self) -> None:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = (wintypes.DWORD,)
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
        kernel.SetStdHandle.restype = wintypes.BOOL
        standard_input = (-10) & 0xffffffff
        original = kernel.GetStdHandle(standard_input)
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"preserved consumer stdin\n")
            os.close(write_fd)
            write_fd = None
            windows.native().require(
                kernel.SetStdHandle(standard_input, msvcrt.get_osfhandle(read_fd)), "SetStdHandle owned input fixture",
            )
            with tempfile.TemporaryDirectory(prefix="android-stdin-") as temporary:
                lines = []
                result = windows.run_command(
                    [sys.executable, "-I", "-S", "-B", "-c",
                     "import sys; sys.stdout.write(sys.stdin.readline())"],
                    Path(temporary), lines.append,
                )
                self.assertEqual(0, result)
                self.assertEqual(["preserved consumer stdin\n"], lines)
        finally:
            windows.native().require(kernel.SetStdHandle(standard_input, original), "Restore inherited stdin handle")
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_package_import_uses_the_same_owned_lifecycle(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="android-package-") as temporary:
            root = Path(temporary)
            code = (
                "import pathlib,sys; "
                f"sys.path.insert(0, {str(repository)!r}); "
                "from scripts import quality_gates; "
                "quality_gates.gradle_arguments=lambda *args: "
                "[sys.executable,'-I','-S','-B','-c',\"print('package consumer')\"]; "
                "result=quality_gates.run_gradle(('testDebugUnitTest',)); "
                "assert result.exit_code == 0 and result.console == 'package consumer\\n'"
            )
            lines = []
            result = windows.run_command(
                [sys.executable, "-I", "-S", "-B", "-c", code], root, lines.append,
            )
            self.assertEqual(0, result, "".join(lines))

    def test_creation_identity_mismatch_is_rejected_and_the_observer_handle_is_closed(self) -> None:
        created = windows.native().created(windows.native().kernel.GetCurrentProcess())
        original = windows.native().close
        closed = []

        def closing(handle):
            closed.append(handle)
            original(handle)

        with mock.patch.object(windows.native(), "close", side_effect=closing):
            with self.assertRaisesRegex(windows.WindowsProcessError, "creation identity changed"):
                windows.ProcessPin.open(os.getpid(), created + 1)
        self.assertEqual(1, len(closed))


class RestorationBoundaryTest(unittest.TestCase):
    def test_only_typed_unsafe_failure_retains_fixture_with_recoverable_location(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-restore-") as temporary:
            root = Path(temporary)
            planted = root / "planted.kt"
            unsafe = windows.UnsafeProcessTreeError("synthetic unconfirmed native exit", root)
            with mock.patch.object(quality_gates, "run_gradle", side_effect=unsafe):
                with self.assertRaises(windows.UnsafeProcessTreeError) as raised:
                    quality_gates.self_test_case("unsafe", planted, "owned fixture\n", ("testDebugUnitTest",), None)
            self.assertIs(unsafe, raised.exception)
            self.assertEqual("owned fixture\n", planted.read_text(encoding="utf-8"))
            self.assertIn(str(planted.resolve()), unsafe.retained_fixtures)
            self.assertIn("Confirm", unsafe.retained_fixtures[str(planted.resolve())])
            self.assertTrue(any(str(planted.resolve()) in note for note in unsafe.__notes__))

    def test_safe_known_failure_still_restores_and_propagates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-restore-") as temporary:
            planted = Path(temporary) / "planted.kt"
            failure = windows.WindowsProcessError("safe native exits, command status invalid")
            with mock.patch.object(quality_gates, "run_gradle", side_effect=failure):
                with self.assertRaises(windows.WindowsProcessError) as raised:
                    quality_gates.self_test_case("safe failure", planted, "owned fixture\n", ("testDebugUnitTest",), None)
            self.assertIs(failure, raised.exception)
            self.assertFalse(planted.exists())

    def test_unsafe_failure_aborts_before_later_self_test_plants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-restore-") as temporary:
            root = Path(temporary)
            failing, formatting, lint = [root / name for name in ("failing.kt", "format.kt", "lint.xml")]
            unsafe = windows.UnsafeProcessTreeError("synthetic unconfirmed pipes", root)

            def run(tasks, extra=(), force_unit_tests=True):
                if tasks == ("formatterInputScopeRegression",):
                    return quality_gates.GradleRun(0, 0.0, "synthetic scope result\n")
                raise unsafe

            with mock.patch.object(quality_gates, "APP", root), \
                    mock.patch.object(quality_gates, "FAILING_TEST", failing), \
                    mock.patch.object(quality_gates, "FORMAT_VIOLATION", formatting), \
                    mock.patch.object(quality_gates, "LINT_VIOLATION", lint), \
                    mock.patch.object(quality_gates, "run_gradle", side_effect=run) as runs:
                with self.assertRaises(windows.UnsafeProcessTreeError):
                    quality_gates.self_test()
            self.assertTrue(failing.exists())
            self.assertFalse(formatting.exists())
            self.assertFalse(lint.exists())
            self.assertEqual(1, sum(call.args[0] != ("formatterInputScopeRegression",) for call in runs.call_args_list))


class PosixBranchContractTest(unittest.TestCase):
    def test_posix_argv_stream_callback_and_gradle_result_are_unchanged(self) -> None:
        command = ["owned-wrapper", "testDebugUnitTest", "--rerun"]
        process = mock.MagicMock()
        process.stdout = io.StringIO("> Task :app:testDebugUnitTest\n")
        process.returncode = 3
        process.__enter__.return_value = process
        output = io.StringIO()
        with mock.patch.object(quality_gates, "os", SimpleNamespace(name="posix")), \
                mock.patch.object(quality_gates, "gradle_arguments", return_value=command), \
                mock.patch.object(quality_gates.subprocess, "Popen", return_value=process) as start, \
                mock.patch.object(windows, "run_command") as windows_run, \
                mock.patch("sys.stdout", output):
            result = quality_gates.run_gradle(("testDebugUnitTest",))
        start.assert_called_once_with(
            command, cwd=quality_gates.ROOT, stdout=quality_gates.subprocess.PIPE,
            stderr=quality_gates.subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        windows_run.assert_not_called()
        self.assertEqual(3, result.exit_code)
        self.assertEqual("> Task :app:testDebugUnitTest\n", result.console)
        self.assertIn("$ owned-wrapper testDebugUnitTest --rerun", output.getvalue())

    def test_posix_interrupted_consumer_still_kills_the_direct_process_and_reraises(self) -> None:
        process = mock.MagicMock()
        process.stdout = iter(["> Task :app:testDebugUnitTest\n"])
        process.__enter__.return_value = process

        def interrupt(*args, **kwargs):
            if args and str(args[0]).startswith("> Task"):
                raise KeyboardInterrupt

        with mock.patch.object(quality_gates, "os", SimpleNamespace(name="posix")), \
                mock.patch.object(quality_gates, "gradle_arguments", return_value=["owned-wrapper"]), \
                mock.patch.object(quality_gates.subprocess, "Popen", return_value=process), \
                mock.patch("builtins.print", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                quality_gates.run_gradle(("testDebugUnitTest",))
        process.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
