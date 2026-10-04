"""Inspect actual bounded wire bytes; retain identifiers and counters, never payload values."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from collections import Counter
from typing import Any
from urllib.parse import unquote

from .policy import DEVICE_ONLY_FIELDS, JOURNEYS, LIMITS, Policy
from .safety import Refusal, canonical, document, require, safe_host


SENTINEL = "SYNTHETIC_RAW_MESSAGE_DO_NOT_EGRESS_ANDROID_16"
VIOLATIONS = frozenset({
    "raw_message_egress", "raw_derived_digest_egress", "device_only_artifact_egress",
    "source_event_origin_unverified", "monetary_analytics", "unknown_destination",
    "undeclared_route", "undeclared_payload_field", "undeclared_payload_schema",
    "payload_value_refused", "opaque_payload", "opaque_response", "malformed_document",
    "duplicate_json_key", "non_finite_json", "document_complexity_refused",
    "document_object_required", "document_size_refused", "capture_timeout",
    "request_budget_exceeded", "capture_budget_exceeded", "header_size_refused",
    "body_size_refused", "malformed_http", "truncated_http", "tls_capture_failed",
    "unexpected_header", "run_binding_drift", "upstream_failed", "unknown_component",
})


def strings(value: Any):
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is str:
            yield item
        elif type(item) is dict:
            stack.extend(item.keys())
            stack.extend(item.values())
        elif type(item) is list:
            stack.extend(item)


class Inspector:
    def __init__(self, policy: Policy):
        require(
            bool(policy.data["destinations"])
            and all(route["synthetic"] and route["host"].endswith(".synthetic.invalid")
                    for route in policy.data["destinations"]),
            "synthetic_capture_profile_required",
        )
        self.policy = policy
        self.run_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.requests = 0
        self.connections = 0
        self.bytes_seen = 0
        self.forwarded = 0
        self.tls_interceptions = 0
        self.hosts: Counter[str] = Counter()
        self.hosts_withheld = 0
        self.fields: dict[str, set[str]] = {}
        self.synthetic_paths: set[str] = set()
        self.violations: Counter[tuple[str, str | None]] = Counter()
        digest = hashlib.sha256(SENTINEL.encode("ascii")).digest()
        self._derivatives = (digest.hex(), digest.hex().upper(), base64.b64encode(digest).decode("ascii"))

    @property
    def deadline(self) -> float:
        return self.started + LIMITS["run_timeout_seconds"]

    def budget(self, size: int = 0) -> None:
        require(time.monotonic() < self.deadline, "capture_timeout")
        self.bytes_seen += size
        require(self.bytes_seen <= LIMITS["max_capture_bytes"], "capture_budget_exceeded")

    def refuse(self, code: str, host: str | None = None) -> None:
        require(code in VIOLATIONS, "unclassified_capture_failure")
        self.violations[(code, self.policy.metadata_host(host))] += 1

    def connection(self) -> None:
        self.budget()
        require(self.connections < LIMITS["max_requests"], "request_budget_exceeded")
        self.connections += 1

    def scan(self, values) -> None:
        for value in values:
            require(SENTINEL not in value, "raw_message_egress")
            require(not any(digest in value for digest in self._derivatives), "raw_derived_digest_egress")

    def admit_host(self, host: str, *, inventory: bool = False) -> None:
        self.budget()
        if inventory:
            metadata = self.policy.metadata_host(host)
            if metadata is None:
                self.hosts_withheld += 1
            else:
                self.hosts[metadata] += 1
        self.scan([host])
        require(
            safe_host(host) is not None and any(route["host"] == host for route in self.policy.data["destinations"]),
            "unknown_destination",
        )

    def inspect(
        self, host: str, method: str, target: str, headers: dict[str, str], body: bytes,
    ) -> tuple[dict[str, Any], bytes]:
        require(self.requests < LIMITS["max_requests"], "request_budget_exceeded")
        self.requests += 1
        self.budget(len(body) + len(canonical(headers)) + len(target))
        self.scan([host, target, unquote(target), *headers.keys(), *headers.values(), body.decode("utf-8", "replace")])
        self.admit_host(host)
        require(method == "POST" and "?" not in target and "#" not in target and "%" not in target, "undeclared_route")
        route = next(
            (route for route in self.policy.data["destinations"] if route["host"] == host and route["path"] == target),
            None,
        )
        require(route is not None, "undeclared_route")
        require(route["component"] in self.policy.data["components"]["release"], "unknown_component")
        require(
            set(headers) == {"host", "content-type", "content-length", "connection", "x-privacy-run"},
            "unexpected_header",
        )
        require(headers["host"] == host and headers["connection"] == "close", "malformed_http")
        require(headers["x-privacy-run"] == self.run_id, "run_binding_drift")
        require(headers["content-type"] == "application/json", "opaque_payload")
        payload = document(body, limit=LIMITS["max_body_bytes"])
        self.scan(strings(payload))
        require(
            not any(key in DEVICE_ONLY_FIELDS or key.endswith("_bidx") for key in payload),
            "device_only_artifact_egress",
        )
        fields = self.policy.payload_check(route["schema"], payload)
        self.fields.setdefault(route["schema"], set()).update(fields)
        self.synthetic_paths.add(route["journey"])
        return route, canonical(payload)

    def response(self, raw: bytes) -> None:
        self.budget(len(raw))
        response = document(raw, limit=LIMITS["max_body_bytes"])
        self.scan(strings(response))
        require(set(response) == {"ok"} and type(response["ok"]) is bool and response["ok"] is True, "opaque_response")

    def report(self, *, now: int, retention_seconds: int = 3600) -> dict[str, Any]:
        require(
            type(retention_seconds) is int and 0 < retention_seconds <= LIMITS["max_retention_seconds"],
            "retention_refused",
        )
        require(
            all(self.policy.metadata_host(host) == host for host in self.hosts)
            and all(host is None or self.policy.metadata_host(host) == host for _, host in self.violations),
            "untrusted_host_metadata",
        )
        violations = [
            {"code": code, "host": host, "count": count}
            for (code, host), count in sorted(self.violations.items(), key=lambda item: (item[0][0], item[0][1] or ""))
        ]
        return {
            "format": "pennilogic_privacy_evidence_v1",
            "kind": "source_synthetic",
            "run_id": self.run_id,
            "policy_sha256": self.policy.sha256,
            "created_at": now,
            "expires_at": now + retention_seconds,
            "request_count": self.requests,
            "connection_count": self.connections,
            "forwarded_count": self.forwarded,
            "tls_interceptions": self.tls_interceptions,
            "capture_bytes": self.bytes_seen,
            "hosts": [{"host": host, "count": count} for host, count in sorted(self.hosts.items())],
            "hosts_withheld": self.hosts_withheld,
            "payload_fields": {schema: sorted(fields) for schema, fields in sorted(self.fields.items())},
            "violation_count": sum(self.violations.values()),
            "violations": violations,
            "observed_synthetic_paths": sorted(self.synthetic_paths),
            "observed_journeys": [],
            "unmet_required_journeys": list(JOURNEYS),
            "release_qualified": False,
        }
