"""Unit tests for the native formatter probe wiring; Gradle executes in the Android self-test."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quality_gates  # noqa: E402


class FormatterInputScopeTest(unittest.TestCase):
    def test_probe_runs_the_native_fixture_with_no_unit_test_cache_override(self) -> None:
        fixture = quality_gates.ROOT / "scripts" / "tests" / "fixtures" / "formatter_input_scope.init.gradle"
        self.assertTrue(fixture.is_file())
        expected = quality_gates.GradleRun(0, 1.0, "native scope passed\n")

        with mock.patch.object(quality_gates, "run_gradle", return_value=expected) as run:
            self.assertIs(expected, quality_gates.run_formatter_input_scope())

        run.assert_called_once_with(
            ("formatterInputScopeRegression",),
            ("--init-script", str(fixture), "--no-configuration-cache"),
        )

    def test_probe_does_not_turn_a_native_failure_into_success(self) -> None:
        expected = quality_gates.GradleRun(1, 1.0, "native scope failed\n")

        with mock.patch.object(quality_gates, "run_gradle", return_value=expected):
            actual = quality_gates.run_formatter_input_scope()

        self.assertIs(expected, actual)
        self.assertEqual(1, actual.exit_code)


if __name__ == "__main__":
    unittest.main()
