"""Domain-separated Ed25519 signatures, not claims of protected producer provenance."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Callable
from typing import Any

from .evidence import validate_report
from .policy import Policy
from .safety import Refusal, canonical, document, exact_keys, expected_run, require
from .tls import check_runtime


DOMAIN = b"PenniLogic Android T-QA-09 scrubbed evidence v1\x00"
PACK_LIMIT = 524_288


def sign_pack(
    report: dict[str, Any], policy: Policy, *, public_key: bytes,
    signer: Callable[[bytes], bytes] | None, now: int,
) -> bytes:
    check_runtime()
    require(signer is not None, "evidence_signer_missing")
    require(type(public_key) is bytes and len(public_key) == 32, "signer_key_refused")
    payload = validate_report(report, policy, now=now)
    snapshot = document(payload)
    signature = signer(DOMAIN + payload)
    require(type(signature) is bytes and len(signature) == 64, "signer_result_refused")
    raw = canonical({
        "format": "pennilogic_privacy_signed_pack_v1",
        "payload": snapshot,
        "signature": {
            "algorithm": "ed25519",
            "key_sha256": hashlib.sha256(public_key).hexdigest(),
            "value": base64.b64encode(signature).decode("ascii"),
        },
    })
    require(len(raw) <= PACK_LIMIT, "document_size_refused")
    verify_pack(raw, policy, trusted_public_key=public_key, now=now, expected_run_id=snapshot["run_id"])
    return raw


def verify_pack(
    raw: bytes, policy: Policy, *, trusted_public_key: bytes, now: int,
    expected_run_id: str,
) -> dict[str, Any]:
    expected_run(expected_run_id)
    check_runtime()
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    require(type(trusted_public_key) is bytes and len(trusted_public_key) == 32, "trusted_signer_required")
    pack = document(raw, limit=PACK_LIMIT)
    exact_keys(pack, {"format", "payload", "signature"}, "invalid_signed_pack")
    require(pack["format"] == "pennilogic_privacy_signed_pack_v1", "invalid_signed_pack")
    exact_keys(pack["signature"], {"algorithm", "key_sha256", "value"}, "invalid_signed_pack")
    signature = pack["signature"]
    require(
        signature["algorithm"] == "ed25519"
        and signature["key_sha256"] == hashlib.sha256(trusted_public_key).hexdigest()
        and type(signature["value"]) is str and len(signature["value"]) == 88,
        "untrusted_signature",
    )
    try:
        value = base64.b64decode(signature["value"], validate=True)
    except (ValueError, binascii.Error):
        raise Refusal("invalid_signature") from None
    require(len(value) == 64, "invalid_signature")
    payload = validate_report(pack["payload"], policy, now=now, expected_run_id=expected_run_id)
    try:
        Ed25519PublicKey.from_public_bytes(trusted_public_key).verify(value, DOMAIN + payload)
    except InvalidSignature:
        raise Refusal("invalid_signature") from None
    return document(payload)
