"""Read the same packaged policy used by Android's actual structured-event logger."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .safety import IDENTIFIER, Refusal, canonical, document, exact_keys, read_bytes, require, safe_host


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "app" / "src" / "main" / "res" / "raw" / "privacy_traffic_policy.json"
JOURNEYS = ("ingestion", "clarification", "analytics")
COMPONENT = re.compile(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.+-]+", re.ASCII)
MONEY_FIELD = re.compile(
    r"(?:amount|amount_minor|balance|currency|money|monetary|price|subtotal|total|value|revenue|cost)",
    re.ASCII | re.IGNORECASE,
)
DEVICE_ONLY_FIELDS = frozenset({
    "raw_event_digest", "raw_hash", "message_digest", "message_hash", "raw_message_hash",
    "raw_message_digest",
})
LIMITS = {
    "max_requests": 64,
    "max_body_bytes": 65_536,
    "max_header_bytes": 8192,
    "max_capture_bytes": 1_048_576,
    "run_timeout_seconds": 20,
    "max_retention_seconds": 86_400,
}


def validate_rule(rule: Any) -> None:
    require(type(rule) is dict and type(rule.get("kind")) is str, "invalid_field_rule")
    kind = rule["kind"]
    keys = {
        "enum": {"kind", "values", "nullable"},
        "integer": {"kind", "minimum", "maximum", "nullable"},
        "boolean": {"kind", "nullable"},
        "pattern": {"kind", "pattern", "max_length", "nullable"},
        "array": {"kind", "items", "max_items", "nullable"},
    }
    require(kind in keys, "invalid_field_rule")
    exact_keys(rule, keys[kind], "invalid_field_rule")
    require(type(rule["nullable"]) is bool, "invalid_field_rule")
    if kind == "enum":
        require(
            type(rule["values"]) is list and 0 < len(rule["values"]) <= 64
            and all(type(item) is str and 0 < len(item) <= 96 and item.isascii() for item in rule["values"])
            and len(set(rule["values"])) == len(rule["values"]),
            "invalid_field_rule",
        )
    elif kind == "integer":
        require(
            type(rule["minimum"]) is int and type(rule["maximum"]) is int
            and -2_147_483_648 <= rule["minimum"] <= rule["maximum"] <= 2_147_483_647,
            "invalid_field_rule",
        )
    elif kind == "pattern":
        require(
            type(rule["pattern"]) is str and 0 < len(rule["pattern"]) <= 96
            and type(rule["max_length"]) is int and 0 < rule["max_length"] <= 96,
            "invalid_field_rule",
        )
        # Only fixed source-authored patterns, never patterns taken from captured traffic.
        try:
            re.compile(rule["pattern"], re.ASCII)
        except re.error:
            raise Refusal("invalid_field_rule") from None
    elif kind == "array":
        require(type(rule["max_items"]) is int and 0 < rule["max_items"] <= 16, "invalid_field_rule")
        validate_rule(rule["items"])


def accepts(value: Any, rule: dict[str, Any]) -> bool:
    if value is None:
        return rule["nullable"]
    kind = rule["kind"]
    if kind == "enum":
        return type(value) is str and value in rule["values"]
    if kind == "integer":
        return type(value) is int and rule["minimum"] <= value <= rule["maximum"]
    if kind == "boolean":
        return type(value) is bool
    if kind == "pattern":
        return (
            type(value) is str and len(value) <= rule["max_length"] and value.isascii()
            and re.fullmatch(rule["pattern"], value, re.ASCII) is not None
        )
    if kind == "array":
        return (
            type(value) is list and len(value) <= rule["max_items"]
            and all(accepts(item, rule["items"]) for item in value)
        )
    return False


class Policy:
    def __init__(self, data: dict[str, Any]):
        exact_keys(
            data,
            {"schema_version", "required_journeys", "limits", "components", "destinations",
             "network_schemas", "journey_providers", "local_events", "diagnostic_hosts"},
            "invalid_policy",
        )
        require(type(data["schema_version"]) is int and data["schema_version"] == 1, "invalid_policy")
        require(data["required_journeys"] == list(JOURNEYS), "required_journey_drift")
        require(data["limits"] == LIMITS, "capture_budget_drift")
        exact_keys(data["components"], {"debug", "release"}, "invalid_component_registry")
        for entries in data["components"].values():
            require(
                type(entries) is list and len(entries) <= 256
                and all(type(item) is str and COMPONENT.fullmatch(item) for item in entries)
                and entries == sorted(set(entries)),
                "invalid_component_registry",
            )
        for key in ("local_events", "network_schemas", "journey_providers"):
            require(type(data[key]) is dict, "invalid_policy")
        require(not set(data["local_events"]) & set(data["network_schemas"]), "payload_schema_overlap")
        require(
            all(schema.get("category") == "local_log" for schema in data["local_events"].values()
                if type(schema) is dict)
            and all(schema.get("category") in ("api", "analytics") for schema in data["network_schemas"].values()
                    if type(schema) is dict),
            "payload_category_drift",
        )
        require(set(data["journey_providers"]) <= set(JOURNEYS), "invalid_journey_provider")
        for name, schema in {**data["local_events"], **data["network_schemas"]}.items():
            require(type(name) is str and IDENTIFIER.fullmatch(name) is not None, "invalid_payload_schema")
            exact_keys(schema, {"category", "fields"}, "invalid_payload_schema")
            require(schema["category"] in ("local_log", "analytics", "api"), "invalid_payload_schema")
            require(type(schema["fields"]) is dict and 0 < len(schema["fields"]) <= 32, "invalid_payload_schema")
            for field, rule in schema["fields"].items():
                require(type(field) is str and IDENTIFIER.fullmatch(field) is not None, "invalid_payload_schema")
                validate_rule(rule)
                if schema["category"] != "local_log":
                    require(
                        field not in DEVICE_ONLY_FIELDS and not field.endswith("_bidx")
                        and field != "source_event_id",
                        "device_only_schema_refused",
                    )
                if schema["category"] == "analytics":
                    require(
                        not MONEY_FIELD.fullmatch(field) and not field.endswith("_bidx")
                        and rule["kind"] in ("enum", "boolean"),
                        "unsafe_analytics_schema",
                    )
        require(type(data["destinations"]) is list and len(data["destinations"]) <= 32, "invalid_destination_registry")
        require(
            type(data["diagnostic_hosts"]) is list and len(data["diagnostic_hosts"]) <= 32
            and all(safe_host(host) is not None for host in data["diagnostic_hosts"])
            and data["diagnostic_hosts"] == sorted(set(data["diagnostic_hosts"])),
            "invalid_host_metadata_registry",
        )
        seen: set[tuple[str, str]] = set()
        for route in data["destinations"]:
            exact_keys(route, {"host", "component", "path", "schema", "journey", "synthetic"}, "invalid_route")
            require(safe_host(route["host"]) is not None, "invalid_route")
            require(type(route["component"]) is str and COMPONENT.fullmatch(route["component"]) is not None, "invalid_route")
            require(
                type(route["path"]) is str and re.fullmatch(r"/[a-z/_]{1,64}", route["path"]) is not None
                and type(route["schema"]) is str and route["schema"] in data["network_schemas"]
                and type(route["journey"]) is str and route["journey"] in JOURNEYS
                and type(route["synthetic"]) is bool,
                "invalid_route",
            )
            require((route["host"], route["path"]) not in seen, "duplicate_route")
            require(
                route["component"] in data["components"]["release"],
                "undeclared_component",
            )
            require(
                route["journey"] != "analytics" or data["network_schemas"][route["schema"]]["category"] == "analytics",
                "analytics_route_drift",
            )
            seen.add((route["host"], route["path"]))
        self.data = data
        self._metadata_hosts = frozenset(
            [*data["diagnostic_hosts"], *(route["host"] for route in data["destinations"])],
        )
        self.sha256 = hashlib.sha256(canonical(data)).hexdigest()

    def metadata_host(self, value: Any) -> str | None:
        if type(value) is str and len(value) <= 253 and value.isascii():
            candidate = value.lower()
            if candidate in self._metadata_hosts:
                return candidate
        return None

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> Policy:
        raw = read_bytes(path)
        policy = cls(document(raw))
        policy.sha256 = hashlib.sha256(raw).hexdigest()
        return policy

    def component_check(self, inventory: dict[str, Any]) -> None:
        exact_keys(inventory, {"schema_version", "variants"}, "invalid_component_inventory")
        require(type(inventory["schema_version"]) is int and inventory["schema_version"] == 1, "invalid_component_inventory")
        require(inventory["variants"] == self.data["components"], "component_inventory_drift")

    def payload_check(self, schema_name: str, payload: dict[str, Any], *, local: bool = False) -> list[str]:
        schemas = self.data["local_events" if local else "network_schemas"]
        require(type(schema_name) is str and schema_name in schemas, "undeclared_payload_schema")
        require(type(payload) is dict and all(type(key) is str for key in payload), "document_object_required")
        schema = schemas[schema_name]
        if not local:
            require(
                not any(key in DEVICE_ONLY_FIELDS or key.endswith("_bidx") for key in payload),
                "device_only_artifact_egress",
            )
            require("source_event_id" not in payload, "source_event_origin_unverified")
        if schema["category"] == "analytics":
            require(
                not any(MONEY_FIELD.fullmatch(key) or key.endswith("_bidx") for key in payload)
                and not any(type(value) in (int, float) for value in payload.values()),
                "monetary_analytics",
            )
        exact_keys(payload, set(schema["fields"]), "undeclared_payload_field")
        for key, rule in schema["fields"].items():
            require(accepts(payload[key], rule), "payload_value_refused")
        return sorted(payload)

    def release_missing(self) -> list[str]:
        missing = [
            f"journey_{name}_provider" for name in JOURNEYS
            if name not in self.data["journey_providers"]
        ]
        if not any(schema["category"] == "analytics" for schema in self.data["network_schemas"].values()):
            missing.append("analytics_instrumentation_provider")
        if not any(not route["synthetic"] for route in self.data["destinations"]):
            missing.append("release_destination_inventory")
        return missing
