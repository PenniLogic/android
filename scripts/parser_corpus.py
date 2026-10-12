"""Synthetic corpus v1: closed-catalog validation, coverage and a parser adapter seam.

No production parser is supplied. Reports contain identifiers and counts, never
rendered input, expected money, parser output or exception text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from privacy_traffic.safety import (
    Refusal,
    canonical,
    document,
    exact_keys,
    physical,
    read_document,
    require,
    snapshot_document,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = ROOT / "testdata" / "parser_corpus"
CORPUS_PATH = CORPUS_ROOT / "v1" / "corpus.json"
SCHEMA_PATH = CORPUS_ROOT / "v1" / "schema.json"
SCHEMA_VERSION = 1
CORPUS_VERSION = "1.0.0"
SCHEMA_SHA256 = "66cae7fa6f63de9fc93aecc76cf7c015beaf23ec6b4b6f3d7775ef11d3168f9e"
PROVENANCE = {
    "origin": "fresh_synthetic",
    "builder": "closed_catalog_v1",
    "review": "required_before_publication",
}
RULES = {
    ("synthetic_bank_alpha", "account_movement", "1.0.0"): "superseded",
    ("synthetic_bank_alpha", "account_movement", "2.0.0"): "active",
    ("synthetic_bank_alpha", "cash_withdrawal", "1.0.0"): "active",
    ("synthetic_bank_alpha", "reversal", "1.0.0"): "active",
    ("synthetic_wallet_beta", "wallet_movement", "1.0.0"): "active",
}
AMOUNTS = {"small": 12345, "fractional": 507, "round": 9900, "grouped": 12345678}
IDENTITIES = {
    "alpha": ("<SYNTH-ACCOUNT-A>", "<SYNTH-PARTY-A>", "<SYNTH-REFERENCE-A>"),
    "beta": ("<SYNTH-ACCOUNT-B>", "<SYNTH-PARTY-B>", "<SYNTH-REFERENCE-B>"),
}
SENDERS = {
    "synthetic_bank_alpha": "<SYNTH-BANK-ALPHA>",
    "synthetic_wallet_beta": "<SYNTH-WALLET-BETA>",
}
# These are invented fixture builders, not bank templates or production parser rules.
FORMATS = {
    "bank_debit_v1": "SYNTHETIC: A/c {account} debited INR {amount} at {party}. Ref {reference}.",
    "bank_credit_v1": "SYNTHETIC: INR {amount} credited to A/c {account} from {party}. Ref {reference}.",
    "bank_debit_v2": "SYNTHETIC: INR {grouped} spent using A/c {account}; payee {party}; ref {reference}.",
    "bank_credit_v2": "SYNTHETIC: A/c {account} received INR {amount}; payer {party}; ref {reference}.",
    "bank_atm": "SYNTHETIC: Cash withdrawal INR {amount} from A/c {account} at {party}. Ref {reference}.",
    "bank_reversal": "SYNTHETIC: INR {amount} reversed and credited to A/c {account}. Ref {reference}.",
    "bank_failed": "SYNTHETIC: Payment INR {amount} from A/c {account} failed. No debit. Ref {reference}.",
    "bank_promotion": "SYNTHETIC: Offer for {party}: spend INR {amount} to earn rewards. No transaction.",
    "bank_balance": "SYNTHETIC: Balance enquiry for A/c {account}: INR {amount}. No transaction.",
    "bank_otp": "SYNTHETIC: OTP <SYNTH-OTP> for INR {amount} at {party}. Do not share.",
    "bank_malformed": "SYNTHETIC: A/c {account} debited INR 12.3.4 at {party}. Ref {reference}.",
    "bank_missing_amount": "SYNTHETIC: A/c {account} debited at {party}. Ref {reference}.",
    "wallet_debit": "SYNTHETIC: Paid INR {amount} to {party} from wallet {account}. Ref {reference}.",
    "wallet_credit": "SYNTHETIC: Wallet {account} received INR {amount} from {party}. Ref {reference}.",
    "wallet_failed": "SYNTHETIC: Wallet payment INR {amount} to {party} failed. Ref {reference}.",
    "wallet_promotion": "SYNTHETIC: Wallet offer: add INR {amount} for a reward. No transaction.",
    "wallet_untrusted": "SYNTHETIC: Paid INR {amount} to {party} from wallet {account}. Ref {reference}.",
}
RESULT_KEYS = {"classification", "direction", "amount_minor", "currency", "reason"}
REASONS = {
    "parsed", "failed_transaction", "promotion", "balance_enquiry", "otp",
    "malformed_amount", "missing_amount", "untrusted_sender",
}


class SafeArguments(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("invalid_arguments")


@dataclass(frozen=True, repr=False)
class SyntheticInput:
    issuer_pattern: str
    rule_id: str
    rule_version: str
    channel: str
    sender: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class ParseResult:
    classification: str
    direction: str | None
    amount_minor: int | None
    currency: str | None
    reason: str


@dataclass(frozen=True, repr=False)
class Case:
    id: str
    rule: tuple[str, str, str]
    polarity: str
    input: SyntheticInput
    expected: ParseResult


@dataclass(frozen=True, repr=False)
class Corpus:
    cases: tuple[Case, ...]
    sha256: str
    source_snapshot: bytes = field(default=b"", repr=False)


def parse_result(value: object, *, golden: bool = False) -> ParseResult:
    value = read_snapshot(value)
    exact_keys(value, RESULT_KEYS, "parse_result_shape")
    require(value["classification"] in ("transaction", "non_transactional", "unmatched"), "classification_invalid")
    require(type(value["reason"]) is str and value["reason"] in REASONS, "reason_invalid")
    if value["classification"] == "transaction":
        require(value["direction"] in ("debit", "credit"), "direction_invalid")
        amount = value["amount_minor"]
        require(type(amount) is int and 0 < amount <= 9223372036854775807, "money_not_positive_int64")
        require(value["currency"] == "INR" and value["reason"] == "parsed", "transaction_result_invalid")
        if golden:
            require(amount in AMOUNTS.values(), "money_outside_synthetic_catalog")
    else:
        require(
            value["direction"] is None and value["amount_minor"] is None and value["currency"] is None,
            "non_transaction_has_money",
        )
        require(value["reason"] != "parsed", "non_transaction_reason_invalid")
    return ParseResult(**value)


def _grouped_amount(minor: int) -> str:
    whole, fraction = divmod(minor, 100)
    digits = str(whole)
    groups = [digits[-3:]]
    remaining = digits[:-3]
    while remaining:
        groups.insert(0, remaining[-2:])
        remaining = remaining[:-2]
    return ",".join(groups) + f".{fraction:02d}"


def _input(value: object, rule: tuple[str, str, str]) -> SyntheticInput:
    exact_keys(value, {"format", "channel", "amount", "identity"}, "input_shape")
    for key, catalog in (("format", FORMATS), ("amount", AMOUNTS), ("identity", IDENTITIES)):
        require(type(value[key]) is str and value[key] in catalog, "input_outside_synthetic_catalog")
    require(value["channel"] in ("sms", "notification"), "channel_invalid")
    family = "bank_" if rule[0] == "synthetic_bank_alpha" else "wallet_"
    require(value["format"].startswith(family), "issuer_format_mismatch")
    account, party, reference = IDENTITIES[value["identity"]]
    minor = AMOUNTS[value["amount"]]
    text = FORMATS[value["format"]].format(
        account=account, party=party, reference=reference,
        amount=f"{minor // 100}.{minor % 100:02d}", grouped=_grouped_amount(minor),
    )
    sender = "<SYNTH-UNTRUSTED>" if value["format"] == "wallet_untrusted" else SENDERS[rule[0]]
    return SyntheticInput(*rule, value["channel"], sender, text)


def validate(value: object) -> Corpus:
    owned = read_snapshot(value)
    exact_keys(owned, {"schema_version", "corpus_version", "provenance", "rules", "cases"}, "corpus_shape")
    require(type(owned["schema_version"]) is int and owned["schema_version"] == SCHEMA_VERSION, "schema_version_invalid")
    require(owned["corpus_version"] == CORPUS_VERSION, "corpus_version_invalid")
    require(owned["provenance"] == PROVENANCE, "provenance_declaration_invalid")
    require(type(owned["rules"]) is list and len(owned["rules"]) == len(RULES), "rule_inventory_invalid")
    declared = set()
    for rule in owned["rules"]:
        exact_keys(rule, {"issuer_pattern", "rule_id", "rule_version", "lifecycle"}, "rule_shape")
        require(all(type(item) is str for item in rule.values()), "rule_value_invalid")
        key = (rule["issuer_pattern"], rule["rule_id"], rule["rule_version"])
        require(key in RULES and key not in declared and rule["lifecycle"] == RULES[key], "rule_inventory_invalid")
        declared.add(key)
    require(type(owned["cases"]) is list and 1 <= len(owned["cases"]) <= 64, "case_count_invalid")
    cases: list[Case] = []
    ids: set[str] = set()
    coverage: Counter = Counter()
    for item in owned["cases"]:
        exact_keys(
            item, {"id", "issuer_pattern", "rule_id", "rule_version", "polarity", "input", "expected"},
            "case_shape",
        )
        case_id = item["id"]
        require(
            type(case_id) is str and len(case_id) == 8 and case_id.startswith("syn_")
            and all("0" <= char <= "9" for char in case_id[4:]) and case_id not in ids,
            "case_identifier_invalid",
        )
        require(
            all(type(item[key]) is str for key in ("issuer_pattern", "rule_id", "rule_version")),
            "case_rule_invalid",
        )
        rule = (item["issuer_pattern"], item["rule_id"], item["rule_version"])
        require(rule in declared, "case_rule_invalid")
        require(item["polarity"] in ("positive", "negative"), "polarity_invalid")
        expected = parse_result(item["expected"], golden=True)
        require(
            (item["polarity"] == "positive") == (expected.classification == "transaction"),
            "polarity_result_mismatch",
        )
        rendered = _input(item["input"], rule)
        if expected.classification == "transaction":
            require(expected.amount_minor == AMOUNTS[item["input"]["amount"]], "expected_amount_mismatch")
        cases.append(Case(case_id, rule, item["polarity"], rendered, expected))
        ids.add(case_id)
        coverage[(rule, item["polarity"])] += 1
    require(
        all(coverage[(rule, polarity)] > 0 for rule in RULES for polarity in ("positive", "negative")),
        "rule_positive_or_negative_missing",
    )
    source_snapshot = canonical(owned)
    return Corpus(tuple(cases), hashlib.sha256(source_snapshot).hexdigest(), source_snapshot)


def read_snapshot(value: object) -> dict:
    # Reuse the repository's bounded JSON/snapshot boundary, including strict built-in types.
    return document(snapshot_document(value))


def load(path: Path = CORPUS_PATH) -> Corpus:
    return validate(read_document(path))


def check_repository_corpus() -> Corpus:
    """Refuse sidecars/extra versions instead of accidentally leaving an unscanned fixture."""
    try:
        require({path.name for path in physical(CORPUS_ROOT).iterdir()} == {"v1"}, "corpus_inventory_invalid")
        version = physical(CORPUS_ROOT / "v1")
        require({path.name for path in version.iterdir()} == {"corpus.json", "schema.json"}, "corpus_inventory_invalid")
    except OSError:
        raise Refusal("corpus_inventory_unreadable") from None
    schema = read_document(version / "schema.json")
    require(
        hashlib.sha256(canonical(schema)).hexdigest() == SCHEMA_SHA256,
        "corpus_schema_allowlist_mismatch",
    )
    return load(version / "corpus.json")


def _case_snapshot(case: Case) -> bytes:
    require(
        type(case) is Case and type(case.rule) is tuple and len(case.rule) == 3
        and type(case.input) is SyntheticInput and type(case.expected) is ParseResult,
        "corpus_case_invalid",
    )
    return snapshot_document({
        **vars(case), "rule": list(case.rule),
        "input": vars(case.input), "expected": vars(case.expected),
    })


def _admit_corpus(corpus: Corpus) -> Corpus:
    require(
        type(corpus) is Corpus and type(corpus.cases) is tuple and type(corpus.sha256) is str,
        "corpus_snapshot_invalid",
    )
    validated = validate(document(corpus.source_snapshot))
    require(
        corpus.sha256 == validated.sha256 and len(corpus.cases) == len(validated.cases),
        "corpus_snapshot_mismatch",
    )
    # Canonical snapshots distinguish integer money from equal-valued floats.
    require(
        all(_case_snapshot(actual) == _case_snapshot(expected)
            for actual, expected in zip(corpus.cases, validated.cases)),
        "corpus_snapshot_mismatch",
    )
    return validated


def coverage_report(corpus: Corpus) -> dict:
    corpus = _admit_corpus(corpus)
    counts = Counter((case.rule, case.polarity) for case in corpus.cases)
    return {
        "scope": "synthetic_corpus_structure",
        "schema_version": SCHEMA_VERSION,
        "corpus_version": CORPUS_VERSION,
        "corpus_sha256": corpus.sha256,
        "corpus_size": len(corpus.cases),
        "rules": [
            {
                "issuer_pattern": rule[0], "rule_id": rule[1], "rule_version": rule[2],
                "lifecycle": RULES[rule],
                "positive": counts[(rule, "positive")], "negative": counts[(rule, "negative")],
            }
            for rule in sorted(RULES)
        ],
        "privacy_check": "closed_catalog_and_shape_only",
        "real_world_provenance_proven": False,
        "production_parser_executed": False,
        "release_qualified": False,
    }


ParserAdapter = Callable[[SyntheticInput], object]


def run_corpus(corpus: Corpus, adapter: ParserAdapter | None = None) -> dict:
    """Run every case, with no quarantine, retry, expected-output fallback or payload diagnostics.

    An adapter returns the five-field result object or raises Refusal. It must
    translate its own runtime errors to Refusal without carrying payload text.
    This seam does not attest that an adapter is the accepted production parser.
    """
    corpus = _admit_corpus(corpus)
    require(adapter is None or callable(adapter), "parser_adapter_invalid")
    outcomes = []
    for case in corpus.cases:
        if adapter is None:
            status, code = "blocked", "parser_adapter_missing"
        else:
            try:
                actual = parse_result(adapter(case.input))
                status, code = ("passed", "matched") if actual == case.expected else ("failed", "parse_mismatch")
            except Refusal:
                status, code = "failed", "parser_adapter_or_result_refused"
        outcomes.append({"case_id": case.id, "status": status, "code": code})
    counts = Counter(item["status"] for item in outcomes)
    return {
        **coverage_report(corpus),
        "scope": "parser_adapter_contract",
        "cases": outcomes,
        "passed": counts["passed"], "failed": counts["failed"], "blocked": counts["blocked"],
        "all_cases_passed": len(outcomes) > 0 and counts["passed"] == len(outcomes),
        "adapter_identity_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        parser = SafeArguments(description=__doc__)
        parser.add_argument("command", choices=("check", "run-parser"))
        args = parser.parse_args(argv)
        corpus = check_repository_corpus()
        report = coverage_report(corpus) if args.command == "check" else run_corpus(corpus)
        print(canonical(report).decode("ascii"))
        return 0 if args.command == "check" else 2
    except Refusal as error:
        print(json.dumps({"code": error.code, "release_qualified": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
