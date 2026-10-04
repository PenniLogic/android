"""Unmet RC providers are hard failures, including during the one-RC reporting rollout."""

from __future__ import annotations

from dataclasses import dataclass

from .policy import Policy
from .safety import IDENTIFIER, MAX_RETENTION_SECONDS, require


RC_PROVIDER_OWNERS = {
    "proxy_runner": "infra",
    "isolated_synthetic_account": "release_qa",
    "isolated_emulator": "release_qa",
    "release_candidate_apk": "android_release",
    "approved_evidence_signer": "release_security",
    "protected_producer_attestation": "infra_22",
    "release_artifact_publication": "coordinator",
    "approved_rollout_record": "coordinator",
}


def release_refusal(policy: Policy) -> dict:
    return {
        "code": "rc_inputs_unavailable",
        "missing": sorted([*policy.release_missing(), *RC_PROVIDER_OWNERS]),
        "provider_owners": RC_PROVIDER_OWNERS,
        "release_qualified": False,
    }


@dataclass(frozen=True)
class ReportingWindow:
    mode: str
    rc_id: str
    recorded_at: int
    expires_at: int

    def __post_init__(self):
        require(self.mode in ("first_rc_reporting", "rollback_reporting"), "reporting_mode_refused")
        require(type(self.rc_id) is str and IDENTIFIER.fullmatch(self.rc_id) is not None, "reporting_rc_refused")
        require(
            type(self.recorded_at) is int and type(self.expires_at) is int
            and 0 <= self.recorded_at < self.expires_at
            and self.expires_at - self.recorded_at <= MAX_RETENTION_SECONDS,
            "reporting_expiry_required",
        )


def privacy_exit(
    *, violation_count: int, rc_id: str, now: int, evidence_ready: bool,
    providers_ready: bool, verified_window: ReportingWindow | None = None,
) -> int:
    """The caller must verify the owner-authorized rollout record and its one-RC history first."""
    require(type(violation_count) is int and violation_count >= 0, "invalid_violation_count")
    require(
        type(evidence_ready) is bool and type(providers_ready) is bool
        and type(now) is int and now >= 0
        and type(rc_id) is str and IDENTIFIER.fullmatch(rc_id) is not None
        and (verified_window is None or isinstance(verified_window, ReportingWindow)),
        "invalid_rollout_input",
    )
    if not evidence_ready or not providers_ready:
        return 2
    if not violation_count:
        return 0
    if (
        verified_window is not None and verified_window.rc_id == rc_id
        and verified_window.recorded_at <= now < verified_window.expires_at
    ):
        return 0
    return 1
