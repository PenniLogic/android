"""Scrub by construction, verify before storage, and delete only owned expired evidence."""

from __future__ import annotations

import ctypes
import os
import re
import stat
import uuid
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from .capture import VIOLATIONS
from .file_lock import exclusive_file
from .policy import JOURNEYS, LIMITS, Policy
from .safety import (
    MAX_DOCUMENT_BYTES, MAX_RETENTION_SECONDS, RUN_ID, SHA256, Refusal, canonical, exact_keys,
    document, expected_run, physical, read_document, require,
)


REPORT_KEYS = {
    "format", "kind", "run_id", "policy_sha256", "created_at", "expires_at",
    "request_count", "connection_count", "forwarded_count", "tls_interceptions", "capture_bytes",
    "hosts", "hosts_withheld", "payload_fields", "violation_count", "violations", "observed_synthetic_paths",
    "observed_journeys", "unmet_required_journeys", "release_qualified",
}
FILE_NAME = re.compile(r"privacy-evidence-([0-9a-f]{32})\.json", re.ASCII)
STORE_MARKER = "privacy-store.json"
STORE_LOCK = "privacy-store.lock"

class _RunBinding(Enum):
    OMITTED = 0


def validate_report(
    report: dict[str, Any], policy: Policy, *, now: int,
    expected_run_id: str | _RunBinding = _RunBinding.OMITTED, allow_expired: bool = False,
) -> bytes:
    if expected_run_id is not _RunBinding.OMITTED:
        expected_run(expected_run_id)
    exact_keys(report, REPORT_KEYS, "unscrubbed_evidence")
    require(
        report["format"] == "pennilogic_privacy_evidence_v1" and report["kind"] == "source_synthetic"
        and report["release_qualified"] is False,
        "evidence_scope_refused",
    )
    require(type(report["run_id"]) is str and RUN_ID.fullmatch(report["run_id"]) is not None, "invalid_evidence_binding")
    require(
        type(report["policy_sha256"]) is str and SHA256.fullmatch(report["policy_sha256"]) is not None
        and report["policy_sha256"] == policy.sha256,
        "policy_evidence_drift",
    )
    if expected_run_id is not _RunBinding.OMITTED:
        require(report["run_id"] == expected_run_id, "run_evidence_drift")
    created, expires = report["created_at"], report["expires_at"]
    require(
        type(now) is int and type(created) is int and type(expires) is int
        and 0 <= created <= now and 0 < expires - created <= MAX_RETENTION_SECONDS,
        "retention_refused",
    )
    if not allow_expired:
        require(created >= now - MAX_RETENTION_SECONDS and now < expires, "stale_evidence")
    for name in ("request_count", "connection_count", "forwarded_count", "tls_interceptions"):
        require(type(report[name]) is int and 0 <= report[name] <= LIMITS["max_requests"], "invalid_evidence_counts")
    require(
        type(report["violation_count"]) is int and 0 <= report["violation_count"] <= LIMITS["max_requests"] + 1,
        "invalid_evidence_counts",
    )
    require(
        type(report["capture_bytes"]) is int and 0 <= report["capture_bytes"] <= LIMITS["max_capture_bytes"]
        and report["forwarded_count"] <= report["request_count"] <= report["tls_interceptions"] <= report["connection_count"],
        "invalid_evidence_counts",
    )
    require(report["request_count"] > 0 or report["violation_count"] > 0, "empty_traffic")
    require(type(report["hosts"]) is list and len(report["hosts"]) <= LIMITS["max_requests"], "unscrubbed_evidence")
    hosts: list[str] = []
    total_hosts = 0
    for item in report["hosts"]:
        exact_keys(item, {"host", "count"}, "unscrubbed_evidence")
        require(
            type(item["host"]) is str and policy.metadata_host(item["host"]) == item["host"]
            and type(item["count"]) is int and 0 < item["count"] <= LIMITS["max_requests"],
            "untrusted_host_metadata",
        )
        hosts.append(item["host"])
        total_hosts += item["count"]
    require(
        type(report["hosts_withheld"]) is int and 0 <= report["hosts_withheld"] <= report["connection_count"]
        and hosts == sorted(set(hosts)) and total_hosts + report["hosts_withheld"] <= report["connection_count"],
        "invalid_evidence_counts",
    )
    require(type(report["payload_fields"]) is dict, "unscrubbed_evidence")
    for schema, fields in report["payload_fields"].items():
        require(type(schema) is str and schema in policy.data["network_schemas"], "unscrubbed_evidence")
        require(
            type(fields) is list and fields == sorted(policy.data["network_schemas"][schema]["fields"]),
            "unscrubbed_evidence",
        )
    require(type(report["violations"]) is list and len(report["violations"]) <= LIMITS["max_requests"] + 1, "unscrubbed_evidence")
    count = 0
    for violation in report["violations"]:
        exact_keys(violation, {"code", "host", "count"}, "unscrubbed_evidence")
        require(
            type(violation["code"]) is str and violation["code"] in VIOLATIONS
            and type(violation["count"]) is int and 0 < violation["count"] <= LIMITS["max_requests"] + 1,
            "unscrubbed_evidence",
        )
        require(
            violation["host"] is None or type(violation["host"]) is str
            and policy.metadata_host(violation["host"]) == violation["host"],
            "untrusted_host_metadata",
        )
        count += violation["count"]
    require(count == report["violation_count"], "invalid_evidence_counts")
    if not count:
        require(
            report["connection_count"] == report["tls_interceptions"] == report["request_count"] == report["forwarded_count"]
            and total_hosts == report["connection_count"] and report["hosts_withheld"] == 0
            and bool(report["payload_fields"]),
            "incomplete_capture_evidence",
        )
    require(
        type(report["observed_synthetic_paths"]) is list
        and all(type(item) is str for item in report["observed_synthetic_paths"])
        and report["observed_synthetic_paths"] == sorted(set(report["observed_synthetic_paths"]))
        and set(report["observed_synthetic_paths"]) <= set(JOURNEYS)
        and report["observed_journeys"] == [] and report["unmet_required_journeys"] == list(JOURNEYS),
        "journey_evidence_refused",
    )
    raw = canonical(report)
    require(len(raw) <= MAX_DOCUMENT_BYTES, "document_size_refused")
    return raw


def _windows_private(path: Path, *, protect_new: bool = False) -> None:
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    descriptor, owner, dacl = pointer(), pointer(), pointer()
    token = wintypes.HANDLE()
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer), pointer,
        ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.GetAclInformation.argtypes = [pointer, pointer, wintypes.DWORD, ctypes.c_int]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    advapi.GetAce.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [pointer]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    def sid_string(sid: pointer) -> str:
        result = wintypes.LPWSTR()
        require(bool(advapi.ConvertSidToStringSidW(sid, ctypes.byref(result))), "output_acl_unverified")
        try:
            return result.value
        finally:
            kernel.LocalFree(ctypes.cast(result, pointer))

    try:
        require(
            advapi.GetNamedSecurityInfoW(
                str(path), 1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor),
            ) == 0 and bool(dacl.value),
            "output_acl_unverified",
        )
        require(bool(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token))), "output_acl_unverified")
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        require(0 < size.value <= 4096, "output_acl_unverified")
        buffer = ctypes.create_string_buffer(size.value)
        require(
            bool(advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size))),
            "output_acl_unverified",
        )
        current = sid_string(ctypes.cast(buffer, ctypes.POINTER(pointer))[0])
        require(sid_string(owner) == current, "output_owner_mismatch")
        if protect_new:
            protected, protected_dacl = pointer(), pointer()
            present, defaulted = wintypes.BOOL(), wintypes.BOOL()
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), pointer,
            ]
            advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
            advapi.GetSecurityDescriptorDacl.argtypes = [
                pointer, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(pointer), ctypes.POINTER(wintypes.BOOL),
            ]
            advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
            advapi.SetNamedSecurityInfoW.argtypes = [
                wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, pointer, pointer, pointer, pointer,
            ]
            advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
            try:
                require(
                    bool(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                        f"D:P(A;OICI;FA;;;{current})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)",
                        1, ctypes.byref(protected), None,
                    )),
                    "output_acl_creation_failed",
                )
                require(
                    bool(advapi.GetSecurityDescriptorDacl(
                        protected, ctypes.byref(present), ctypes.byref(protected_dacl), ctypes.byref(defaulted),
                    )) and bool(present.value),
                    "output_acl_creation_failed",
                )
                require(
                    advapi.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, protected_dacl, None) == 0,
                    "output_acl_creation_failed",
                )
            finally:
                if protected.value:
                    kernel.LocalFree(protected)
            _windows_private(path)
            return
        acl_info = (wintypes.DWORD * 3)()
        require(bool(advapi.GetAclInformation(dacl, acl_info, ctypes.sizeof(acl_info), 2)), "output_acl_unverified")
        require(0 < acl_info[0] <= 64, "output_acl_unverified")
        for index in range(acl_info[0]):
            ace = pointer()
            require(bool(advapi.GetAce(dacl, index, ctypes.byref(ace))), "output_acl_unverified")
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            if ace_type == 1:
                continue
            require(ace_type == 0, "output_acl_unverified")
            principal = sid_string(pointer(ace.value + 8))
            require(principal in {current, "S-1-5-18", "S-1-5-32-544"}, "output_not_private")
    finally:
        if token:
            kernel.CloseHandle(token)
        if descriptor.value:
            kernel.LocalFree(descriptor)


def private_path(path: Path, *, directory: bool) -> None:
    target = physical(path)
    try:
        info = target.stat()
    except OSError:
        raise Refusal("output_inspection_failed") from None
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "output_type_refused")
    if os.name == "nt":
        _windows_private(target)
    else:
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0, "output_not_private")


class EvidenceStore:
    def __init__(self, root: Path, *, owner_id: str, create: bool = False):
        require(type(owner_id) is str and RUN_ID.fullmatch(owner_id) is not None, "store_owner_required")
        self.root = physical(root)
        self.owner_id = owner_id
        self._binding: dict[str, Any] | None = None
        try:
            if create and not self.root.exists():
                self.root.mkdir(mode=0o700)
                if os.name == "nt":
                    _windows_private(self.root, protect_new=True)
            private_path(self.root, directory=True)
            marker = self.root / STORE_MARKER
            if create:
                require(not any(self.root.iterdir()), "store_not_empty")
                lock_id = uuid.uuid4().hex
                lock_path = self.root / STORE_LOCK
                self._exclusive_write(lock_path, canonical({
                    "format": "privacy_store_lock_v1", "owner_id": owner_id, "lock_id": lock_id,
                }))
                identity = lock_path.stat()
                self._exclusive_write(marker, canonical({
                    "format": "privacy_store_v2", "owner_id": owner_id, "lock_id": lock_id,
                    "lock_device": identity.st_dev, "lock_inode": identity.st_ino,
                }))
            self._check_owner()
        except OSError:
            raise Refusal("store_initialization_failed") from None

    def _check_owner(self) -> None:
        marker = self.root / STORE_MARKER
        private_path(marker, directory=False)
        binding = read_document(marker)
        exact_keys(
            binding, {"format", "owner_id", "lock_id", "lock_device", "lock_inode"}, "store_owner_mismatch",
        )
        require(
            binding["format"] == "privacy_store_v2" and binding["owner_id"] == self.owner_id
            and type(binding["lock_id"]) is str and RUN_ID.fullmatch(binding["lock_id"]) is not None
            and type(binding["lock_device"]) is int and binding["lock_device"] >= 0
            and type(binding["lock_inode"]) is int and binding["lock_inode"] > 0
            and (self._binding is None or binding == self._binding),
            "store_owner_mismatch",
        )
        private_path(self.root / STORE_LOCK, directory=False)
        identity = (self.root / STORE_LOCK).stat()
        require(
            (identity.st_dev, identity.st_ino) == (binding["lock_device"], binding["lock_inode"]),
            "store_lock_identity_drift",
        )
        self._binding = binding

    @contextmanager
    def _locked(self) -> Iterator[None]:
        private_path(self.root, directory=True)
        self._check_owner()
        with exclusive_file(self.root / STORE_LOCK) as stream:
            private_path(self.root, directory=True)
            self._check_owner()
            binding = self._binding
            require(binding is not None, "store_owner_mismatch")
            opened = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
                and (opened.st_dev, opened.st_ino) == (binding["lock_device"], binding["lock_inode"]),
                "store_lock_identity_drift",
            )
            require(
                document(stream.read(1025), limit=1024) == {
                    "format": "privacy_store_lock_v1", "owner_id": self.owner_id, "lock_id": binding["lock_id"],
                },
                "store_lock_binding_drift",
            )
            yield
            self._check_owner()

    def _exclusive_write(self, path: Path, raw: bytes) -> None:
        physical(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            opened = os.fstat(stream.fileno())
            private_path(path, directory=False)
            visible = path.stat()
            require(
                stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
                and (opened.st_dev, opened.st_ino) == (visible.st_dev, visible.st_ino),
                "output_identity_drift",
            )
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def write(self, report: dict[str, Any], policy: Policy, *, now: int) -> Path:
        raw = validate_report(report, policy, now=now)
        run_id = document(raw)["run_id"]
        with self._locked():
            self._purge_expired(policy, now=now)
            require(sum(1 for path in self.root.iterdir() if FILE_NAME.fullmatch(path.name)) < 64, "store_capacity_refused")
            path = self.root / f"privacy-evidence-{run_id}.json"
            try:
                self._exclusive_write(path, raw)
            except FileExistsError:
                raise Refusal("evidence_overwrite_refused") from None
            except OSError:
                raise Refusal("evidence_write_failed") from None
        return path

    def purge_expired(self, policy: Policy, *, now: int) -> int:
        with self._locked():
            return self._purge_expired(policy, now=now)

    def _purge_expired(self, policy: Policy, *, now: int) -> int:
        candidates: list[Path] = []
        for path in self.root.iterdir():
            if path.name in (STORE_MARKER, STORE_LOCK):
                continue
            match = FILE_NAME.fullmatch(path.name)
            require(match is not None, "store_foreign_entry")
            private_path(path, directory=False)
            report = read_document(path)
            validate_report(report, policy, now=now, expected_run_id=match[1], allow_expired=True)
            if report["expires_at"] <= now:
                candidates.append(path)
        for path in candidates:
            try:
                path.unlink()
            except OSError:
                raise Refusal("evidence_purge_failed") from None
        return len(candidates)
