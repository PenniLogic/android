"""Real regressions for the four independently reproduced f39 source findings."""

from __future__ import annotations

import copy
import base64
import hashlib
import multiprocessing
import os
import queue
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from privacy_traffic.capture import SENTINEL, Inspector
from privacy_traffic.evidence import EvidenceStore, FILE_NAME, STORE_LOCK, STORE_MARKER, private_path, validate_report
from privacy_traffic.pack import DOMAIN, sign_pack, verify_pack
from privacy_traffic.policy import LIMITS
from privacy_traffic.probe import run_probe, synthetic_policy
from privacy_traffic.proxy import InterceptProxy, SyntheticOrigin, read_http
from privacy_traffic.safety import Refusal, canonical, document, remaining_seconds
from privacy_traffic.tls import MemoryCA


BOUNDARY_OBSERVATIONS: list[dict] = []


def test_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, public


def encoded_hosts() -> list[str]:
    digest = hashlib.sha256(SENTINEL.encode("ascii")).hexdigest()
    return [
        f"{digest[:32]}.{digest[32:]}.synthetic.invalid",
        f"{digest[:32].upper()}.{digest[32:].upper()}.synthetic.invalid",
        f"{digest[:16]}-{digest[16:32]}.{digest[32:48]}-{digest[48:]}.synthetic.invalid",
        f"prefix-{digest[:32]}.{digest[32:]}-suffix.synthetic.invalid",
        "private-synthetic-identifier.synthetic.invalid",
        "unknown%2esynthetic.invalid",
        "https://unlisted.synthetic.invalid/private?value=synthetic",
        "synthetic@unlisted.synthetic.invalid",
    ]


def prohibited_present(raw: bytes) -> bool:
    digest = hashlib.sha256(SENTINEL.encode("ascii")).hexdigest()
    text = raw.decode("ascii").lower()
    reconstructed = "".join(character for character in text if character.isalnum())
    return digest in reconstructed or SENTINEL.lower() in text or "private-synthetic-identifier" in text


def unknown_connect(host: str) -> tuple[dict, int]:
    inspector = Inspector(synthetic_policy())
    ca = MemoryCA()
    with SyntheticOrigin(ca) as origin:
        with InterceptProxy(inspector, ca, origin) as proxy:
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=3) as client:
                client.sendall(
                    f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"),
                )
                response = bytearray()
                while b"\r\n\r\n" not in response:
                    part = client.recv(1024)
                    if not part:
                        break
                    response.extend(part)
                if not response.startswith(b"HTTP/1.1 422 "):
                    raise AssertionError("unknown_connect_must_be_refused")
        requests = origin.requests
    return inspector.report(now=int(time.time())), requests


def _writer(root, owner_id, report, entered, release, result, hold):
    class ObservedWriter(EvidenceStore):
        def _exclusive_write(self, path, raw):
            if hold and FILE_NAME.fullmatch(path.name):
                entered.set()
                if not release.wait(5):
                    raise Refusal("owned_writer_fixture_timeout")
            return super()._exclusive_write(path, raw)

    try:
        store = ObservedWriter(Path(root), owner_id=owner_id)
        result.put({"stage": "ready", "pid": os.getpid()})
        store.write(report, synthetic_policy(), now=int(time.time()))
        result.put({"stage": "result", "code": "written", "pid": os.getpid()})
    except Refusal as error:
        result.put({"stage": "result", "code": error.code, "pid": os.getpid()})


def _crash_lock(root, owner_id, held, release):
    store = EvidenceStore(Path(root), owner_id=owner_id)
    with store._locked():
        held.set()
        if not release.wait(5):
            os._exit(18)
        os._exit(17)


def unchecked_signed_pack(report, key, public) -> bytes:
    return canonical({
        "format": "pennilogic_privacy_signed_pack_v1", "payload": report,
        "signature": {
            "algorithm": "ed25519", "key_sha256": hashlib.sha256(public).hexdigest(),
            "value": base64.b64encode(key.sign(DOMAIN + canonical(report))).decode("ascii"),
        },
    })


class PrivacyCorrectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = synthetic_policy()
        cls.clean = run_probe("clean")["evidence"]

    def refused(self, action, code: str):
        with self.assertRaises(Refusal) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def test_encoded_raw_derived_hostname_is_scrubbed_before_private_persistence_and_signing(self):
        report, requests = unknown_connect(encoded_hosts()[0])
        key, public = test_key()
        now = int(time.time())
        with tempfile.TemporaryDirectory(prefix="privacy-correction-host-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            output = store.write(report, self.policy, now=now)
            private_path(output, directory=False)
            persisted = output.read_bytes()
            pack = sign_pack(report, self.policy, public_key=public, signer=key.sign, now=now)
            restored = verify_pack(
                pack, self.policy, trusted_public_key=public, now=now, expected_run_id=report["run_id"],
            )
            scrubbed = not prohibited_present(persisted) and not prohibited_present(pack)
        BOUNDARY_OBSERVATIONS.append({
            "finding": "F1", "origin_requests": requests, "private_persistence_scrubbed": scrubbed,
            "signature_verified": restored["release_qualified"] is False,
            "violation_count": report["violation_count"],
        })
        self.assertEqual(0, requests)
        self.assertEqual(1, report["violation_count"])
        self.assertTrue(scrubbed, "encoded_device_only_host_must_not_survive_evidence")

    def test_connect_absolute_capture_deadline_with_real_continuing_activity(self):
        inspector = Inspector(self.policy)
        ca = MemoryCA()
        started = inspector.started
        with SyntheticOrigin(ca) as origin:
            with InterceptProxy(inspector, ca, origin) as proxy:
                with socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as client:
                    client.sendall(b"CONNECT analytics.synthetic.invalid:443 HTTP/1.1\r\nHost: ")
                    while time.monotonic() < started + LIMITS["run_timeout_seconds"] + 0.25:
                        try:
                            client.sendall(b"x")
                        except OSError:
                            break
                        if not proxy._thread.is_alive():
                            break
                        time.sleep(0.1)
                    timed_out = any(code == "capture_timeout" for code, _ in inspector.violations)
                    active = proxy._thread.is_alive()
                elapsed = time.monotonic() - started
            requests = origin.requests
        BOUNDARY_OBSERVATIONS.append({
            "finding": "F2", "elapsed_seconds": round(elapsed, 6),
            "deadline_seconds": LIMITS["run_timeout_seconds"], "capture_timeout_observed": timed_out,
            "active_after_deadline": active, "origin_requests": requests,
            "owned_threads_joined": not proxy._thread.is_alive() and not origin._thread.is_alive(),
        })
        self.assertTrue(timed_out, "absolute_deadline_must_refuse_continuing_connect_header")
        self.assertFalse(active, "capture_must_not_remain_active_after_absolute_deadline")
        self.assertLess(elapsed, LIMITS["run_timeout_seconds"] + 0.5, "deadline_observation_exceeded_scheduler_tolerance")
        self.assertEqual(0, requests)

    def test_missing_expected_run_id_cannot_disable_real_signature_binding(self):
        key, public = test_key()
        now = int(time.time())
        pack = sign_pack(self.clean, self.policy, public_key=public, signer=key.sign, now=now)
        self.refused(
            lambda: verify_pack(pack, self.policy, trusted_public_key=public, now=now, expected_run_id=None),
            "expected_run_id_required",
        )

    def test_host_case_delimiter_escape_disguise_and_private_ids_are_withheld_before_output(self):
        key, public = test_key()
        for index, host in enumerate(encoded_hosts()):
            with self.subTest(control=index):
                report, requests = unknown_connect(host)
                self.assertEqual(0, requests)
                self.assertEqual(1, report["violation_count"])
                self.assertEqual(1, report["hosts_withheld"])
                self.assertTrue(report["hosts"] == [], "untrusted_host_metadata_must_not_enter_inventory")
                self.assertTrue(
                    all(item["host"] is None for item in report["violations"]),
                    "untrusted_host_metadata_must_not_enter_violation",
                )
                raw = validate_report(report, self.policy, now=int(time.time()))
                pack = sign_pack(report, self.policy, public_key=public, signer=key.sign, now=int(time.time()))
                self.assertTrue(not prohibited_present(raw) and not prohibited_present(pack), "prohibited_metadata_survived_scrub")

    def test_source_declared_unknown_host_is_named_but_never_network_allowed(self):
        for host in ("unlisted.synthetic.invalid", "UNLISTED.SYNTHETIC.INVALID"):
            report, requests = unknown_connect(host)
            self.assertEqual(0, requests)
            self.assertEqual(0, report["hosts_withheld"])
            self.assertEqual([{"host": "unlisted.synthetic.invalid", "count": 1}], report["hosts"])
            self.assertEqual("unknown_destination", report["violations"][0]["code"])
            self.assertEqual("unlisted.synthetic.invalid", report["violations"][0]["host"])

    def test_direct_report_inventory_storage_and_real_signed_payload_repeat_host_scrub(self):
        key, public = test_key()
        with tempfile.TemporaryDirectory(prefix="privacy-correction-scrub-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            for index, host in enumerate(encoded_hosts()):
                with self.subTest(control=index):
                    for surface in ("hosts", "violations"):
                        report = copy.deepcopy(self.clean)
                        if surface == "hosts":
                            report["hosts"][0]["host"] = host
                        else:
                            report["violation_count"] = 1
                            report["violations"] = [{"code": "unknown_destination", "host": host, "count": 1}]
                        now = int(time.time())
                        self.refused(lambda: validate_report(report, self.policy, now=now), "untrusted_host_metadata")
                        self.refused(lambda: store.write(report, self.policy, now=now), "untrusted_host_metadata")
                        self.refused(
                            lambda: sign_pack(report, self.policy, public_key=public, signer=key.sign, now=now),
                            "untrusted_host_metadata",
                        )
                        pack = unchecked_signed_pack(report, key, public)
                        self.refused(
                            lambda: verify_pack(
                                pack, self.policy, trusted_public_key=public, now=now, expected_run_id=report["run_id"],
                            ),
                            "untrusted_host_metadata",
                        )
            self.assertFalse(any(FILE_NAME.fullmatch(path.name) for path in store.root.iterdir()))
        inspector = Inspector(self.policy)
        inspector.hosts[encoded_hosts()[0]] = 1
        self.refused(lambda: inspector.report(now=int(time.time())), "untrusted_host_metadata")

    def test_missing_malformed_context_is_refused_before_parse_runtime_or_crypto(self):
        for index, value in enumerate((None, "", 1, True, b"x" * 32, [], {}, "x" * 32, "A" * 32, "0" * 31, "0" * 33)):
            with self.subTest(control=index):
                with mock.patch("privacy_traffic.pack.check_runtime") as runtime:
                    self.refused(
                        lambda: verify_pack(
                            b"not_json", self.policy, trusted_public_key=b"", now=-1, expected_run_id=value,
                        ),
                        "expected_run_id_required",
                    )
                    runtime.assert_not_called()
                self.refused(
                    lambda: validate_report(self.clean, self.policy, now=int(time.time()), expected_run_id=value),
                    "expected_run_id_required",
                )

    def test_valid_wrong_and_expired_context_use_real_ed25519_payload(self):
        key, public = test_key()
        now = int(time.time())
        pack = sign_pack(self.clean, self.policy, public_key=public, signer=key.sign, now=now)
        report = verify_pack(
            pack, self.policy, trusted_public_key=public, now=now, expected_run_id=self.clean["run_id"],
        )
        self.assertIs(report["release_qualified"], False)
        self.refused(
            lambda: verify_pack(pack, self.policy, trusted_public_key=public, now=now, expected_run_id=uuid.uuid4().hex),
            "run_evidence_drift",
        )
        self.refused(
            lambda: verify_pack(
                pack, self.policy, trusted_public_key=public, now=self.clean["expires_at"],
                expected_run_id=self.clean["run_id"],
            ),
            "stale_evidence",
        )

    def test_actual_cli_missing_empty_malformed_wrong_and_valid_run_context(self):
        from test_privacy_traffic import cli

        key, public = test_key()
        pack = sign_pack(self.clean, self.policy, public_key=public, signer=key.sign, now=int(time.time()))
        with tempfile.TemporaryDirectory(prefix="privacy-correction-context-") as temporary:
            root = Path(temporary)
            pack_path, public_path = root / "source-pack.json", root / "public-key.bin"
            pack_path.write_bytes(pack)
            public_path.write_bytes(public)
            common = ["verify-pack", str(pack_path), "--trusted-public-key", str(public_path)]
            for index, arguments in enumerate(([], ["--run-id", ""], ["--run-id", "A" * 32], ["--run-id", SENTINEL])):
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parents[1] / "privacy_traffic_harness.py"),
                     *common, *arguments],
                    capture_output=True, timeout=10,
                )
                with self.subTest(control=index):
                    self.assertEqual(2, result.returncode)
                    self.assertFalse(prohibited_present(result.stdout + result.stderr), "CLI_error_echoed_untrusted_context")
                    self.assertFalse(b"signature_verified" in result.stdout)
            code, result = cli([*common, "--run-id", uuid.uuid4().hex])
            self.assertEqual(2, code)
            self.assertEqual("run_evidence_drift", result["code"])
            code, result = cli([*common, "--run-id", self.clean["run_id"]])
            self.assertEqual(0, code)
            self.assertIs(result["signature_verified"], True)
            self.assertIs(result["release_qualified"], False)

    def test_late_read_completion_and_expired_deadline_cannot_return_data(self):
        class LateChannel:
            reads = 0

            def recv(self, _size):
                self.reads += 1
                time.sleep(0.04)
                return b"CONNECT analytics.synthetic.invalid:443 HTTP/1.1\r\nHost: analytics.synthetic.invalid:443\r\n\r\n"

        channel = LateChannel()
        self.refused(lambda: read_http(channel, deadline=time.monotonic() + 0.01, connect=True), "capture_timeout")
        self.assertEqual(1, channel.reads)
        self.refused(lambda: read_http(channel, deadline=time.monotonic() - 1, connect=True), "capture_timeout")
        self.assertEqual(1, channel.reads)
        self.refused(lambda: remaining_seconds(float("nan")), "invalid_capture_deadline")

    def test_store_exact_64_boundary_no_overwrite_and_safe_expiry_recovery(self):
        with tempfile.TemporaryDirectory(prefix="privacy-correction-exact-cap-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            now = int(time.time())
            for _ in range(64):
                report = copy.deepcopy(self.clean)
                report["run_id"] = uuid.uuid4().hex
                report["created_at"], report["expires_at"] = now, now + 1
                store.write(report, self.policy, now=now)
            self.assertEqual(64, len(list(store.root.glob("privacy-evidence-*.json"))))
            next_report = copy.deepcopy(self.clean)
            next_report["run_id"] = uuid.uuid4().hex
            self.refused(lambda: store.write(next_report, self.policy, now=now), "store_capacity_refused")
            self.assertEqual(64, store.purge_expired(self.policy, now=now + 2))
            store.write(next_report, self.policy, now=now + 2)
            self.refused(lambda: store.write(next_report, self.policy, now=now + 2), "evidence_overwrite_refused")
            self.assertEqual(1, len(list(store.root.glob("privacy-evidence-*.json"))))

    def test_store_foreign_hardlinked_missing_replaced_lock_and_legacy_marker_refuse_without_repair(self):
        for mode in ("content", "hardlink", "missing", "replacement", "directory", "legacy"):
            with self.subTest(control=mode), tempfile.TemporaryDirectory(prefix="privacy-correction-lock-") as temporary:
                store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
                lock = store.root / STORE_LOCK
                if mode == "content":
                    lock.write_bytes(canonical({"format": "foreign_lock", "owner_id": uuid.uuid4().hex}))
                    code = "store_lock_binding_drift"
                elif mode == "hardlink":
                    os.link(lock, Path(temporary) / "lock-alias")
                    code = "output_type_refused"
                elif mode == "missing":
                    lock.unlink()
                    code = "output_inspection_failed"
                elif mode == "replacement":
                    replacement = store.root / "replacement-lock"
                    store._exclusive_write(replacement, lock.read_bytes())
                    os.replace(replacement, lock)
                    code = "store_lock_identity_drift"
                elif mode == "directory":
                    lock.unlink()
                    lock.mkdir()
                    code = "output_type_refused"
                else:
                    (store.root / STORE_MARKER).write_bytes(canonical({
                        "format": "privacy_store_v1", "owner_id": store.owner_id,
                    }))
                    code = "store_owner_mismatch"
                before = sorted(path.name for path in store.root.iterdir())
                self.refused(lambda: store.write(self.clean, self.policy, now=int(time.time())), code)
                self.assertEqual(before, sorted(path.name for path in store.root.iterdir()))

    def test_context_exception_and_real_process_crash_release_kernel_lock(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory(prefix="privacy-correction-crash-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            with self.assertRaises(Refusal):
                with store._locked():
                    raise Refusal("planted_store_exception")
            held, release = context.Event(), context.Event()
            worker = context.Process(target=_crash_lock, args=(str(store.root), store.owner_id, held, release))
            try:
                worker.start()
                self.assertTrue(held.wait(5), "owned_crash_fixture_did_not_acquire_lock")
                pid = worker.pid
                release.set()
                worker.join(5)
                self.assertFalse(worker.is_alive(), "crashed_owned_process_exit_unconfirmed")
                self.assertEqual(17, worker.exitcode)
                store.write(self.clean, self.policy, now=int(time.time()))
                BOUNDARY_OBSERVATIONS.append({
                    "finding": "F4_crash", "owned_pid": pid, "exit_code": worker.exitcode,
                    "kernel_lock_released": True, "next_write_succeeded": True,
                })
            finally:
                release.set()
                if worker.pid is not None:
                    if worker.is_alive():
                        worker.terminate()
                    worker.join(5)
                    self.assertFalse(worker.is_alive(), "owned_crash_fixture_cleanup_unconfirmed")
                    worker.close()

    def test_busy_kernel_lock_has_bounded_refusal_then_normal_recovery(self):
        from privacy_traffic.file_lock import LOCK_WAIT_SECONDS

        with tempfile.TemporaryDirectory(prefix="privacy-correction-busy-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            with store._locked():
                started = time.monotonic()
                self.refused(
                    lambda: store.write(self.clean, self.policy, now=int(time.time())),
                    "store_lock_busy",
                )
                elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, LOCK_WAIT_SECONDS)
            self.assertLess(elapsed, LOCK_WAIT_SECONDS + 1)
            store.write(self.clean, self.policy, now=int(time.time()))

    def test_write_uses_the_validated_snapshot_when_input_changes_during_lock_admission(self):
        with tempfile.TemporaryDirectory(prefix="privacy-correction-snapshot-") as temporary:
            store = EvidenceStore(Path(temporary) / "owned", owner_id=uuid.uuid4().hex, create=True)
            report = copy.deepcopy(self.clean)
            original = report["run_id"]
            real_lock = store._locked

            @contextmanager
            def mutate_input():
                with real_lock():
                    report["run_id"] = "invalid_input_after_validation"
                    yield

            with mock.patch.object(store, "_locked", mutate_input):
                output = store.write(report, self.policy, now=int(time.time()))
            self.assertEqual(f"privacy-evidence-{original}.json", output.name)
            self.assertEqual(original, document(output.read_bytes())["run_id"])

    def test_two_real_store_processes_cannot_admit_65_files(self):
        context = multiprocessing.get_context("spawn")
        owner_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="privacy-correction-capacity-") as temporary:
            root = Path(temporary) / "owned"
            sibling = Path(temporary) / "sibling.txt"
            sibling.write_bytes(b"SYNTHETIC_KEEP")
            store = EvidenceStore(root, owner_id=owner_id, create=True)
            for _ in range(63):
                report = copy.deepcopy(self.clean)
                report["run_id"] = uuid.uuid4().hex
                store.write(report, self.policy, now=int(time.time()))
            entered, release = context.Event(), context.Event()
            results = [context.Queue(), context.Queue()]
            reports = [copy.deepcopy(self.clean), copy.deepcopy(self.clean)]
            for report in reports:
                report["run_id"] = uuid.uuid4().hex
            workers = [
                context.Process(
                    target=_writer,
                    args=(str(root), owner_id, reports[index], entered, release, results[index], index == 0),
                )
                for index in range(2)
            ]
            observations = []
            try:
                workers[0].start()
                self.assertTrue(entered.wait(10), "first_owned_writer_did_not_reach_create")
                self.assertEqual("ready", results[0].get(timeout=3)["stage"])
                workers[1].start()
                self.assertEqual("ready", results[1].get(timeout=5)["stage"])
                try:
                    early = results[1].get(timeout=0.25)
                except queue.Empty:
                    early = None
                release.set()
                observations.append(results[0].get(timeout=10))
                observations.append(early if early is not None else results[1].get(timeout=10))
                for worker in workers:
                    worker.join(10)
                    self.assertFalse(worker.is_alive(), "owned_writer_shutdown_unconfirmed")
                    self.assertEqual(0, worker.exitcode)
                count = len(list(root.glob("privacy-evidence-*.json")))
                sibling_kept = sibling.read_bytes() == b"SYNTHETIC_KEEP"
            finally:
                release.set()
                for worker in workers:
                    if worker.pid is not None:
                        if worker.is_alive():
                            worker.terminate()
                        worker.join(5)
                        self.assertFalse(worker.is_alive(), "owned_writer_cleanup_unconfirmed")
                        worker.close()
                for result in results:
                    result.close()
                    result.join_thread()
            BOUNDARY_OBSERVATIONS.append({
                "finding": "F4", "retained_files": count,
                "results": observations, "sibling_preserved": sibling_kept,
                "kernel_interprocess_serialization_observed": early is None,
                "owned_process_exits_confirmed": True,
            })
            self.assertEqual(64, count, "two_legitimate_writers_must_not_exceed_store_cap")
            self.assertEqual(["store_capacity_refused", "written"], sorted(item["code"] for item in observations))
            self.assertTrue(early is None, "second_process_must_not_pass_admission_while_first_holds_store")
            self.assertTrue(sibling_kept)


if __name__ == "__main__":
    unittest.main()
