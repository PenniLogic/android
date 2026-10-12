"""T-QA-12 matrix definitions and source-only emulator/managed/physical runner seams.

No device discovery, provisioning, settings changes, subprocesses or retries occur
here. Passing injected adapter fixtures never establishes runtime qualification.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from parser_corpus import SafeArguments, check_repository_corpus, read_snapshot
from privacy_traffic.safety import Refusal, canonical, exact_keys, read_document, require

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "docs" / "testing" / "android-compatibility-matrix.json"
SCHEMA_PATH = ROOT / "docs" / "testing" / "android-compatibility-matrix.schema.json"
SCHEMA_SHA256 = "f33e569393d9999cdee5d679f16a3af186d46d5214e069bb1b5b8a95a412c497"
STRATEGY = {
    "version": "1.1.1",
    "commit": "1722fc79ab2abee15d3adc2ed2f3baaad4bd6ed4",
    "oldest_api": 31,
    "current_api": 36,
}
STAGES = ("pull_request", "release_candidate")
BASE_SUBSETS = ("parser_corpus", "permission_journeys", "restricted_settings")
# Exact source requirements, not detected hardware. API 35 retains the accepted platform baseline.
LANES = {
    "emulator_api_31": ("emulator", 31, "default_emulator", "reference_emulator", "default", BASE_SUBSETS, STAGES),
    "emulator_api_33": ("emulator", 33, "default_emulator", "reference_emulator", "default", BASE_SUBSETS, ("release_candidate",)),
    "emulator_api_35": ("emulator", 35, "default_emulator", "reference_emulator", "default", BASE_SUBSETS, ("release_candidate",)),
    "emulator_current": ("emulator", 36, "default_emulator", "reference_emulator", "default", BASE_SUBSETS, STAGES),
    "emulator_large_text": ("emulator", 36, "default_emulator", "reference_emulator", "largest_text_display_reduced_motion", (*BASE_SUBSETS, "large_text"), STAGES),
    "emulator_talkback": ("emulator", 36, "default_emulator", "reference_emulator", "talkback_enabled", (*BASE_SUBSETS, "talkback"), ("release_candidate",)),
    "managed_low_memory": ("managed_device", None, "minimum_supported_low_memory", None, "profile_selection_pending", ("parser_corpus", "permission_journeys", "background_capture_recovery", "cold_start", "timeline_10000", "offline_queue"), ("release_candidate",)),
    "managed_oem_variant_a": ("managed_device", None, "oem_managed", None, "aggressive_background_restrictions", ("parser_corpus", "permission_journeys", "background_capture_recovery"), ("release_candidate",)),
    "managed_oem_variant_b": ("managed_device", None, "oem_managed", None, "aggressive_background_restrictions", ("parser_corpus", "permission_journeys", "background_capture_recovery"), ("release_candidate",)),
    "physical_mid_range_indian_sim": ("physical", None, "mid_range", None, "indian_sim_synthetic_account_wipe_required", ("parser_corpus", "permission_journeys", "physical_sms_smoke", "cold_start", "timeline_10000"), ("release_candidate",)),
}
NON_QUARANTINABLE = {
    "parser_corpus", "permission_journeys", "restricted_settings",
    "background_capture_recovery", "physical_sms_smoke", "offline_queue",
}


@dataclass(frozen=True)
class Lane:
    id: str
    kind: str
    os_api: int | None
    memory_class: str
    oem_variant: str | None
    configuration: str
    test_subsets: tuple[str, ...]
    required_for: tuple[str, ...]


@dataclass(frozen=True)
class Quarantine:
    lane_id: str
    test_subset: str
    owner_issue: int
    opened_on: date
    expires_on: date


@dataclass(frozen=True)
class Matrix:
    lanes: tuple[Lane, ...]
    quarantines: tuple[Quarantine, ...]


@dataclass(frozen=True)
class RunnerRequest:
    lane: Lane
    test_subset: str
    corpus_sha256: str


@dataclass(frozen=True)
class AdapterResult:
    status: str
    tests_run: int
    skipped: int = 0


Runner = Callable[[RunnerRequest], AdapterResult]


def _date(value: object) -> date:
    require(type(value) is str and len(value) == 10, "quarantine_date_invalid")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise Refusal("quarantine_date_invalid") from None
    require(result.isoformat() == value, "quarantine_date_invalid")
    return result


def validate(value: object, *, today: date | None = None) -> Matrix:
    owned = read_snapshot(value)
    exact_keys(owned, {"schema_version", "matrix_version", "strategy", "scaffold", "lanes", "quarantines"}, "matrix_shape")
    require(type(owned["schema_version"]) is int and owned["schema_version"] == 1, "matrix_schema_version_invalid")
    exact_keys(owned["strategy"], set(STRATEGY), "matrix_strategy_drift")
    require(
        type(owned["strategy"]["oldest_api"]) is int and type(owned["strategy"]["current_api"]) is int,
        "matrix_strategy_drift",
    )
    require(owned["matrix_version"] == "1.0.0" and owned["strategy"] == STRATEGY, "matrix_strategy_drift")
    exact_keys(owned["scaffold"], {"min_api", "minimum_is_provisional"}, "matrix_scaffold_drift")
    require(
        type(owned["scaffold"]["min_api"]) is int
        and owned["scaffold"]["minimum_is_provisional"] is True
        and owned["scaffold"]["min_api"] == 26,
        "matrix_scaffold_drift",
    )
    require(type(owned["lanes"]) is list and len(owned["lanes"]) == len(LANES), "matrix_lane_inventory_invalid")
    lanes = []
    ids = set()
    for item in owned["lanes"]:
        exact_keys(
            item,
            {"id", "kind", "os_api", "memory_class", "ram_mib", "oem_variant", "configuration",
             "test_subsets", "required_for", "result"},
            "matrix_lane_shape",
        )
        require(type(item["id"]) is str and item["id"] in LANES and item["id"] not in ids, "matrix_lane_identifier_invalid")
        require(type(item["os_api"]) in (int, type(None)) and item["ram_mib"] is None, "matrix_device_selection_unaccepted")
        require(
            type(item["test_subsets"]) is list and type(item["required_for"]) is list,
            "matrix_subset_shape",
        )
        actual = (
            item["kind"], item["os_api"], item["memory_class"], item["oem_variant"], item["configuration"],
            tuple(item["test_subsets"]), tuple(item["required_for"]),
        )
        require(actual == LANES[item["id"]], "matrix_required_lane_drift")
        require(item["result"] == "not_run", "definition_cannot_claim_execution")
        lanes.append(Lane(item["id"], *actual))
        ids.add(item["id"])
    require(type(owned["quarantines"]) is list and len(owned["quarantines"]) <= 64, "quarantine_inventory_invalid")
    quarantines = []
    quarantined = set()
    now = today if today is not None else datetime.now(timezone.utc).date()
    for item in owned["quarantines"]:
        exact_keys(item, {"lane_id", "test_subset", "owner_issue", "opened_on", "expires_on"}, "quarantine_shape")
        require(type(item["lane_id"]) is str and item["lane_id"] in ids, "quarantine_lane_invalid")
        require(type(item["test_subset"]) is str and item["test_subset"] in LANES[item["lane_id"]][5], "quarantine_subset_invalid")
        require(item["test_subset"] not in NON_QUARANTINABLE, "critical_subset_cannot_be_quarantined")
        require(type(item["owner_issue"]) is int and 0 < item["owner_issue"] <= 2147483647, "quarantine_owner_required")
        opened, expiry = _date(item["opened_on"]), _date(item["expires_on"])
        require(opened <= now < expiry and 0 < (expiry - opened).days <= 14, "quarantine_expired_or_invalid")
        key = (item["lane_id"], item["test_subset"])
        require(key not in quarantined, "quarantine_duplicate")
        quarantines.append(Quarantine(*key, item["owner_issue"], opened, expiry))
        quarantined.add(key)
    return Matrix(tuple(lanes), tuple(quarantines))


def load() -> Matrix:
    schema = read_document(SCHEMA_PATH)
    require(
        hashlib.sha256(canonical(schema)).hexdigest() == SCHEMA_SHA256,
        "matrix_schema_allowlist_mismatch",
    )
    matrix = validate(read_document(MATRIX_PATH))
    platform = read_document(ROOT / "docs" / "platform" / "android-behavior-matrix.json")
    require(
        platform.get("target_api") == STRATEGY["current_api"]
        and platform.get("min_api") == 26
        and platform.get("api_levels") == [31, 33, 35, 36],
        "accepted_platform_baseline_drift",
    )
    return matrix


def _admit_matrix(matrix: Matrix, *, today: date | None = None) -> Matrix:
    require(
        type(matrix) is Matrix and type(matrix.lanes) is tuple and len(matrix.lanes) == len(LANES),
        "matrix_lane_inventory_invalid",
    )
    require(
        all(type(lane) is Lane and type(lane.test_subsets) is tuple and type(lane.required_for) is tuple
            for lane in matrix.lanes),
        "matrix_lane_shape",
    )
    require(
        type(matrix.quarantines) is tuple and len(matrix.quarantines) <= 64,
        "quarantine_inventory_invalid",
    )
    for item in matrix.quarantines:
        require(type(item) is Quarantine, "quarantine_shape")
        require(type(item.opened_on) is date and type(item.expires_on) is date, "quarantine_date_invalid")
    return validate({
        "schema_version": 1, "matrix_version": "1.0.0", "strategy": STRATEGY,
        "scaffold": {"min_api": 26, "minimum_is_provisional": True},
        "lanes": [
            {
                **vars(lane), "test_subsets": list(lane.test_subsets), "required_for": list(lane.required_for),
                "ram_mib": None, "result": "not_run",
            }
            for lane in matrix.lanes
        ],
        "quarantines": [
            {
                **vars(item), "opened_on": item.opened_on.isoformat(), "expires_on": item.expires_on.isoformat(),
            }
            for item in matrix.quarantines
        ],
    }, today=today)


def select(matrix: Matrix, stage: str, *, today: date | None = None) -> tuple[Lane, ...]:
    require(stage in STAGES, "matrix_stage_invalid")
    matrix = _admit_matrix(matrix, today=today)
    lanes = tuple(lane for lane in matrix.lanes if stage in lane.required_for)
    require(len(lanes) > 0, "required_matrix_empty")
    return lanes


def plan(matrix: Matrix, stage: str, *, today: date | None = None) -> dict:
    selected = select(matrix, stage, today=today)
    selected_ids = {lane.id for lane in selected}
    return {
        "scope": "compatibility_plan",
        "schema_version": 1,
        "matrix_version": "1.0.0",
        "strategy_version": STRATEGY["version"],
        "stage": stage,
        "lanes": [
            {
                "lane_id": lane.id, "kind": lane.kind, "os_api": lane.os_api,
                "memory_class": lane.memory_class, "ram_mib": None, "oem_variant": lane.oem_variant,
                "configuration": lane.configuration, "test_subsets": list(lane.test_subsets),
                "result": "not_run",
            }
            for lane in selected
        ],
        "quarantines": [
            {
                "lane_id": item.lane_id, "test_subset": item.test_subset, "owner_issue": item.owner_issue,
                "opened_on": item.opened_on.isoformat(), "expires_on": item.expires_on.isoformat(),
            }
            for item in matrix.quarantines if item.lane_id in selected_ids
        ],
        "minimum_supported_api_confirmed": False,
        "runtime_qualified": False,
        "release_qualified": False,
    }


def _adapter_result(value: object) -> AdapterResult:
    require(type(value) is AdapterResult, "adapter_result_invalid")
    require(type(value.status) is str and value.status in ("passed", "failed", "blocked"), "adapter_result_invalid")
    require(
        type(value.tests_run) is int and type(value.skipped) is int
        and 0 <= value.skipped <= value.tests_run <= 1000000,
        "adapter_counts_invalid",
    )
    require(value.status != "passed" or value.tests_run > 0 and value.skipped == 0, "empty_or_skipped_subset")
    return value


def run_required(
    matrix: Matrix,
    stage: str,
    *,
    runners: Mapping[str, Runner] | None = None,
    today: date | None = None,
) -> dict:
    """Injection seam for source tests; shipping emulator/managed/physical bindings are absent.

    Native adapters must eventually bind the exact device, configuration, commit,
    complete subset and newly executed evidence. Naked source results are not that
    binding. Refusal is the adapter's payload-free failure contract.
    """
    corpus = check_repository_corpus()
    selected = select(matrix, stage, today=today)
    quarantine = {(item.lane_id, item.test_subset): item for item in matrix.quarantines}
    adapters = {} if runners is None else runners
    require(set(adapters) <= {"emulator", "managed_device", "physical"}, "runner_kind_invalid")
    require(all(callable(adapter) for adapter in adapters.values()), "runner_not_callable")
    results = []
    for lane in selected:
        for subset in lane.test_subsets:
            status, code, count, skipped = "blocked", "runner_binding_missing", 0, 0
            if lane.os_api is None or lane.oem_variant is None:
                code = "device_profile_selection_pending"
            elif lane.kind in adapters:
                try:
                    outcome = _adapter_result(adapters[lane.kind](RunnerRequest(lane, subset, corpus.sha256)))
                    status, code, count, skipped = outcome.status, "source_adapter_result", outcome.tests_run, outcome.skipped
                except Refusal:
                    status, code = "failed", "adapter_or_result_refused"
            entry = {
                "lane_id": lane.id, "os_api": lane.os_api, "memory_class": lane.memory_class,
                "oem_variant": lane.oem_variant, "test_subset": subset, "status": status,
                "code": code, "tests_run": count, "skipped": skipped,
                "evidence_scope": "source_adapter" if code == "source_adapter_result" else "not_qualified",
            }
            held = quarantine.get((lane.id, subset))
            if held is not None:
                entry["quarantine"] = {
                    "owner_issue": held.owner_issue, "opened_on": held.opened_on.isoformat(),
                    "expires_on": held.expires_on.isoformat(),
                }
            results.append(entry)
    counts = Counter(entry["status"] for entry in results)

    def grouped(dimension: str) -> list[dict]:
        groups = {}
        for entry in results:
            label = entry[dimension]
            group = groups.setdefault(label, {"value": label, "required": 0, "passed": 0, "quarantined": 0})
            group["required"] += 1
            group["passed"] += entry["status"] == "passed" and "quarantine" not in entry
            group["quarantined"] += "quarantine" in entry
        return [
            {**group, "pass_rate_basis_points": group["passed"] * 10000 // group["required"]}
            for _, group in sorted(groups.items(), key=lambda item: str(item[0]))
        ]

    return {
        "scope": "compatibility_runner_contract" if runners is not None else "compatibility_readiness",
        "schema_version": 1, "matrix_version": "1.0.0", "strategy_version": STRATEGY["version"],
        "stage": stage, "corpus_sha256": corpus.sha256, "results": results,
        "required": len(results), "passed": counts["passed"], "failed": counts["failed"], "blocked": counts["blocked"],
        "quarantined": sum("quarantine" in entry for entry in results),
        "by_os": grouped("os_api"), "by_memory_class": grouped("memory_class"), "by_oem_variant": grouped("oem_variant"),
        "all_adapter_checks_passed": counts["passed"] == len(results) and not any("quarantine" in entry for entry in results),
        "required_runtime_subset_passed": False,
        "runtime_qualified": False,
        "release_qualified": False,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeArguments(description=__doc__)
        parser.add_argument("command", choices=("check", "plan", "run-required"))
        parser.add_argument("--stage", choices=STAGES, default="pull_request")
        args = parser.parse_args(argv)
        matrix = load()
        if args.command == "run-required":
            print(canonical(run_required(matrix, args.stage)).decode("ascii"))
            return 2
        report = plan(matrix, args.stage)
        report["scope"] = "compatibility_definitions" if args.command == "check" else "compatibility_plan"
        print(canonical(report).decode("ascii"))
        return 0
    except Refusal as error:
        print(json.dumps({"code": error.code, "release_qualified": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
