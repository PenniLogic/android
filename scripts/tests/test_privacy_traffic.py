"""Synthetic sockets/processes and adversarial evidence controls; no device or user traffic."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from privacy_traffic.capture import SENTINEL, Inspector
from privacy_traffic.evidence import EvidenceStore, private_path, validate_report
from privacy_traffic.pack import DOMAIN, sign_pack, verify_pack
from privacy_traffic.policy import DEVICE_ONLY_FIELDS, JOURNEYS, LIMITS, POLICY_PATH, ROOT, Policy
from privacy_traffic.probe import synthetic_policy
from privacy_traffic.proxy import InterceptProxy, SyntheticOrigin, read_http, synthetic_request
from privacy_traffic.release import ReportingWindow, privacy_exit
from privacy_traffic.safety import MAX_DOCUMENT_BYTES, Refusal, canonical, document, physical
from privacy_traffic.tls import MemoryCA, check_runtime


CAPTURE_RUNS: list[dict] = []


class ByteChannel:
    def __init__(self, raw: bytes):
        self.stream = io.BytesIO(raw)

    def recv(self, size: int) -> bytes:
        return self.stream.read(size)

    def sendall(self, _: bytes) -> None:
        raise AssertionError("parser_fixture_is_read_only")


def cli(arguments: list[str]) -> tuple[int, dict]:
    command = [sys.executable, str(ROOT / "scripts" / "privacy_traffic_harness.py"), *arguments]
    started = time.monotonic()
    if os.name == "nt":
        from windows_processes import run_command
        lines: list[str] = []
        size = 0

        def consume(line: str) -> None:
            nonlocal size
            size += len(line)
            if size > MAX_DOCUMENT_BYTES:
                raise Refusal("process_output_size_refused")
            lines.append(line)

        code = run_command(command, ROOT, consume)
        raw = "".join(lines)
    else:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        code, raw = result.returncode, result.stdout + result.stderr
    require_clean = SENTINEL not in raw and hashlib.sha256(SENTINEL.encode("ascii")).hexdigest() not in raw.lower()
    if not require_clean:
        raise AssertionError("process_output_contained_prohibited_synthetic_content")
    objects = [line for line in raw.splitlines() if line.startswith("{")]
    if len(objects) != 1:
        raise AssertionError("process_did_not_return_one_sanitized_document")
    value = document(objects[0].encode("utf-8"))
    if arguments[0] == "source-probe":
        CAPTURE_RUNS.append({
            "scenario": arguments[1], "exit_code": code,
            "duration_seconds": round(time.monotonic() - started, 3),
            "capture_process_id": value.get("capture_process_id"),
            "origin_requests": value.get("origin_requests"),
            "evidence": value.get("evidence"),
            "proxy_shutdown_confirmed": value.get("proxy_shutdown_confirmed"),
        })
    return code, value


class PrivacyWireTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        check_runtime()
        cls.clean_code, cls.clean = cli(["source-probe", "clean"])

    def negative(self, scenario: str, expected: str) -> dict:
        code, result = cli(["source-probe", scenario])
        self.assertEqual(1, code, "negative_wire_control_must_fail")
        self.assertIs(result["source_assertions_passed"], False)
        self.assertIs(result["proxy_shutdown_confirmed"], True)
        self.assertIs(result["release_qualified"], False)
        self.assertTrue(
            expected in {item["code"] for item in result["evidence"]["violations"]},
            "expected_wire_refusal_missing",
        )
        self.assertEqual(2, result["origin_requests"], "prohibited_payload_must_not_reach_origin")
        return result

    def test_real_tls_proxy_forwards_three_synthetic_paths_without_product_coverage(self):
        self.assertEqual(0, self.clean_code)
        evidence = self.clean["evidence"]
        self.assertEqual(3, evidence["connection_count"])
        self.assertEqual(3, evidence["tls_interceptions"])
        self.assertEqual(3, evidence["forwarded_count"])
        self.assertEqual(3, self.clean["origin_requests"])
        self.assertEqual([], evidence["observed_journeys"])
        self.assertEqual(list(JOURNEYS), evidence["unmet_required_journeys"])
        self.assertEqual(sorted(JOURNEYS), evidence["observed_synthetic_paths"])
        self.assertIs(evidence["release_qualified"], False)
        self.assertGreater(self.clean["capture_process_id"], 0)
        self.assertGreater(evidence["capture_bytes"], 0)

    def test_planted_raw_message_fails_real_outbound_capture(self):
        self.negative("raw", "raw_message_egress")

    def test_json_escaped_raw_message_fails_decoded_capture(self):
        self.negative("raw_escaped", "raw_message_egress")

    def test_raw_message_in_header_is_refused_without_echo(self):
        self.negative("raw_header", "raw_message_egress")

    def test_raw_message_in_url_is_refused_without_url_echo(self):
        self.negative("raw_url", "raw_message_egress")

    def test_integer_minor_units_and_currency_are_forbidden_in_analytics(self):
        self.negative("money_field", "monetary_analytics")

    def test_money_disguised_as_allowed_analytics_field_value_is_refused(self):
        self.negative("money_value", "monetary_analytics")

    def test_unknown_destination_is_named_only_as_safe_host(self):
        result = self.negative("unknown_host", "unknown_destination")
        violation = next(item for item in result["evidence"]["violations"] if item["code"] == "unknown_destination")
        self.assertEqual("unlisted.synthetic.invalid", violation["host"])
        self.assertTrue(any(item["host"] == "unlisted.synthetic.invalid" for item in result["evidence"]["hosts"]))

    def test_opaque_payload_is_not_silently_skipped(self):
        self.negative("opaque", "opaque_payload")

    def test_blind_index_value_never_serialized(self):
        self.negative("bidx", "device_only_artifact_egress")

    def test_raw_derived_digest_not_serializable(self):
        self.negative("raw_digest_field", "raw_derived_digest_egress")

    def test_raw_digest_stays_local_even_under_an_allowed_field_name(self):
        self.negative("raw_digest_value", "raw_derived_digest_egress")

    def test_source_event_id_shape_does_not_prove_non_raw_origin(self):
        self.negative("source_event_id", "source_event_origin_unverified")

    def test_required_synthetic_path_missing_fails_source_run(self):
        code, result = cli(["source-probe", "missing_path"])
        self.assertEqual(1, code)
        self.assertEqual(["clarification"], result["missing_synthetic_paths"])
        self.assertEqual(2, result["origin_requests"])
        self.assertEqual(list(JOURNEYS), result["evidence"]["unmet_required_journeys"])

    def test_oversized_wire_payload_is_explicit_failure(self):
        self.negative("oversized", "body_size_refused")

    def test_actual_rc_command_refuses_all_missing_real_providers(self):
        code, result = cli(["run-rc"])
        self.assertEqual(2, code)
        self.assertEqual("rc_inputs_unavailable", result["code"])
        for name in (
            "journey_ingestion_provider", "journey_clarification_provider", "journey_analytics_provider",
            "analytics_instrumentation_provider", "proxy_runner", "approved_evidence_signer",
            "protected_producer_attestation", "release_candidate_apk", "release_artifact_publication",
        ):
            self.assertIn(name, result["missing"])
        self.assertIs(result["release_qualified"], False)

    def test_allowlist_schema_drift_fails_actual_socket_capture(self):
        data = copy.deepcopy(synthetic_policy().data)
        del data["network_schemas"]["synthetic_probe"]["fields"]["outcome"]
        inspector = Inspector(Policy(data))
        ca = MemoryCA()
        with SyntheticOrigin(ca) as origin:
            with InterceptProxy(inspector, ca, origin) as proxy:
                status = synthetic_request(
                    proxy, "analytics.synthetic.invalid", "/probe/analytics",
                    canonical({"event": "synthetic_probe", "stage": "analytics", "outcome": "ok", "synthetic": True}),
                )
                self.assertEqual(422, status)
            self.assertEqual(0, origin.requests)
        self.assertTrue(any(code == "undeclared_payload_field" for code, _ in inspector.violations))

    def test_source_capture_can_persist_only_scrubbed_private_evidence(self):
        with tempfile.TemporaryDirectory(prefix="privacy-process-store-") as temporary:
            root = Path(temporary) / "owned"
            code, result = cli(["source-probe", "clean", "--store", str(root), "--owner-id", uuid.uuid4().hex])
            self.assertEqual(0, code)
            outputs = list(root.glob("privacy-evidence-*.json"))
            self.assertEqual(1, len(outputs))
            private_path(outputs[0], directory=False)
            validate_report(
                document(outputs[0].read_bytes()), synthetic_policy(), now=int(time.time()),
                expected_run_id=result["evidence"]["run_id"],
            )

    def test_untrusted_tls_certificate_is_refused_without_global_trust_bypass(self):
        ca = MemoryCA()
        inspector = Inspector(synthetic_policy())
        with SyntheticOrigin(ca) as origin:
            with InterceptProxy(inspector, ca, origin) as proxy:
                with socket.create_connection(("127.0.0.1", proxy.port), timeout=3) as client:
                    client.sendall(
                        b"CONNECT analytics.synthetic.invalid:443 HTTP/1.1\r\n"
                        b"Host: analytics.synthetic.invalid:443\r\n\r\n",
                    )
                    header = bytearray()
                    while not header.endswith(b"\r\n\r\n"):
                        header.extend(client.recv(1))
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        MemoryCA().client_context().wrap_socket(client, server_hostname="analytics.synthetic.invalid")
        self.assertEqual(0, origin.requests)


class PrivacyPolicyTest(unittest.TestCase):
    def refused(self, action, code: str):
        with self.assertRaises(Refusal) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        self.assertTrue(SENTINEL not in str(caught.exception), "refusal_must_not_echo_input")

    def test_source_policy_has_no_fabricated_network_or_journey_provider(self):
        policy = Policy.load()
        self.assertEqual([], policy.data["destinations"])
        self.assertEqual({}, policy.data["network_schemas"])
        self.assertEqual({}, policy.data["journey_providers"])
        self.assertEqual(132, len(policy.data["components"]["debug"]))
        self.assertEqual(127, len(policy.data["components"]["release"]))
        self.assertEqual(hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(), policy.sha256)

    def test_new_component_without_allowlist_update_fails_real_process(self):
        policy = Policy.load()
        inventory = {"schema_version": 1, "variants": copy.deepcopy(policy.data["components"])}
        inventory["variants"]["release"].append("org.synthetic:undeclared-component:1")
        with tempfile.TemporaryDirectory(prefix="privacy-components-") as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_bytes(canonical(inventory))
            code, result = cli(["check-components", str(path)])
        self.assertEqual(2, code)
        self.assertEqual("component_inventory_drift", result["code"])
        self.assertIs(result["release_qualified"], False)

    def test_new_host_and_undeclared_component_cannot_be_admitted_by_schema(self):
        data = copy.deepcopy(synthetic_policy().data)
        data["destinations"][0]["component"] = "org.synthetic:undeclared-component:1"
        self.refused(lambda: Policy(data), "undeclared_component")

    def test_device_only_fields_cannot_be_added_to_outbound_source_schema(self):
        for name in [*sorted(DEVICE_ONLY_FIELDS), "merchant_bidx", "source_event_id"]:
            with self.subTest(field=name):
                data = copy.deepcopy(synthetic_policy().data)
                data["network_schemas"]["synthetic_probe"]["fields"][name] = {
                    "kind": "boolean", "nullable": False,
                }
                self.refused(lambda: Policy(data), "device_only_schema_refused")

    def test_analytics_schema_cannot_admit_money_or_free_form_values(self):
        for name, rule in (
            ("amount_minor", {"kind": "integer", "minimum": 0, "maximum": 100, "nullable": False}),
            ("currency", {"kind": "enum", "values": ["USD"], "nullable": False}),
            ("outcome", {"kind": "pattern", "pattern": "[a-z]+", "max_length": 64, "nullable": False}),
        ):
            with self.subTest(field=name):
                data = copy.deepcopy(synthetic_policy().data)
                data["network_schemas"]["synthetic_probe"]["fields"][name] = rule
                self.refused(lambda: Policy(data), "unsafe_analytics_schema")

    def test_actual_log_schema_refuses_added_field_and_value_drift(self):
        payload = {
            "event": "app_start", "build_type": "debug", "version_name": "0.1.0-debug",
            "version_code": 1, "configuration": "loaded", "problems": [],
        }
        policy = Policy.load()
        policy.payload_check("app_start", payload, local=True)
        payload["raw_message"] = SENTINEL
        self.refused(lambda: policy.payload_check("app_start", payload, local=True), "undeclared_payload_field")
        del payload["raw_message"]
        payload["version_name"] = SENTINEL
        self.refused(lambda: policy.payload_check("app_start", payload, local=True), "payload_value_refused")

    def test_required_journeys_and_budgets_cannot_be_demoted(self):
        for mutation in ("journeys", "budget"):
            data = copy.deepcopy(Policy.load().data)
            if mutation == "journeys":
                data["required_journeys"] = ["analytics"]
                code = "required_journey_drift"
            else:
                data["limits"]["max_body_bytes"] += 1
                code = "capture_budget_drift"
            self.refused(lambda: Policy(data), code)

    def test_analytics_route_cannot_be_reclassified_as_api_or_local_log(self):
        for category, code in (("api", "analytics_route_drift"), ("local_log", "payload_category_drift")):
            data = copy.deepcopy(synthetic_policy().data)
            data["network_schemas"]["synthetic_probe"]["category"] = category
            self.refused(lambda: Policy(data), code)

    def test_boolean_origin_success_cannot_be_coerced_from_a_number(self):
        inspector = Inspector(synthetic_policy())
        self.refused(lambda: inspector.response(b'{"ok":1}'), "opaque_response")

    def test_empty_traffic_is_refused_not_passed(self):
        policy = synthetic_policy()
        report = Inspector(policy).report(now=int(time.time()))
        self.refused(lambda: validate_report(report, policy, now=int(time.time())), "empty_traffic")

    def test_capture_request_byte_and_time_limits_are_fail_closed(self):
        inspector = Inspector(synthetic_policy())
        for _ in range(LIMITS["max_requests"]):
            inspector.connection()
        self.refused(inspector.connection, "request_budget_exceeded")
        self.assertEqual(LIMITS["max_requests"], inspector.connections)
        inspector = Inspector(synthetic_policy())
        self.refused(lambda: inspector.budget(LIMITS["max_capture_bytes"] + 1), "capture_budget_exceeded")
        inspector = Inspector(synthetic_policy())
        inspector.started -= LIMITS["run_timeout_seconds"] + 1
        self.refused(inspector.budget, "capture_timeout")

    def test_malformed_opaque_duplicate_deep_and_oversized_documents_are_refused(self):
        for name, raw in (
            ("malformed", b'{"broken":'),
            ("opaque", b"\x00\xff\xfe"),
            ("duplicate", b'{"value":true,"value":false}'),
            ("non_finite", b'{"value":NaN}'),
            ("deep", b'{"value":' + b"[" * 20 + b"0" + b"]" * 20 + b"}"),
            ("oversized", b" " * (MAX_DOCUMENT_BYTES + 1)),
        ):
            with self.subTest(case=name), self.assertRaises(Refusal):
                document(raw)

    def test_http_ambiguity_compression_and_bounds_are_refused(self):
        for name, raw in (
            ("duplicate", b"POST / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n{}"),
            ("compressed", b"POST / HTTP/1.1\r\nContent-Length: 2\r\nContent-Encoding: gzip\r\n\r\n{}"),
            ("chunked", b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"),
            ("huge_header", b"POST / HTTP/1.1\r\nX: " + b"x" * 8193),
            ("huge_body", b"POST / HTTP/1.1\r\nContent-Length: 65537\r\n\r\n"),
            ("truncated", b"POST / HTTP/1.1\r\nContent-Length: 2\r\n\r\n"),
        ):
            with self.subTest(case=name), self.assertRaises(Refusal):
                read_http(ByteChannel(raw))


class PrivacyEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code, result = cli(["source-probe", "clean"])
        if code != 0:
            raise AssertionError("real_capture_required_for_evidence_tests")
        cls.report = result["evidence"]
        cls.policy = synthetic_policy()

    def refused(self, action, code: str):
        with self.assertRaises(Refusal) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def report_copy(self):
        return copy.deepcopy(self.report)

    def test_scrubbed_evidence_is_reviewable_without_rerunning_capture(self):
        raw = validate_report(self.report, self.policy, now=int(time.time()), expected_run_id=self.report["run_id"])
        restored = document(raw)
        self.assertEqual(3, restored["forwarded_count"])
        self.assertEqual(["event", "outcome", "stage", "synthetic"], restored["payload_fields"]["synthetic_probe"])
        self.assertIs(restored["release_qualified"], False)

    def test_unscrubbed_evidence_cannot_be_written(self):
        report = self.report_copy()
        report["raw_message"] = SENTINEL
        with tempfile.TemporaryDirectory(prefix="privacy-scrub-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            self.refused(lambda: store.write(report, self.policy, now=int(time.time())), "unscrubbed_evidence")
            self.assertEqual(["privacy-store.json"], sorted(path.name for path in store.root.iterdir()))

    def test_stale_future_unbounded_retention_and_false_journey_evidence_are_refused(self):
        now = int(time.time())
        for name in ("stale", "future", "retention", "journey", "qualification", "run"):
            with self.subTest(case=name):
                report = self.report_copy()
                if name == "stale":
                    report["created_at"], report["expires_at"] = now - 2, now - 1
                elif name == "future":
                    report["created_at"], report["expires_at"] = now + 1, now + 2
                elif name == "retention":
                    report["expires_at"] = report["created_at"] + 86401
                elif name == "journey":
                    report["observed_journeys"] = ["ingestion"]
                elif name == "qualification":
                    report["release_qualified"] = True
                else:
                    report["run_id"] = uuid.uuid4().hex
                with self.assertRaises(Refusal):
                    validate_report(report, self.policy, now=now, expected_run_id=self.report["run_id"])

    def test_malformed_inventory_values_are_explicit_refusals_without_type_errors(self):
        for name, mutation in (
            ("violation_code", {"violation_count": 1, "violations": [{"code": [], "host": None, "count": 1}]}),
            ("journey_list", {"observed_synthetic_paths": [[]]}),
            ("host", {"hosts": [{"host": ["not_a_host"], "count": 1}]}),
            ("boolean_count", {"request_count": True}),
            ("unknown_field", {"payload_fields": {"synthetic_probe": ["raw_message"]}}),
        ):
            with self.subTest(case=name):
                report = self.report_copy()
                report.update(mutation)
                with self.assertRaises(Refusal):
                    validate_report(report, self.policy, now=int(time.time()))

    def test_owned_store_preserves_siblings_and_purges_only_expired_named_evidence(self):
        report = self.report_copy()
        now = int(time.time())
        report["created_at"], report["expires_at"] = now, now + 1
        with tempfile.TemporaryDirectory(prefix="privacy-retention-") as temporary:
            root = Path(temporary)
            sibling = root / "sibling-sentinel.txt"
            sibling.write_bytes(b"SYNTHETIC_KEEP")
            store = EvidenceStore(root / "owned", owner_id=uuid.uuid4().hex, create=True)
            output = store.write(report, self.policy, now=now)
            self.assertEqual(0, store.purge_expired(self.policy, now=now))
            self.assertEqual(1, store.purge_expired(self.policy, now=now + 2))
            self.assertFalse(output.exists())
            self.assertTrue(sibling.read_bytes() == b"SYNTHETIC_KEEP", "unowned_sibling_changed")

    def test_existing_evidence_and_foreign_store_entries_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="privacy-custody-") as temporary:
            root = Path(temporary)
            store = EvidenceStore(root / "owned", owner_id=uuid.uuid4().hex, create=True)
            output = store.write(self.report, self.policy, now=int(time.time()))
            self.refused(lambda: store.write(self.report, self.policy, now=int(time.time())), "evidence_overwrite_refused")
            foreign = store.root / "foreign.txt"
            foreign.write_bytes(b"SYNTHETIC_KEEP")
            self.refused(lambda: store.purge_expired(self.policy, now=int(time.time()) + 3601), "store_foreign_entry")
            self.assertTrue(output.exists() and foreign.exists())

    def test_wrong_owner_or_changed_store_marker_refuses_writes(self):
        with tempfile.TemporaryDirectory(prefix="privacy-owner-") as temporary:
            root = Path(temporary)
            store = EvidenceStore(root / "owned", owner_id=uuid.uuid4().hex, create=True)
            self.refused(lambda: EvidenceStore(store.root, owner_id=uuid.uuid4().hex), "store_owner_mismatch")
            (store.root / "privacy-store.json").write_bytes(canonical({"format": "privacy_store_v1", "owner_id": uuid.uuid4().hex}))
            self.refused(lambda: store.write(self.report, self.policy, now=int(time.time())), "store_owner_mismatch")

    def test_public_output_permissions_are_refused_on_the_actual_platform(self):
        with tempfile.TemporaryDirectory(prefix="privacy-public-") as temporary:
            root = Path(temporary)
            if os.name == "nt":
                result = subprocess.run(
                    ["icacls", str(root), "/grant", "*S-1-1-0:(RX)"],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(0, result.returncode, "owned_public_acl_negative_control_failed")
            else:
                root.chmod(0o755)
            self.refused(lambda: private_path(root, directory=True), "output_not_private")

    def test_hardlinked_evidence_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="privacy-hardlink-") as temporary:
            root = Path(temporary)
            store = EvidenceStore(root / "owned", owner_id=uuid.uuid4().hex, create=True)
            output = store.write(self.report, self.policy, now=int(time.time()))
            os.link(output, root / "alias.json")
            with self.assertRaises(Refusal):
                private_path(output, directory=False)

    def test_symbolic_link_ancestors_refuse_storage_and_preserve_target(self):
        with tempfile.TemporaryDirectory(prefix="privacy-link-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            sentinel = target / "sibling.txt"
            sentinel.write_bytes(b"SYNTHETIC_KEEP")
            link = root / "alias"
            try:
                os.symlink(target, link, target_is_directory=True)
            except OSError as error:
                if os.name == "nt" and error.winerror == 1314:
                    self.skipTest("Windows symbolic-link privilege unavailable; no host setting changed")
                raise
            self.refused(lambda: physical(link / "owned"), "path_alias_refused")
            self.assertTrue(sentinel.read_bytes() == b"SYNTHETIC_KEEP", "link_target_changed")

    @unittest.skipUnless(os.name == "nt", "Windows junction control; symlink control covers POSIX")
    def test_windows_junction_ancestor_is_refused_without_traversal(self):
        with tempfile.TemporaryDirectory(prefix="privacy-junction-") as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "alias"
            environment = {**os.environ, "PRIVACY_LINK_PATH": str(link), "PRIVACY_LINK_TARGET": str(target)}
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "New-Item -ItemType Junction -Path $env:PRIVACY_LINK_PATH -Target $env:PRIVACY_LINK_TARGET | Out-Null"],
                capture_output=True, text=True, timeout=15, env=environment,
            )
            self.assertEqual(0, result.returncode, "owned_junction_creation_failed")
            try:
                self.refused(lambda: physical(link / "owned"), "path_alias_refused")
                self.assertEqual([], list(target.iterdir()))
            finally:
                if link.exists():
                    link.rmdir()

    def test_real_ed25519_signature_and_tamper_wrong_key_and_domain_refusals(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        now = int(time.time())
        pack = sign_pack(self.report, self.policy, public_key=public, signer=key.sign, now=now)
        restored = verify_pack(pack, self.policy, trusted_public_key=public, now=now, expected_run_id=self.report["run_id"])
        self.assertEqual(3, restored["forwarded_count"])
        self.assertIs(restored["release_qualified"], False)
        value = document(pack)
        value["payload"]["capture_bytes"] += 1
        self.refused(
            lambda: verify_pack(canonical(value), self.policy, trusted_public_key=public, now=now, expected_run_id=self.report["run_id"]),
            "invalid_signature",
        )
        other = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.refused(
            lambda: verify_pack(pack, self.policy, trusted_public_key=other, now=now, expected_run_id=self.report["run_id"]),
            "untrusted_signature",
        )
        self.refused(
            lambda: sign_pack(self.report, self.policy, public_key=public, signer=lambda data: key.sign(data[len(DOMAIN):]), now=now),
            "invalid_signature",
        )

    def test_real_signed_source_pack_is_verified_by_the_actual_cli_without_rc_approval(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        raw = sign_pack(self.report, self.policy, public_key=public, signer=key.sign, now=int(time.time()))
        with tempfile.TemporaryDirectory(prefix="privacy-signature-cli-") as temporary:
            root = Path(temporary)
            pack_path, public_path = root / "source-pack.json", root / "public-key.bin"
            pack_path.write_bytes(raw)
            public_path.write_bytes(public)
            code, result = cli([
                "verify-pack", str(pack_path), "--trusted-public-key", str(public_path), "--run-id", self.report["run_id"],
            ])
        self.assertEqual(0, code)
        self.assertIs(result["signature_verified"], True)
        self.assertIs(result["release_qualified"], False)

    def test_missing_host_or_payload_inventory_cannot_be_green_evidence(self):
        for name in ("hosts", "payload_fields"):
            report = self.report_copy()
            report[name] = [] if name == "hosts" else {}
            self.refused(
                lambda: validate_report(report, self.policy, now=int(time.time())),
                "incomplete_capture_evidence",
            )

    def test_missing_signer_and_untrusted_or_opaque_pack_are_not_manifest_success(self):
        self.refused(
            lambda: sign_pack(self.report, self.policy, public_key=b"x" * 32, signer=None, now=int(time.time())),
            "evidence_signer_missing",
        )
        self.refused(
            lambda: verify_pack(
                b'{"sha256":"not_a_signature"}', self.policy, trusted_public_key=b"x" * 32,
                now=int(time.time()), expected_run_id=self.report["run_id"],
            ),
            "invalid_signed_pack",
        )
        with self.assertRaises(Refusal):
            verify_pack(b"\x00\xff", self.policy, trusted_public_key=b"x" * 32, now=int(time.time()), expected_run_id=self.report["run_id"])

    def test_reporting_is_one_rc_only_expiring_and_never_waives_evidence_or_providers(self):
        for mode in ("first_rc_reporting", "rollback_reporting"):
            window = ReportingWindow(mode, "synthetic_rc_one", 100, 200)
            self.assertEqual(0, privacy_exit(
                violation_count=1, rc_id="synthetic_rc_one", now=150,
                evidence_ready=True, providers_ready=True, verified_window=window,
            ))
            for rc, now, evidence, providers in (
                ("synthetic_rc_two", 150, True, True),
                ("synthetic_rc_one", 200, True, True),
                ("synthetic_rc_one", 150, False, True),
                ("synthetic_rc_one", 150, True, False),
            ):
                self.assertNotEqual(0, privacy_exit(
                    violation_count=1, rc_id=rc, now=now, evidence_ready=evidence,
                    providers_ready=providers, verified_window=window,
                ))
        self.refused(lambda: ReportingWindow("rollback_reporting", "synthetic_rc_one", 100, 100), "reporting_expiry_required")

    def test_malformed_reporting_inputs_cannot_be_truthy_success(self):
        for name, value in (("evidence_ready", "false"), ("providers_ready", 1), ("now", True), ("rc_id", [])):
            parameters = {
                "violation_count": 0, "rc_id": "synthetic_rc_one", "now": 150,
                "evidence_ready": True, "providers_ready": True,
            }
            parameters[name] = value
            with self.subTest(case=name):
                self.refused(lambda: privacy_exit(**parameters), "invalid_rollout_input")


if __name__ == "__main__":
    unittest.main()
