"""Focused source runner tests; no adb, emulator, managed device or service is used."""

from __future__ import annotations

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compatibility_matrix as matrix
from privacy_traffic.safety import Refusal, canonical, read_document


class CompatibilityMatrixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.value = read_document(matrix.MATRIX_PATH)
        self.today = datetime.now(timezone.utc).date()

    def quarantine(self, *, offset: int = 7, subset: str = "large_text") -> dict:
        return {
            "lane_id": "emulator_large_text", "test_subset": subset, "owner_issue": 35,
            "opened_on": self.today.isoformat(), "expires_on": (self.today + timedelta(days=offset)).isoformat(),
        }

    def refuse_matrix(self, value: matrix.Matrix) -> None:
        for stage in matrix.STAGES:
            for boundary in ("select", "plan", "run_required"):
                with self.subTest(stage=stage, boundary=boundary):
                    probe = mock.Mock(return_value=matrix.AdapterResult("passed", 1))
                    output, error = io.StringIO(), io.StringIO()
                    with redirect_stdout(output), redirect_stderr(error), self.assertRaises(Refusal) as caught:
                        if boundary == "run_required":
                            matrix.run_required(value, stage, runners={"emulator": probe})
                        else:
                            getattr(matrix, boundary)(value, stage)
                    probe.assert_not_called()
                    self.assertRegex(str(caught.exception), r"^[a-z][a-z0-9_]*$")
                    self.assertEqual("", output.getvalue() + error.getvalue())

    def test_strategy_and_platform_lanes_are_preserved_without_guessing_hardware(self) -> None:
        validated = matrix.load()
        self.assertEqual(10, len(validated.lanes))
        self.assertEqual({31, 33, 35, 36}, {lane.os_api for lane in validated.lanes if lane.kind == "emulator"})
        held = [lane for lane in validated.lanes if lane.kind != "emulator"]
        self.assertEqual(4, len(held))
        self.assertTrue(all(lane.os_api is None and lane.oem_variant is None for lane in held))
        self.assertTrue(all(lane["ram_mib"] is None for lane in self.value["lanes"]))
        self.assertEqual(26, self.value["scaffold"]["min_api"])
        self.assertTrue(self.value["scaffold"]["minimum_is_provisional"])
        self.assertEqual(
            {"emulator_api_31", "emulator_current", "emulator_large_text"},
            {lane.id for lane in matrix.select(validated, "pull_request")},
        )
        self.assertEqual(10, len(matrix.select(validated, "release_candidate")))

    def test_plan_names_each_required_subset_and_never_records_execution(self) -> None:
        plan = matrix.plan(matrix.load(), "pull_request")
        self.assertEqual(10, sum(len(lane["test_subsets"]) for lane in plan["lanes"]))
        self.assertTrue(all(lane["result"] == "not_run" for lane in plan["lanes"]))
        self.assertFalse(plan["minimum_supported_api_confirmed"])
        self.assertFalse(plan["runtime_qualified"])
        self.assertFalse(plan["release_qualified"])

    def test_required_lanes_subsets_configurations_and_stages_cannot_be_reduced(self) -> None:
        changes = (
            lambda value: value["lanes"].pop(),
            lambda value: value["lanes"].__setitem__(1, value["lanes"][0]),
            lambda value: value["lanes"][0]["test_subsets"].pop(),
            lambda value: value["lanes"][0]["test_subsets"].append("SYNTH_PLANTED_SUBSET"),
            lambda value: value["lanes"][0]["required_for"].remove("pull_request"),
            lambda value: value["lanes"][4].__setitem__("configuration", "default"),
            lambda value: value["lanes"][5].__setitem__("configuration", "default"),
            lambda value: value["lanes"][0].__setitem__("result", "passed"),
            lambda value: value["lanes"][0].__setitem__("os_api", 26),
            lambda value: value["lanes"][0].__setitem__("os_api", 31.0),
            lambda value: value["lanes"][0].__setitem__("os_api", True),
            lambda value: value["lanes"][6].__setitem__("ram_mib", 2048),
            lambda value: value["lanes"][7].__setitem__("oem_variant", "SYNTH_PLANTED_OEM"),
            lambda value: value["strategy"].__setitem__("current_api", 37),
            lambda value: value["strategy"].__setitem__("current_api", 36.0),
            lambda value: value["scaffold"].__setitem__("min_api", 26.0),
            lambda value: value["scaffold"].__setitem__("minimum_is_provisional", 1),
            lambda value: value["scaffold"].__setitem__("minimum_is_provisional", False),
            lambda value: value.__setitem__("schema_version", True),
            lambda value: value.__setitem__("matrix_version", "2.0.0"),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                value = copy.deepcopy(self.value)
                change(value)
                with self.assertRaises(Refusal):
                    matrix.validate(value)

    def test_unknown_or_payload_fields_and_malformed_containers_fail_closed(self) -> None:
        for path in ((), ("strategy",), ("scaffold",), ("lanes", 0)):
            changed = copy.deepcopy(self.value)
            location = changed
            for key in path:
                location = location[key]
            location["SYNTH_PLANTED_DEVICE_SERIAL"] = "SYNTH_PLANTED_ACCOUNT"
            with self.assertRaises(Refusal):
                matrix.validate(changed)
        for field, value in (("lanes", {}), ("quarantines", {}), ("lanes", [])):
            changed = copy.deepcopy(self.value)
            changed[field] = value
            with self.assertRaises(Refusal):
                matrix.validate(changed)

    def test_platform_drift_is_not_silently_reinterpreted_as_current(self) -> None:
        real_read = matrix.read_document

        def drifted(path: Path) -> dict:
            value = real_read(path)
            if path.name == "android-behavior-matrix.json":
                value["target_api"] = 37
            return value

        with mock.patch.object(matrix, "read_document", side_effect=drifted):
            with self.assertRaisesRegex(Refusal, "^accepted_platform_baseline_drift$"):
                matrix.load()

    def test_schema_cannot_accept_an_unreviewed_payload_example(self) -> None:
        real_read = matrix.read_document

        def planted(path: Path) -> dict:
            value = real_read(path)
            if path == matrix.SCHEMA_PATH:
                value["examples"] = ["SYNTH_PLANTED_DEVICE_ACCOUNT"]
            return value

        with mock.patch.object(matrix, "read_document", side_effect=planted):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(1, matrix.main(["check"]))
            self.assertEqual("", output.getvalue())
            self.assertFalse("SYNTH_PLANTED_DEVICE_ACCOUNT" in error.getvalue())
            self.assertEqual("matrix_schema_allowlist_mismatch", json.loads(error.getvalue())["code"])

    def test_no_runtime_bindings_reports_all_required_cells_as_blocked(self) -> None:
        report = matrix.run_required(matrix.load(), "release_candidate")
        self.assertEqual(37, report["required"])
        self.assertEqual(37, report["blocked"])
        self.assertEqual(0, report["passed"])
        self.assertFalse(report["all_adapter_checks_passed"])
        self.assertFalse(report["required_runtime_subset_passed"])
        for dimension in ("by_os", "by_memory_class", "by_oem_variant"):
            self.assertEqual(37, sum(group["required"] for group in report[dimension]))
            self.assertTrue(all(group["pass_rate_basis_points"] == 0 for group in report[dimension]))
        self.assertEqual(17, sum(row["code"] == "device_profile_selection_pending" for row in report["results"]))

    def test_injected_emulator_fixture_passes_only_the_source_contract(self) -> None:
        requests = []

        def probe(request: matrix.RunnerRequest) -> matrix.AdapterResult:
            requests.append(request)
            return matrix.AdapterResult("passed", 1)

        report = matrix.run_required(matrix.load(), "pull_request", runners={"emulator": probe})
        self.assertEqual(10, len(requests))
        self.assertEqual(10, report["passed"])
        self.assertTrue(report["all_adapter_checks_passed"])
        self.assertEqual("compatibility_runner_contract", report["scope"])
        self.assertFalse(report["required_runtime_subset_passed"])
        self.assertFalse(report["runtime_qualified"])
        self.assertFalse(report["release_qualified"])
        self.assertTrue(all(row["evidence_scope"] == "source_adapter" for row in report["results"]))
        self.assertEqual(1, len({request.corpus_sha256 for request in requests}))

    def test_pending_managed_and_physical_profiles_never_invoke_an_adapter(self) -> None:
        forbidden = mock.Mock(side_effect=AssertionError("unselected device must not run"))
        report = matrix.run_required(
            matrix.load(), "release_candidate",
            runners={
                "emulator": lambda _: matrix.AdapterResult("passed", 1),
                "managed_device": forbidden, "physical": forbidden,
            },
        )
        forbidden.assert_not_called()
        self.assertEqual(20, report["passed"])
        self.assertEqual(17, report["blocked"])
        self.assertFalse(report["all_adapter_checks_passed"])
        pending = next(group for group in report["by_os"] if group["value"] is None)
        self.assertEqual(17, pending["required"])
        self.assertEqual(0, pending["pass_rate_basis_points"])

    def test_failure_does_not_retry_or_remove_later_required_cells(self) -> None:
        requests = []

        def probe(request: matrix.RunnerRequest) -> matrix.AdapterResult:
            requests.append(request)
            return matrix.AdapterResult("failed" if len(requests) == 1 else "passed", 1)

        report = matrix.run_required(matrix.load(), "pull_request", runners={"emulator": probe})
        self.assertEqual(10, len(requests))
        self.assertEqual(1, report["failed"])
        self.assertEqual(9, report["passed"])
        self.assertFalse(report["all_adapter_checks_passed"])

    def test_empty_skipped_wrong_typed_and_unknown_results_fail(self) -> None:
        plants = (
            None, {"raw": "SYNTH_PLANTED_MESSAGE"}, matrix.AdapterResult("passed", 0),
            matrix.AdapterResult("passed", True), matrix.AdapterResult("passed", 1.0),
            matrix.AdapterResult("passed", 2, 1), matrix.AdapterResult("passed", 1, True),
            matrix.AdapterResult("SYNTH_PLANTED_DEVICE_SERIAL", 1), matrix.AdapterResult("passed", 1000001),
            matrix.AdapterResult("failed", -1), matrix.AdapterResult("blocked", 1, 2),
        )
        for index, plant in enumerate(plants):
            with self.subTest(plant=index):
                report = matrix.run_required(matrix.load(), "pull_request", runners={"emulator": lambda _: plant})
                self.assertEqual(10, report["failed"])
                self.assertFalse(b"SYNTH_PLANTED" in canonical(report))

    def test_adapter_failure_text_is_not_evidence(self) -> None:
        def refused(_: matrix.RunnerRequest) -> matrix.AdapterResult:
            raise Refusal("synthetic_planted_financial_exception")

        report = matrix.run_required(matrix.load(), "pull_request", runners={"emulator": refused})
        self.assertEqual(10, report["failed"])
        self.assertFalse(b"synthetic_planted" in canonical(report))
        with self.assertRaisesRegex(Refusal, "^runner_kind_invalid$"):
            matrix.run_required(matrix.load(), "pull_request", runners={"SYNTH_PLANTED_RUNNER": refused})
        with self.assertRaisesRegex(Refusal, "^runner_not_callable$"):
            matrix.run_required(matrix.load(), "pull_request", runners={"emulator": None})

    def test_quarantine_requires_owner_bounded_expiry_and_noncritical_subset(self) -> None:
        valid = self.quarantine(offset=14)
        self.value["quarantines"] = [valid]
        self.assertEqual(1, len(matrix.validate(self.value).quarantines))
        for field, value in (
            ("owner_issue", None), ("owner_issue", True), ("owner_issue", 0), ("owner_issue", "SYNTH_PLANTED_OWNER"),
            ("expires_on", self.today.isoformat()),
            ("expires_on", (self.today + timedelta(days=15)).isoformat()),
            ("opened_on", (self.today + timedelta(days=1)).isoformat()),
            ("expires_on", "SYNTH_PLANTED_DATE"), ("lane_id", "SYNTH_PLANTED_DEVICE"),
            ("test_subset", "parser_corpus"), ("test_subset", "permission_journeys"),
        ):
            changed = copy.deepcopy(self.value)
            changed["quarantines"][0][field] = value
            with self.assertRaises(Refusal):
                matrix.validate(changed)
        for field in ("owner_issue", "expires_on"):
            changed = copy.deepcopy(self.value)
            del changed["quarantines"][0][field]
            with self.assertRaises(Refusal):
                matrix.validate(changed)
        self.value["quarantines"].append(valid)
        with self.assertRaisesRegex(Refusal, "^quarantine_duplicate$"):
            matrix.validate(self.value)

    def test_quarantined_case_still_executes_is_reported_and_cannot_waive_required_pass(self) -> None:
        self.value["quarantines"] = [self.quarantine()]
        requests = []

        def probe(request: matrix.RunnerRequest) -> matrix.AdapterResult:
            requests.append(request)
            return matrix.AdapterResult("passed", 1)

        report = matrix.run_required(matrix.validate(self.value), "pull_request", runners={"emulator": probe})
        self.assertEqual(10, len(requests))
        self.assertEqual(1, report["quarantined"])
        self.assertFalse(report["all_adapter_checks_passed"])
        self.assertEqual(9, sum(group["passed"] for group in report["by_os"]))
        held = next(row for row in report["results"] if "quarantine" in row)
        self.assertEqual(35, held["quarantine"]["owner_issue"])
        self.assertEqual("passed", held["status"])
        plan = matrix.plan(matrix.validate(self.value), "pull_request")
        self.assertEqual(1, len(plan["quarantines"]))
        self.assertEqual(35, plan["quarantines"][0]["owner_issue"])

    def test_quarantine_expiring_after_validation_still_blocks_execution(self) -> None:
        self.value["quarantines"] = [self.quarantine(offset=1)]
        validated = matrix.validate(self.value)
        probe = mock.Mock(return_value=matrix.AdapterResult("passed", 1))
        for report in (matrix.select, matrix.plan):
            with self.subTest(boundary=report.__name__):
                with self.assertRaisesRegex(Refusal, "^quarantine_expired_or_invalid$"):
                    report(validated, "pull_request", today=self.today + timedelta(days=1))
        with self.assertRaisesRegex(Refusal, "^quarantine_expired_or_invalid$"):
            matrix.run_required(
                validated, "pull_request", runners={"emulator": probe},
                today=self.today + timedelta(days=1),
            )
        probe.assert_not_called()

    def test_programmatic_matrix_cannot_omit_a_required_lane(self) -> None:
        valid = matrix.load()
        for lanes in (
            (), valid.lanes[:1], (*valid.lanes[:-1], valid.lanes[0]),
            (replace(valid.lanes[0], os_api=31.0), *valid.lanes[1:]),
            (replace(valid.lanes[0], test_subsets=()), *valid.lanes[1:]),
            (replace(valid.lanes[0], required_for=("release_candidate",)), *valid.lanes[1:]),
        ):
            self.refuse_matrix(matrix.Matrix(lanes, ()))

    def test_programmatic_quarantines_reject_critical_and_ownerless_entries(self) -> None:
        valid = matrix.load()
        allowed = matrix.Quarantine(
            "emulator_large_text", "large_text", 35, self.today, self.today + timedelta(days=7),
        )
        for subset in matrix.NON_QUARANTINABLE:
            lane = next(lane for lane in valid.lanes if subset in lane.test_subsets)
            with self.subTest(subset=subset):
                self.refuse_matrix(replace(valid, quarantines=(
                    replace(allowed, lane_id=lane.id, test_subset=subset),
                )))
        for owner in (0, -1, True, 35.0, 2147483648, None, "SYNTH_PLANTED_OWNER"):
            with self.subTest(owner_type=type(owner).__name__):
                self.refuse_matrix(replace(valid, quarantines=(replace(allowed, owner_issue=owner),)))
        self.refuse_matrix(matrix.Matrix(valid.lanes, (
            replace(allowed, lane_id="emulator_api_31", test_subset="parser_corpus", owner_issue=0),
        )))

    def test_programmatic_quarantines_reject_duplicates_targets_dates_and_shapes(self) -> None:
        valid = matrix.load()
        allowed = matrix.Quarantine(
            "emulator_large_text", "large_text", 35, self.today, self.today + timedelta(days=7),
        )
        for index, entries in enumerate((
            (allowed, allowed), (replace(allowed, lane_id="SYNTH_PLANTED_LANE"),),
            (replace(allowed, lane_id="emulator_api_31"),),
            (replace(allowed, test_subset="SYNTH_PLANTED_SUBSET"),),
            (replace(allowed, opened_on=self.today + timedelta(days=1)),),
            (replace(allowed, expires_on=self.today),),
            (replace(allowed, expires_on=self.today + timedelta(days=15)),),
            (replace(allowed, opened_on=self.today.isoformat()),),
            (replace(allowed, expires_on=None),), (self.quarantine(),), [allowed], (allowed,) * 65,
        )):
            with self.subTest(change=index):
                self.refuse_matrix(replace(valid, quarantines=entries))
        self.refuse_matrix(replace(valid, quarantines=(
            matrix.Quarantine("emulator_talkback", "parser_corpus", 35, self.today, self.today + timedelta(days=1)),
        )))

    def test_valid_constructed_quarantine_matches_json_and_keeps_required_denominators(self) -> None:
        self.value["quarantines"] = [self.quarantine()]
        validated = matrix.validate(self.value)
        constructed = matrix.Matrix(
            validated.lanes,
            (matrix.Quarantine("emulator_large_text", "large_text", 35, self.today, self.today + timedelta(days=7)),),
        )
        for stage, required, passed in (("pull_request", 10, 10), ("release_candidate", 37, 20)):
            with self.subTest(stage=stage):
                self.assertEqual(matrix.plan(validated, stage), matrix.plan(constructed, stage))
                probe = mock.Mock(return_value=matrix.AdapterResult("passed", 1))
                report = matrix.run_required(constructed, stage, runners={"emulator": probe})
                self.assertEqual(required, report["required"])
                self.assertEqual(required, len(report["results"]))
                self.assertEqual(passed, probe.call_count)
                self.assertEqual(passed, report["passed"])
                self.assertEqual(required - passed, report["blocked"])
                self.assertEqual(1, report["quarantined"])
                for dimension in ("by_os", "by_memory_class", "by_oem_variant"):
                    self.assertEqual(required, sum(group["required"] for group in report[dimension]))
                    self.assertEqual(passed - 1, sum(group["passed"] for group in report[dimension]))
                self.assertFalse(report["all_adapter_checks_passed"])
                self.assertFalse(report["required_runtime_subset_passed"])
                self.assertFalse(report["release_qualified"])

    def test_schema_matches_matrix_record_keys_and_selection_catalogs(self) -> None:
        schema = read_document(matrix.SCHEMA_PATH)
        self.assertEqual(set(self.value), set(schema["required"]))
        self.assertEqual(set(self.value), set(schema["properties"]))
        self.assertIs(schema["additionalProperties"], False)
        lane = schema["$defs"]["lane"]
        self.assertEqual(set(self.value["lanes"][0]), set(lane["required"]))
        self.assertEqual(set(lane["required"]), set(lane["properties"]))
        self.assertIs(lane["additionalProperties"], False)
        self.assertEqual(set(matrix.LANES), set(lane["properties"]["id"]["enum"]))
        self.assertEqual(set(matrix.STAGES), set(lane["properties"]["required_for"]["items"]["enum"]))
        self.assertEqual(
            {subset for requirement in matrix.LANES.values() for subset in requirement[5]},
            set(schema["$defs"]["subset"]["enum"]),
        )
        quarantine = schema["$defs"]["quarantine"]
        self.assertEqual(set(self.quarantine()), set(quarantine["required"]))
        self.assertIs(quarantine["additionalProperties"], False)

    def test_commands_distinguish_static_success_from_missing_acceptance(self) -> None:
        for command, expected in (("check", 0), ("plan", 0), ("run-required", 2)):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(expected, matrix.main([command, "--stage", "release_candidate"]))
            self.assertEqual("", error.getvalue())
            report = json.loads(output.getvalue())
            self.assertFalse(report["release_qualified"])
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(1, matrix.main(["plan", "--stage", "SYNTH_PLANTED_STAGE"]))
        self.assertFalse("SYNTH_PLANTED_STAGE" in output.getvalue() + error.getvalue())


if __name__ == "__main__":
    unittest.main()
