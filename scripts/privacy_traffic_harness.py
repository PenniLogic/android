"""T-QA-09 source checks. run-rc refuses unavailable real providers; never promotes probes."""

from __future__ import annotations

import argparse
import json
import sys
import time
import unittest
from pathlib import Path

from privacy_traffic.policy import ROOT, Policy
from privacy_traffic.release import release_refusal
from privacy_traffic.safety import Refusal, canonical, expected_run, read_bytes, read_document


def run_id_argument(value: str) -> str:
    try:
        return expected_run(value)
    except Refusal:
        raise argparse.ArgumentTypeError("expected_run_id_required") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run-rc")
    commands.add_parser("self-test")
    probe = commands.add_parser("source-probe")
    probe.add_argument("scenario", default="clean", nargs="?")
    probe.add_argument("--store", type=Path)
    probe.add_argument("--owner-id")
    components = commands.add_parser("check-components")
    components.add_argument("inventory", type=Path)
    verify = commands.add_parser("verify-pack")
    verify.add_argument("pack", type=Path)
    verify.add_argument("--trusted-public-key", type=Path, required=True)
    verify.add_argument("--run-id", required=True, type=run_id_argument)
    args = parser.parse_args(argv)
    try:
        if args.command == "self-test":
            suite = unittest.defaultTestLoader.discover(str(ROOT / "scripts" / "tests"), pattern="test_privacy_traffic*.py")
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            module = sys.modules.get("test_privacy_traffic")
            boundaries = sys.modules.get("test_privacy_traffic_boundaries")
            print(canonical({
                "scope": "source_self_test",
                "tests": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
                "capture_runs": getattr(module, "CAPTURE_RUNS", []),
                "boundary_observations": getattr(boundaries, "BOUNDARY_OBSERVATIONS", []),
                "release_qualified": False,
                "approved_release_signer_present": False,
            }).decode("ascii"))
            return 0 if result.wasSuccessful() and result.testsRun > 0 else 1
        policy = Policy.load()
        if args.command == "run-rc":
            print(canonical(release_refusal(policy)).decode("ascii"))
            return 2
        if args.command == "check-components":
            policy.component_check(read_document(args.inventory))
            print(json.dumps({"scope": "source", "component_inventory_matches": True, "release_qualified": False}))
            return 0
        if args.command == "source-probe":
            from privacy_traffic.probe import run_probe
            from privacy_traffic.probe import synthetic_policy
            from privacy_traffic.evidence import EvidenceStore
            if (args.store is None) != (args.owner_id is None):
                raise Refusal("store_owner_required")
            result = run_probe(args.scenario)
            if args.store is not None:
                store = EvidenceStore(args.store, owner_id=args.owner_id, create=not args.store.exists())
                store.write(result["evidence"], synthetic_policy(), now=int(time.time()))
            print(canonical(result).decode("ascii"))
            return 0 if result["source_assertions_passed"] else 1
        if args.command == "verify-pack":
            from privacy_traffic.pack import PACK_LIMIT, verify_pack
            from privacy_traffic.probe import synthetic_policy
            verify_pack(
                read_bytes(args.pack, limit=PACK_LIMIT), synthetic_policy(),
                trusted_public_key=read_bytes(args.trusted_public_key, limit=32),
                now=int(time.time()), expected_run_id=args.run_id,
            )
            print(json.dumps({"signature_verified": True, "release_qualified": False}))
            return 0
        raise Refusal("unknown_harness_command")
    except Refusal as error:
        print(json.dumps({"code": error.code, "release_qualified": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
