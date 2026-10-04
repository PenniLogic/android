"""Real process/TLS negative controls. The fixture routes are never product journeys."""

from __future__ import annotations

import copy
import hashlib
import os
import time
from typing import Any

from .capture import SENTINEL, Inspector
from .evidence import validate_report
from .policy import JOURNEYS, ROOT, Policy
from .proxy import InterceptProxy, SyntheticOrigin, synthetic_request
from .safety import canonical, exact_keys, read_document, require
from .tls import MemoryCA


FIXTURE = ROOT / "scripts" / "tests" / "fixtures" / "privacy_traffic" / "synthetic-network.json"
SCENARIOS = (
    "clean", "raw", "raw_escaped", "raw_header", "raw_url", "money_field", "money_value",
    "unknown_host", "opaque", "bidx", "raw_digest_field", "raw_digest_value", "source_event_id",
    "missing_path", "oversized",
)


def synthetic_policy() -> Policy:
    base = Policy.load()
    fixture = read_document(FIXTURE)
    exact_keys(fixture, {"component", "destinations", "network_schemas"}, "invalid_synthetic_fixture")
    data = copy.deepcopy(base.data)
    data["destinations"] = fixture["destinations"]
    data["network_schemas"] = fixture["network_schemas"]
    for variant in ("debug", "release"):
        data["components"][variant] = sorted([*data["components"][variant], fixture["component"]])
    return Policy(data)


def run_probe(scenario: str) -> dict[str, Any]:
    started = time.monotonic()
    require(scenario in SCENARIOS, "unknown_source_scenario")
    policy = synthetic_policy()
    inspector = Inspector(policy)
    ca = MemoryCA()
    responses: list[int] = []
    with SyntheticOrigin(ca) as origin:
        with InterceptProxy(inspector, ca, origin) as proxy:
            for stage in JOURNEYS:
                if scenario == "missing_path" and stage == "clarification":
                    continue
                payload: dict[str, Any] = {"event": "synthetic_probe", "stage": stage, "outcome": "ok", "synthetic": True}
                host, path = "analytics.synthetic.invalid", f"/probe/{stage}"
                content_type, extras, declared_body_bytes = "application/json", None, None
                if stage == "analytics":
                    if scenario in ("raw", "raw_escaped"):
                        payload["outcome"] = SENTINEL
                    elif scenario == "raw_header":
                        extras = {"X-Synthetic-Prohibited": SENTINEL}
                    elif scenario == "raw_url":
                        path += "?" + SENTINEL
                    elif scenario == "money_field":
                        payload["amount_minor"], payload["currency"] = 12345, "USD"
                    elif scenario == "money_value":
                        payload["outcome"] = 12345
                    elif scenario == "unknown_host":
                        host = "unlisted.synthetic.invalid"
                    elif scenario == "bidx":
                        payload["merchant_bidx"] = "SYNTHETIC_BLIND_INDEX_VALUE"
                    elif scenario == "raw_digest_field":
                        payload["raw_hash"] = hashlib.sha256(SENTINEL.encode("ascii")).hexdigest()
                    elif scenario == "raw_digest_value":
                        payload["outcome"] = hashlib.sha256(SENTINEL.encode("ascii")).hexdigest()
                    elif scenario == "source_event_id":
                        payload["source_event_id"] = "synthetic_opaque_id"
                body = canonical(payload)
                if stage == "analytics" and scenario == "raw_escaped":
                    body = body.replace(SENTINEL.encode("ascii"), "".join(f"\\u{ord(char):04x}" for char in SENTINEL).encode("ascii"))
                elif stage == "analytics" and scenario == "opaque":
                    body, content_type = b"SYNTHETIC_OPAQUE_BYTES", "application/octet-stream"
                elif stage == "analytics" and scenario == "oversized":
                    body, declared_body_bytes = b"", 65_537
                responses.append(synthetic_request(
                    proxy, host, path, body, content_type=content_type, extra_headers=extras,
                    declared_body_bytes=declared_body_bytes,
                ))
        require(not proxy.failures and not origin.failures, "synthetic_service_failed")
    report = inspector.report(now=int(time.time()))
    validate_report(report, policy, now=int(time.time()), expected_run_id=inspector.run_id)
    missing = sorted(set(JOURNEYS) - inspector.synthetic_paths)
    source_passed = (
        not report["violation_count"] and not missing and responses == [200, 200, 200]
        and origin.requests == inspector.forwarded == 3 and inspector.tls_interceptions == 3
    )
    return {
        "scope": "source_synthetic",
        "scenario": scenario,
        "capture_process_id": os.getpid(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "source_assertions_passed": source_passed,
        "missing_synthetic_paths": missing,
        "origin_requests": origin.requests,
        "proxy_shutdown_confirmed": True,
        "evidence": report,
        "release_qualified": False,
    }
