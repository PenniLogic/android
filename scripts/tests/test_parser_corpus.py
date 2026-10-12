"""Source contract tests using only freshly invented corpus and identifier plants."""

from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parser_corpus as corpus
from privacy_traffic.safety import Refusal, canonical, document, read_document


def fixture_probe(value: corpus.SyntheticInput) -> dict:
    """Tiny interpreter for the invented test grammar, not an Android/bank parser."""
    text = value.text
    reason = None
    if value.sender == "<SYNTH-UNTRUSTED>":
        reason = "untrusted_sender"
    elif "failed" in text:
        reason = "failed_transaction"
    elif "Offer" in text or "offer" in text:
        reason = "promotion"
    elif "Balance enquiry" in text:
        reason = "balance_enquiry"
    elif "OTP" in text:
        reason = "otp"
    elif "12.3.4" in text:
        reason = "malformed_amount"
    elif "INR" not in text:
        reason = "missing_amount"
    if reason is not None:
        return {
            "classification": "unmatched" if reason in ("malformed_amount", "missing_amount", "untrusted_sender") else "non_transactional",
            "direction": None, "amount_minor": None, "currency": None, "reason": reason,
        }
    match = re.search(r"INR ([0-9,]+)\.([0-9]{2})(?: |;)", text)
    if match is None:
        raise Refusal("fixture_probe_shape")
    minor = int(match[1].replace(",", "")) * 100 + int(match[2])
    debit = any(word in text for word in ("debited", "spent", "Cash withdrawal", "Paid"))
    return {
        "classification": "transaction", "direction": "debit" if debit else "credit",
        "amount_minor": minor, "currency": "INR", "reason": "parsed",
    }


class CorpusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.value = read_document(corpus.CORPUS_PATH)

    def refuse(self, value: object) -> None:
        with self.assertRaises(Refusal) as caught:
            corpus.validate(value)
        self.assertRegex(str(caught.exception), r"^[a-z][a-z0-9_]*$")

    def test_complete_committed_corpus_and_coverage(self) -> None:
        result = corpus.check_repository_corpus()
        report = corpus.coverage_report(result)
        self.assertEqual(19, report["corpus_size"])
        self.assertEqual(5, len(report["rules"]))
        self.assertEqual(8, sum(row["positive"] for row in report["rules"]))
        self.assertEqual(11, sum(row["negative"] for row in report["rules"]))
        self.assertEqual(2, len({row["issuer_pattern"] for row in report["rules"]}))
        self.assertTrue(all(row["positive"] > 0 and row["negative"] > 0 for row in report["rules"]))
        self.assertTrue(any(row["lifecycle"] == "superseded" for row in report["rules"]))
        self.assertFalse(report["real_world_provenance_proven"])
        self.assertFalse(report["production_parser_executed"])
        self.assertFalse(report["release_qualified"])
        self.assertEqual({f"syn_{index:04d}" for index in range(1, 20)}, {case.id for case in result.cases})

    def test_schema_keys_and_catalogs_match_the_validator(self) -> None:
        schema = read_document(corpus.SCHEMA_PATH)
        self.assertEqual(set(self.value), set(schema["required"]))
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertIs(schema["additionalProperties"], False)
        definitions = schema["$defs"]
        for name, example in (
            ("rule", self.value["rules"][0]), ("case", self.value["cases"][0]),
            ("input", self.value["cases"][0]["input"]), ("expected", self.value["cases"][0]["expected"]),
        ):
            with self.subTest(definition=name):
                definition = definitions[name]
                self.assertEqual(set(example), set(definition["required"]))
                self.assertEqual(set(example), set(definition["properties"]))
                self.assertIs(definition["additionalProperties"], False)
        fields = definitions["input"]["properties"]
        self.assertEqual(set(corpus.FORMATS), set(fields["format"]["enum"]))
        self.assertEqual(set(corpus.IDENTITIES), set(fields["identity"]["enum"]))
        self.assertEqual(set(corpus.AMOUNTS), set(fields["amount"]["enum"]))
        self.assertEqual(set(corpus.SENDERS), set(definitions["issuer"]["enum"]))
        self.assertEqual({rule[1] for rule in corpus.RULES}, set(definitions["rule_id"]["enum"]))
        self.assertEqual({rule[2] for rule in corpus.RULES}, set(definitions["rule_version"]["enum"]))
        expected = definitions["expected"]["properties"]
        self.assertEqual(set(corpus.AMOUNTS.values()) | {None}, set(expected["amount_minor"]["enum"]))
        self.assertEqual(corpus.REASONS, set(expected["reason"]["enum"]))
        self.assertEqual(corpus.SCHEMA_VERSION, schema["properties"]["schema_version"]["const"])
        self.assertEqual(corpus.CORPUS_VERSION, schema["properties"]["corpus_version"]["const"])

    def test_generated_inputs_have_reserved_synthetic_markers_and_no_payload_repr(self) -> None:
        validated = corpus.validate(self.value)
        for case in validated.cases:
            self.assertTrue(case.input.text.startswith("SYNTHETIC: "))
            self.assertTrue(case.input.sender.startswith("<SYNTH-"))
            self.assertFalse(case.input.text in repr(case))
            self.assertFalse(case.input.text in repr(case.input))
            self.assertFalse("12345" in repr(case.expected))
        self.assertTrue("1,23,456.78" in validated.cases[4].input.text)

    def test_snapshot_is_not_changed_by_caller_edits(self) -> None:
        validated = corpus.validate(self.value)
        digest = validated.sha256
        self.value["cases"][0]["expected"]["amount_minor"] = 999
        self.value["cases"].clear()
        self.assertEqual(19, len(validated.cases))
        self.assertEqual(digest, validated.sha256)
        self.assertTrue(corpus.run_corpus(validated, fixture_probe)["all_cases_passed"])

    def test_planted_identifiers_cannot_enter_any_free_text_slot(self) -> None:
        plants = (
            (("cases", 0, "input", "identity"), "SYNTH_PLANTED_NAME_NOT_A_PERSON"),
            (("cases", 0, "input", "amount"), "987654.32"),
            (("cases", 0, "input", "format"), "SYNTH_PLANTED_ACCOUNT_9999000011112222"),
            (("cases", 0, "input", "channel"), "SYNTH_PLANTED_MESSAGE_IDENTIFIER"),
            (("cases", 0, "id"), "SYNTH_PLANTED_MESSAGE_IDENTIFIER"),
            (("cases", 0, "issuer_pattern"), "SYNTH_PLANTED_ISSUER"),
            (("cases", 0, "rule_id"), "SYNTH_PLANTED_NAME_NOT_A_PERSON"),
            (("cases", 0, "rule_version"), "SYNTH_PLANTED_ACCOUNT_9999000011112222"),
            (("cases", 0, "expected", "reason"), "SYNTH_PLANTED_MESSAGE_IDENTIFIER"),
            (("cases", 0, "expected", "currency"), "SYNTH_PLANTED_NAME_NOT_A_PERSON"),
            (("provenance", "origin"), "pseudonymised_production_export"),
        )
        for index, (path, plant) in enumerate(plants):
            with self.subTest(plant=index):
                value = copy.deepcopy(self.value)
                location = value
                for key in path[:-1]:
                    location = location[key]
                location[path[-1]] = plant
                self.refuse(value)

    def test_extra_fields_are_refused_at_every_object_boundary(self) -> None:
        paths = ((), ("provenance",), ("rules", 0), ("cases", 0), ("cases", 0, "input"), ("cases", 0, "expected"))
        for index, path in enumerate(paths):
            with self.subTest(boundary=index):
                value = copy.deepcopy(self.value)
                location = value
                for key in path:
                    location = location[key]
                location["SYNTH_PLANTED_SECRET"] = "SYNTH_PLANTED_MESSAGE"
                self.refuse(value)

    def test_schema_cannot_be_used_as_an_unscanned_payload_sidecar(self) -> None:
        real_read = corpus.read_document

        def planted(path: Path) -> dict:
            value = real_read(path)
            if path == corpus.SCHEMA_PATH:
                value["examples"] = ["SYNTH_PLANTED_MESSAGE"]
            return value

        with mock.patch.object(corpus, "read_document", side_effect=planted):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(1, corpus.main(["check"]))
            self.assertEqual("", output.getvalue())
            self.assertFalse("SYNTH_PLANTED_MESSAGE" in error.getvalue())
            self.assertEqual("corpus_schema_allowlist_mismatch", json.loads(error.getvalue())["code"])

    def test_invalid_money_shapes_fail_without_rounding(self) -> None:
        for amount in (True, False, 12345.0, "12345", -1, 0, 9223372036854775808, 98765432, None, [], {}):
            with self.subTest(kind=type(amount).__name__):
                value = copy.deepcopy(self.value)
                value["cases"][0]["expected"]["amount_minor"] = amount
                self.refuse(value)
        for field, value in (("currency", "USD"), ("direction", None), ("reason", "promotion")):
            changed = copy.deepcopy(self.value)
            changed["cases"][0]["expected"][field] = value
            self.refuse(changed)

    def test_money_cannot_be_attached_to_a_negative(self) -> None:
        for field, value in (("amount_minor", 507), ("currency", "INR"), ("direction", "credit"), ("reason", "parsed")):
            changed = copy.deepcopy(self.value)
            changed["cases"][2]["expected"][field] = value
            self.refuse(changed)

    def test_rule_case_inventory_cannot_silently_disappear(self) -> None:
        changes = (
            lambda value: value["rules"].pop(),
            lambda value: value["rules"].append(value["rules"][0]),
            lambda value: value["rules"].__setitem__(1, value["rules"][0]),
            lambda value: value["cases"].clear(),
            lambda value: value["cases"].__setitem__(1, value["cases"][0]),
            lambda value: value.__setitem__("cases", [case for case in value["cases"] if case["rule_id"] != "reversal"]),
            lambda value: value.__setitem__("cases", [case for case in value["cases"] if case["polarity"] != "negative"]),
            lambda value: value["cases"][0].__setitem__("rule_version", "3.0.0"),
            lambda value: value["cases"][0].__setitem__("polarity", "negative"),
            lambda value: value["cases"][0]["input"].__setitem__("format", "wallet_debit"),
            lambda value: value["cases"][0]["input"].__setitem__("amount", "round"),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                value = copy.deepcopy(self.value)
                change(value)
                self.refuse(value)

    def test_malformed_types_and_versions_fail_closed(self) -> None:
        for field, value in (("schema_version", True), ("schema_version", 1.0), ("schema_version", 2), ("corpus_version", "2.0.0"), ("rules", {}), ("cases", {})):
            changed = copy.deepcopy(self.value)
            changed[field] = value
            self.refuse(changed)
        for field in ("id", "issuer_pattern", "rule_id", "rule_version", "polarity", "input", "expected"):
            for bad in (None, [], {}):
                changed = copy.deepcopy(self.value)
                changed["cases"][0][field] = bad
                self.refuse(changed)

    def test_bounded_json_refuses_duplicate_keys_encodings_and_nonfinite_numbers(self) -> None:
        for raw in (
            b'{"SYNTH_PLANT":1,"SYNTH_PLANT":2}', b'\xef\xbb\xbf{}', b'{"x":NaN}',
            b'{"x":Infinity}', b'{"x":1e999}', b'{"x":', b'{}' * 131073,
            '{"x":1}'.encode("utf-16"),
        ):
            with self.subTest(size=len(raw)):
                with self.assertRaises(Refusal):
                    corpus.validate(document(raw))

    def test_entire_corpus_runs_against_test_grammar_only(self) -> None:
        attempts = []

        def probe(value: corpus.SyntheticInput) -> dict:
            attempts.append(value)
            return fixture_probe(value)

        validated = corpus.validate(self.value)
        report = corpus.run_corpus(validated, probe)
        self.assertTrue(report["all_cases_passed"])
        self.assertEqual(19, len(attempts))
        self.assertEqual(19, report["passed"])
        self.assertFalse(report["production_parser_executed"])
        self.assertFalse(report["adapter_identity_verified"])
        self.assertFalse(report["release_qualified"])
        encoded = canonical({key: value for key, value in report.items() if key != "corpus_sha256"})
        self.assertTrue(all(case.input.text.encode() not in encoded for case in validated.cases))
        self.assertTrue(all(str(amount).encode() not in encoded for amount in corpus.AMOUNTS.values()))

    def test_one_parser_regression_fails_but_does_not_skip_later_cases_or_retry(self) -> None:
        calls = 0

        def regressed(value: corpus.SyntheticInput) -> dict:
            nonlocal calls
            calls += 1
            parsed = fixture_probe(value)
            if calls == 1:
                parsed["amount_minor"] += 1
            return parsed

        report = corpus.run_corpus(corpus.validate(self.value), regressed)
        self.assertEqual(19, calls)
        self.assertEqual(18, report["passed"])
        self.assertEqual(1, report["failed"])
        self.assertFalse(report["all_cases_passed"])
        self.assertEqual("parse_mismatch", report["cases"][0]["code"])

    def test_missing_invalid_and_refused_adapters_never_pass_or_echo_content(self) -> None:
        validated = corpus.validate(self.value)
        with self.assertRaisesRegex(Refusal, "^parser_adapter_invalid$"):
            corpus.run_corpus(validated, "SYNTH_PLANTED_ADAPTER")
        missing = corpus.run_corpus(validated)
        self.assertEqual(19, missing["blocked"])
        self.assertFalse(missing["all_cases_passed"])
        for output in (None, {"SYNTH_PLANTED_ACCOUNT": "SYNTH_PLANTED_NAME"}, [], "SYNTH_PLANTED_MESSAGE"):
            report = corpus.run_corpus(validated, lambda _: output)
            self.assertEqual(19, report["failed"])
            self.assertFalse(b"SYNTH_PLANTED" in canonical(report))

        def refused(_: corpus.SyntheticInput) -> dict:
            raise Refusal("synthetic_planted_sensitive_exception")

        report = corpus.run_corpus(validated, refused)
        self.assertEqual(19, report["failed"])
        self.assertFalse(b"synthetic_planted" in canonical(report))

    def test_result_boundary_rejects_oversize_nonfinite_and_non_json_values(self) -> None:
        for output in (
            {"raw": "SYNTH_PLANT" * 30000}, {"raw": float("inf")}, {"raw": object()},
        ):
            report = corpus.run_corpus(corpus.validate(self.value), lambda _: output)
            self.assertEqual(19, report["failed"])
            self.assertFalse(b"SYNTH_PLANT" in canonical(report))

    def test_command_exit_codes_and_errors_are_payload_free(self) -> None:
        for command, expected in (("check", 0), ("run-parser", 2), ("SYNTH_PLANTED_ARGUMENT", 1)):
            with self.subTest(command_scope=expected):
                output, error = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    code = corpus.main([command])
                self.assertEqual(expected, code)
                self.assertFalse("SYNTH_PLANTED_ARGUMENT" in output.getvalue() + error.getvalue())
        with mock.patch.object(corpus, "check_repository_corpus", side_effect=Refusal("input_outside_synthetic_catalog")):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                self.assertEqual(1, corpus.main(["check"]))
            self.assertEqual("", output.getvalue())
            self.assertEqual("input_outside_synthetic_catalog", json.loads(error.getvalue())["code"])

    def test_actual_planted_corpus_file_makes_command_fail_without_echoing_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pennilogic-corpus-cli-test-") as temporary:
            root = Path(temporary)
            version = root / "v1"
            version.mkdir()
            (version / "schema.json").write_bytes(corpus.SCHEMA_PATH.read_bytes())
            corpus_file = version / "corpus.json"
            corpus_file.write_bytes(canonical(self.value))
            with mock.patch.object(corpus, "CORPUS_ROOT", root):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(0, corpus.main(["check"]))
                self.assertEqual(19, json.loads(output.getvalue())["corpus_size"])
                self.value["cases"][0]["input"]["identity"] = "SYNTH_PLANTED_ACCOUNT_9999000011112222"
                corpus_file.write_bytes(canonical(self.value))
                output, error = io.StringIO(), io.StringIO()
                with redirect_stdout(output), redirect_stderr(error):
                    self.assertEqual(1, corpus.main(["check"]))
                self.assertEqual("", output.getvalue())
                self.assertFalse("SYNTH_PLANTED" in error.getvalue())
                self.assertEqual("input_outside_synthetic_catalog", json.loads(error.getvalue())["code"])

    def test_extra_sidecar_and_missing_or_hardlinked_files_are_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pennilogic-corpus-test-") as temporary:
            root = Path(temporary)
            (root / "v1").mkdir()
            (root / "v1" / "corpus.json").write_bytes(canonical(self.value))
            (root / "v1" / "schema.json").write_bytes(corpus.SCHEMA_PATH.read_bytes())
            (root / "v1" / "sidecar.txt").write_text("SYNTHETIC EXTRA FILE", encoding="ascii")
            with mock.patch.object(corpus, "CORPUS_ROOT", root):
                with self.assertRaisesRegex(Refusal, "^corpus_inventory_invalid$"):
                    corpus.check_repository_corpus()
            with self.assertRaises(Refusal):
                corpus.load(root / "absent.json")
            alias = root / "alias.json"
            os.link(root / "v1" / "corpus.json", alias)
            with self.assertRaisesRegex(Refusal, "^file_type_refused$"):
                corpus.load(alias)


if __name__ == "__main__":
    unittest.main()
