"""N1 real caller-thread scheduling at the report validation/serialization boundary."""

from __future__ import annotations

import copy
import inspect
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from privacy_traffic.evidence import EvidenceStore, private_path, validate_report
from privacy_traffic.pack import sign_pack, verify_pack
from privacy_traffic.probe import run_probe, synthetic_policy
from privacy_traffic.safety import MAX_DEPTH, MAX_DOCUMENT_BYTES, MAX_NODES, Refusal, canonical, document, snapshot_document
from test_privacy_traffic_boundaries import BOUNDARY_OBSERVATIONS, prohibited_present, test_key


def source_line(function, *expressions) -> int:
    source, first = inspect.getsourcelines(function)
    for expression in expressions:
        for offset, line in enumerate(source):
            if line.strip() == expression:
                return first + offset
    raise AssertionError("report_validation_boundary_not_found")


def validation_boundary() -> int:
    return source_line(validate_report, "raw = canonical(report)", "return raw")


def scheduled_mutation(action, mutation, *, code, line, predicate=None):
    requested, finished = threading.Event(), threading.Event()
    observed = {}

    def mutate():
        if requested.wait(5):
            mutation()
            observed["mutator_thread_id"] = threading.get_ident()
        finished.set()

    caller = threading.Thread(target=mutate, name="privacy-owned-report-caller")
    previous = sys.gettrace()

    def trace(frame, event, _argument):
        if frame.f_code is code:
            if (
                event == "line" and frame.f_lineno == line and not requested.is_set()
                and (predicate is None or predicate(frame))
            ):
                observed.update({
                    "trace_line": line, "trace_thread_id": threading.get_ident(),
                    "monotonic_at_boundary": time.monotonic(),
                })
                requested.set()
                if not finished.wait(5):
                    raise AssertionError("owned_report_mutator_did_not_finish")
            return trace
        return None

    caller.start()
    try:
        sys.settrace(trace)
        result = action()
    finally:
        sys.settrace(previous)
        requested.set()
        caller.join(5)
        if caller.is_alive():
            raise AssertionError("owned_report_mutator_shutdown_unconfirmed")
        observed["mutator_joined"] = True
    if "trace_line" not in observed:
        raise AssertionError("report_scheduling_boundary_not_executed")
    if observed["trace_thread_id"] == observed["mutator_thread_id"]:
        raise AssertionError("report_mutation_requires_a_distinct_real_thread")
    return result, observed


class PrivacySnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = synthetic_policy()
        cls.clean = run_probe("clean")["evidence"]

    def refused(self, action, code):
        with self.assertRaises(Refusal) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        self.assertTrue(
            str(caught.exception) == code and "SYNTHETIC_PROHIBITED_CALLER_CONTENT" not in str(caught.exception),
            "snapshot_refusal_must_be_static_and_echo_safe",
        )

    def test_caller_thread_cannot_insert_private_field_after_checks_before_serialization(self):
        report = copy.deepcopy(self.clean)
        original = canonical(report)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="privacy-n1-before-after-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            output, schedule = scheduled_mutation(
                lambda: store.write(report, self.policy, now=int(time.time())),
                lambda: report.__setitem__("raw_message", "SYNTHETIC_PROHIBITED_CALLER_CONTENT"),
                code=validate_report.__code__, line=validation_boundary(),
            )
            private_path(output, directory=False)
            persisted = output.read_bytes()
            safe = "raw_message" not in document(persisted)
            readback_code = "valid"
            try:
                validate_report(document(persisted), self.policy, now=int(time.time()))
            except Refusal as error:
                readback_code = error.code
            original_bytes = persisted == original
        BOUNDARY_OBSERVATIONS.append({
            "finding": "N1", "actual_process_id": os.getpid(), **schedule,
            "private_file_written": True, "private_field_withheld": safe,
            "persisted_exact_original_snapshot": original_bytes,
            "readback_code": readback_code, "named_fixture_removed": not output.exists(),
            "duration_seconds": round(time.monotonic() - started, 6),
            "production_functions_replaced": False,
        })
        self.assertTrue(safe, "unvalidated_private_field_must_not_be_persisted")
        self.assertEqual("valid", readback_code)
        self.assertTrue(original_bytes, "only_the_prevalidation_snapshot_may_be_persisted")
        self.assertEqual("SYNTHETIC_PROHIBITED_CALLER_CONTENT", report["raw_message"])

    def test_first_schema_check_reads_only_a_private_snapshot_not_caller_containers(self):
        report = copy.deepcopy(self.clean)
        original = canonical(report)
        private = {}

        def inspect_snapshot(frame):
            private.update({
                "private_root": frame.f_locals["report"] is not report,
                "private_nested": frame.f_locals["report"]["hosts"] is not report["hosts"],
                "immutable_bytes_present": type(frame.f_locals.get("raw")) is bytes,
            })
            return True

        raw, _ = scheduled_mutation(
            lambda: validate_report(report, self.policy, now=int(time.time())),
            lambda: report["hosts"][0].__setitem__("raw_message", "SYNTHETIC_PROHIBITED_CALLER_CONTENT"),
            code=validate_report.__code__,
            line=source_line(validate_report, 'exact_keys(report, REPORT_KEYS, "unscrubbed_evidence")'),
            predicate=inspect_snapshot,
        )
        self.assertTrue(all(private.values()), "all_mutable_caller_fields_must_be_detached_before_schema_validation")
        self.assertTrue(raw == original, "schema_validation_must_read_the_original_bounded_snapshot")

    def test_late_nested_identity_policy_count_and_journey_changes_cannot_reach_private_storage(self):
        for control in ("nested_private", "host", "fields", "run", "policy", "counter", "journey", "violations", "expiry"):
            with self.subTest(control=control), tempfile.TemporaryDirectory(prefix="privacy-n1-nested-") as temporary:
                report = copy.deepcopy(self.clean)
                original = canonical(report)
                store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)

                def mutate():
                    if control == "nested_private":
                        report["hosts"][0]["raw_message"] = "SYNTHETIC_PROHIBITED_CALLER_CONTENT"
                    elif control == "host":
                        report["hosts"][0]["host"] = "private-synthetic-identifier.synthetic.invalid"
                    elif control == "fields":
                        report["payload_fields"]["synthetic_probe"].append("raw_message")
                    elif control == "run":
                        report["run_id"] = uuid.uuid4().hex
                    elif control == "policy":
                        report["policy_sha256"] = "0" * 64
                    elif control == "counter":
                        report["forwarded_count"] = 0
                    elif control == "journey":
                        report["observed_journeys"].append("ingestion")
                    elif control == "violations":
                        report["violations"].append({"code": "unknown_destination", "host": None, "count": 1})
                    else:
                        report["expires_at"] = report["created_at"] - 1

                output, _ = scheduled_mutation(
                    lambda: store.write(report, self.policy, now=int(time.time())),
                    mutate, code=validate_report.__code__, line=validation_boundary(),
                )
                persisted = output.read_bytes()
                self.assertTrue(persisted == original, "caller_mutation_must_not_change_validated_storage_bytes")
                self.assertEqual(f"privacy-evidence-{document(original)['run_id']}.json", output.name)
                self.assertFalse(prohibited_present(persisted), "private_metadata_survived_snapshot_boundary")
                validate_report(document(persisted), self.policy, now=int(time.time()))

    def test_mid_snapshot_dictionary_or_list_mutation_refuses_without_an_evidence_file(self):
        capture_code = next(
            value for value in snapshot_document.__code__.co_consts
            if isinstance(value, types.CodeType) and value.co_name == "capture"
        )
        for control in ("dict", "list"):
            with self.subTest(control=control), tempfile.TemporaryDirectory(prefix="privacy-n1-copy-") as temporary:
                report = copy.deepcopy(self.clean)
                store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
                target = report if control == "dict" else report["hosts"]
                line = source_line(
                    snapshot_document,
                    "owned_dict[key] = capture(child, depth + 1)" if control == "dict" else
                    "owned_list.append(capture(item[index], depth + 1))",
                )

                def mutation():
                    if control == "dict":
                        target["raw_message"] = "SYNTHETIC_PROHIBITED_CALLER_CONTENT"
                    else:
                        target.clear()

                def outcome():
                    try:
                        store.write(report, self.policy, now=int(time.time()))
                        return "written"
                    except Refusal as error:
                        return error.code

                result, schedule = scheduled_mutation(
                    outcome, mutation, code=capture_code, line=line,
                    predicate=lambda frame: frame.f_locals["item"] is target,
                )
                self.assertEqual("snapshot_input_changed", result)
                self.assertTrue(schedule["mutator_joined"])
                self.assertFalse(any(store.root.glob("privacy-evidence-*.json")))

    def test_immutable_byte_snapshot_is_independent_of_nested_caller_changes(self):
        report = copy.deepcopy(self.clean)
        before = canonical(report)
        raw = snapshot_document(report)
        self.assertIs(type(raw), bytes)
        report["hosts"][0]["raw_message"] = "SYNTHETIC_PROHIBITED_CALLER_CONTENT"
        report["payload_fields"]["synthetic_probe"].clear()
        report["run_id"] = uuid.uuid4().hex
        detached = bytearray(raw)
        detached[0] = ord("[")
        self.assertTrue(raw == before, "mutable_inputs_and_byte_copies_cannot_change_the_owned_bytes")
        validate_report(document(raw), self.policy, now=int(time.time()))
        self.refused(lambda: validate_report(detached, self.policy, now=int(time.time())), "document_object_required")

    def test_signing_after_real_caller_mutation_packages_exactly_the_validated_bytes(self):
        report = copy.deepcopy(self.clean)
        original = canonical(report)
        run_id = report["run_id"]
        key, public = test_key()

        def mutate():
            report["raw_message"] = "SYNTHETIC_PROHIBITED_CALLER_CONTENT"
            report["hosts"][0]["host"] = "private-synthetic-identifier.synthetic.invalid"
            report["run_id"] = uuid.uuid4().hex

        raw, schedule = scheduled_mutation(
            lambda: sign_pack(report, self.policy, public_key=public, signer=key.sign, now=int(time.time())),
            mutate, code=sign_pack.__code__,
            line=source_line(sign_pack, "signature = signer(DOMAIN + payload)"),
        )
        restored = verify_pack(raw, self.policy, trusted_public_key=public, now=int(time.time()), expected_run_id=run_id)
        self.assertTrue(canonical(restored) == original, "signature_and_payload_must_bind_one_snapshot")
        self.assertFalse(prohibited_present(raw), "raw_caller_mutation_must_not_enter_signed_output")
        self.assertTrue(schedule["mutator_joined"])
        with tempfile.TemporaryDirectory(prefix="privacy-n1-signed-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            output = store.write(restored, self.policy, now=int(time.time()))
            self.assertTrue(output.read_bytes() == original)
            restored["raw_message"] = "SYNTHETIC_PROHIBITED_CALLER_CONTENT"
            self.refused(lambda: store.write(restored, self.policy, now=int(time.time())), "unscrubbed_evidence")
            second = verify_pack(raw, self.policy, trusted_public_key=public, now=int(time.time()), expected_run_id=run_id)
            self.assertTrue(canonical(second) == original)

    def test_snapshot_type_size_depth_and_node_limits_refuse_before_storage_without_echo(self):
        class Unserializable:
            def __str__(self):
                raise AssertionError("custom_string_conversion_must_not_be_called")

        for value in (b"private", ("private",), {"private"}, Unserializable()):
            report = copy.deepcopy(self.clean)
            report["raw_message"] = value
            self.refused(lambda: validate_report(report, self.policy, now=int(time.time())), "snapshot_type_refused")
        self.refused(lambda: snapshot_document({1: "private"}), "snapshot_type_refused")
        for value in (float("nan"), float("inf"), float("-inf")):
            self.refused(lambda: snapshot_document({"x": value}), "non_finite_json")
        self.refused(lambda: snapshot_document({"x": "x" * MAX_DOCUMENT_BYTES}), "document_size_refused")
        self.refused(lambda: snapshot_document({"x": 1 << (MAX_DOCUMENT_BYTES * 4)}), "document_size_refused")
        self.refused(lambda: snapshot_document({"x": [False] * MAX_NODES}), "document_complexity_refused")
        value = {}
        value["cycle"] = value
        self.refused(lambda: snapshot_document(value), "document_complexity_refused")
        self.assertEqual(MAX_DOCUMENT_BYTES, len(snapshot_document({"x": "x" * (MAX_DOCUMENT_BYTES - 8)})))
        exact_nodes = {"x": [False] * (MAX_NODES - 2)}
        document(snapshot_document(exact_nodes))
        depth = {"x": 0}
        for _ in range(MAX_DEPTH):
            depth = {"x": depth}
        self.refused(lambda: snapshot_document(depth), "document_complexity_refused")
        for report in (
            {**copy.deepcopy(self.clean), "raw_message": "x" * MAX_DOCUMENT_BYTES},
            {**copy.deepcopy(self.clean), "raw_message": Unserializable()},
        ):
            with tempfile.TemporaryDirectory(prefix="privacy-n1-invalid-") as temporary:
                store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
                with self.assertRaises(Refusal):
                    store.write(report, self.policy, now=int(time.time()))
                self.assertFalse(any(store.root.glob("privacy-evidence-*.json")))

    def test_normal_snapshot_does_not_write_or_mutate_any_caller_container(self):
        report = copy.deepcopy(self.clean)
        before = canonical(report)
        root_id, hosts_id, host_id = id(report), id(report["hosts"]), id(report["hosts"][0])
        raw = validate_report(report, self.policy, now=int(time.time()))
        self.assertTrue(raw == before and canonical(report) == before, "normal_validation_must_preserve_caller_data")
        self.assertEqual((root_id, hosts_id, host_id), (id(report), id(report["hosts"]), id(report["hosts"][0])))
        key, public = test_key()
        pack = sign_pack(report, self.policy, public_key=public, signer=key.sign, now=int(time.time()))
        self.assertTrue(canonical(report) == before, "signing_must_not_mutate_the_caller")
        self.assertTrue(
            canonical(verify_pack(pack, self.policy, trusted_public_key=public, now=int(time.time()),
                                  expected_run_id=report["run_id"])) == before,
        )

    def test_concurrent_value_changes_produce_only_closed_valid_snapshot_or_fixed_refusal(self):
        report = copy.deepcopy(self.clean)
        started, stop = threading.Event(), threading.Event()

        def mutate():
            started.set()
            while not stop.wait(0.0001):
                report["hosts"][0]["host"] = "private-synthetic-identifier.synthetic.invalid"
                report["hosts"][0]["host"] = "analytics.synthetic.invalid"

        caller = threading.Thread(target=mutate, name="privacy-owned-snapshot-values")
        caller.start()
        self.assertTrue(started.wait(1))
        outcomes = []
        try:
            for _ in range(25):
                try:
                    raw = validate_report(report, self.policy, now=int(time.time()))
                except Refusal as error:
                    self.assertIn(error.code, ("untrusted_host_metadata", "snapshot_input_changed"))
                    outcomes.append("refused")
                else:
                    self.assertFalse(prohibited_present(raw), "concurrent_value_entered_snapshot_output")
                    validate_report(document(raw), self.policy, now=int(time.time()))
                    outcomes.append("valid")
        finally:
            stop.set()
            caller.join(5)
            self.assertFalse(caller.is_alive(), "owned_snapshot_caller_shutdown_unconfirmed")
        self.assertEqual(25, len(outcomes))
        BOUNDARY_OBSERVATIONS.append({
            "finding": "N1_concurrent_snapshot", "attempts": len(outcomes),
            "closed_valid_snapshots": outcomes.count("valid"), "fixed_refusals": outcomes.count("refused"),
            "owned_caller_joined": True, "prohibited_output": False,
        })


if __name__ == "__main__":
    unittest.main()
