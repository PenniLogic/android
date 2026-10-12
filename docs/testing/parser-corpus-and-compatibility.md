# Synthetic parser corpus and Android compatibility source contract

Source-start portion of [PenniLogic/android#35](https://github.com/PenniLogic/android/issues/35)
(`T-QA-12`). This publishes source definitions and executable **source checks**, not a production
parser, an instrumented journey, device evidence, a merge approval or completion of the ticket.

The requirements are the accepted
[test strategy v1.1.1](https://github.com/PenniLogic/docs/blob/1722fc79ab2abee15d3adc2ed2f3baaad4bd6ed4/governance/test-strategy.md),
[E25 threat record](https://github.com/PenniLogic/docs/blob/1722fc79ab2abee15d3adc2ed2f3baaad4bd6ed4/governance/threat-model/records/E25.json)
(especially E25-F02), and the existing
[Android behaviour matrix](../platform/android-behavior-matrix.md).

## Run the source checks

From the repository root on Windows (standard-library Python only):

```powershell
python scripts\parser_corpus.py check
python scripts\compatibility_matrix.py check
python scripts\compatibility_matrix.py plan --stage pull_request
python scripts\compatibility_matrix.py plan --stage release_candidate
python -m unittest scripts.tests.test_parser_corpus scripts.tests.test_compatibility_matrix
```

`check` and `plan` return 0 for valid source, 1 for a refusal. Neither executes a parser or device.
The following commands deliberately return **2 (required integration unavailable)**, with every
missing case or lane/subset reported, not a green empty test run:

```powershell
python scripts\parser_corpus.py run-parser
python scripts\compatibility_matrix.py run-required --stage pull_request
python scripts\compatibility_matrix.py run-required --stage release_candidate
```

There is no result-file import, shell/plugin loading, device discovery, automatic retry, adb command,
device reset, network call, purchase or service setup. A CLI argument error is also payload-free.

## Corpus v1

[`corpus.json`](../../testdata/parser_corpus/v1/corpus.json) and its
[`schema.json`](../../testdata/parser_corpus/v1/schema.json) define 19 newly invented cases, two
**synthetic** issuer patterns and five synthetic rule/version pairs. The superseded account-movement
v1 pair is retained beside v2. These are not real issuer coverage or shipped rule versions; the
production library and its target remain with
[PenniLogic/android#46](https://github.com/PenniLogic/android/issues/46) and
[PenniLogic/android#47](https://github.com/PenniLogic/android/issues/47).

Every case carries a stable `syn_NNNN` ID, issuer pattern, rule ID/version, positive/negative polarity,
structured input selectors and an explicit expected result. Provenance applies to every entry in
the versioned document. At least one positive and one negative is required for every declared
rule/version, including superseded versions. Cases cover debit, credit, Indian-grouped decimal
formatting, fractional minor units, ATM withdrawal, reversal, failed payments, promotions, balance
enquiries, OTPs, malformed/missing amounts and an untrusted sender across SMS and notification inputs.

Inputs select only reviewed fixture builders and values in `scripts/parser_corpus.py`; they cannot
contain pasted text, arbitrary sender/account/name/reference fields, or arbitrary amounts.
`SyntheticInput` materializes the invented structure **in process only**. Account, party, reference,
sender and OTP markers use an explicit `<SYNTH-...>` namespace; no real-looking account suffix,
customer name or transaction identifier is necessary. The builders are not production matching
templates or a sender allowlist.

Expected money is positive signed-64-bit integer minor units plus `INR`, never a float, decimal string,
boolean, inferred balance or server-authoritative ledger write. Non-transactional/unmatched results
have null amount, currency and direction. The source catalog permits only its explicitly invented
amounts, and positive expected amounts must agree with the selected input amount.

### What the privacy/contribution check proves

The check validates bounded UTF-8 JSON, unique keys, exact fields/types, a closed selector/rule catalog,
the synthetic provenance **declaration**, money invariants, unique case IDs and positive/negative
coverage. It refuses extra corpus files instead of silently leaving a sidecar unscanned. The existing
physical-file reader rejects symlink/reparse ancestors, non-regular files and hardlinks.
Each schema's canonical JSON fingerprint is allowlisted in its source validator for this version;
an added example/description payload or a relaxed schema fails rather than becoming an unscanned
sidecar. A schema update and its fingerprint require the same independent version review. This is
a content allowlist, not schema signing or a provenance attestation.
Tests plant fresh artificial account/name/amount/message canaries, extra fields, malformed JSON,
nonfinite numbers and invalid result objects; errors contain static codes, not rejected values.

**It cannot prove real-world provenance.** A number can coincide with a real amount, a dishonest
declaration is still a declaration, and the source builder/catalog itself needs independent review.
This is not a general PII detector, redactor, proof that arbitrary text is safe, or approval of a
real-data import. Reports explicitly state `real_world_provenance_proven: false`. No real message
was used as a seed for these fixtures.

### Contribution protocol

1. Describe a newly observed *structure*, never paste, attach, quote or transcribe the real values
   from the message into source, an issue, an AI prompt, a test failure or an artifact. Do not create
   a local real-message seed file or export. Pseudonymised production text is not synthetic.
2. Author a fresh builder shape with synthetic markers and separately invented amount selectors.
   Add a permanent case ID and explicit expected output, with positive/negative coverage for the
   intended rule/version. Keep the parser/library change in its owning ticket. A novel shape needs
   a reviewed catalog/schema extension; it is not admitted by tagging free text `synthetic`.
3. Version the contract/corpus and retain published historical cases and superseded rule versions.
   Existing v1 pins intentionally fail on an unreviewed version or catalog change. Adding a new
   version requires explicitly extending the inventory/validator/schema and its consumer, not
   replacing the v1 directory or relaxing the unknown-file refusal. Corrections need a reviewed
   version change explaining which expected result was wrong; do not edit a golden to hide a regression.
4. Run both focused modules and the source checks above. Root obtains separate Core/QA and applicable
   Privacy/Security/Money/Contract review before publication. `review: required_before_publication`
   is a hold, not a self-attestation. Record only public case/rule identifiers in review evidence.

### Parser integration seam

`parser_corpus.load()` produces an immutable snapshot; `run_corpus(corpus, adapter)` calls the adapter
once for **every case**, never passing it the golden output. The callback receives `SyntheticInput`
(issuer/rule/version, channel, reserved sender and generated text) and returns exactly
`classification`, `direction`, `amount_minor`, `currency`, `reason`, matching the schema's expected
shape. It returns a result or a payload-free `Refusal`; the native adapter owns translation of its
runtime exceptions and must never print payloads. This source seam is not a general log scrubber.
Result objects are bounded and validated before comparison; invalid outputs/refusals fail the case,
and a mismatch cannot skip later cases. No corpus case can be quarantined or retried here.

The report publishes corpus version/digest/size, positive and negative counts by issuer/rule/version
and case IDs/status codes only. It does not serialize generated input, expected/actual money or
exception details. Fixture/expected dataclasses have no payload-bearing representation.
The test-only `fixture_probe` interprets the *invented grammar* to exercise the full callback contract;
it is not exported as a parser or used by either CLI.

There is no accepted parser integration yet:
[PenniLogic/android#8](https://github.com/PenniLogic/android/issues/8) and
[PenniLogic/android#46](https://github.com/PenniLogic/android/issues/46) must bind their actual native
parser to this versioned fixture contract. The callback's identity is not attested by this code;
`production_parser_executed`, `adapter_identity_verified` and `release_qualified` remain false.
A future native consumer can use the existing Gson/JUnit setup to consume the same contract; it must
not substitute the Python fixture probe, an expected-output echo, or an independently copied corpus.

## Compatibility v1

[`android-compatibility-matrix.json`](android-compatibility-matrix.json) and its
[schema](android-compatibility-matrix.schema.json) name OS, memory class, OEM variant, configuration,
test subsets, required stage and result. Definition results are always `not_run`.
The domain validator binds exact lane/subset/stage/configuration requirements; dropping or duplicating
a lane, suppressing a required subset, changing the oldest/current API or marking a definition
`passed` fails.

| Stage | Required lanes | Source selection |
| --- | --- | --- |
| Pull request | API 31, current/API 36, largest text/display with reduced motion | 3 lanes, 10 lane/subset cells |
| Release candidate | All PR lanes plus API 33, API 35, TalkBack, low-memory, two distinct OEM slots, physical Indian-SIM | 10 lanes, 37 cells |

API 31 is the **strategy's oldest lane**, not a newly chosen product support floor. The application
still has provisional `minSdk 26`; [PenniLogic/android#2](https://github.com/PenniLogic/android/issues/2)
owns confirming the support floor. API 36 comes from the accepted current/target baseline. API 33
preserves the strategy's notification/restricted-settings boundary; API 35 also preserves the
existing platform matrix. This source does not change `PlatformBaseline`, SDK pins, AVDs or their
owners' acceptance evidence.

The strategy specifies low-memory, two aggressive-background OEM variants and a physical mid-range
Indian-SIM device, but does **not** select RAM sizes, OEM makes/builds or their OS versions. Those
fields remain null, with named required memory/configuration classes. The two OEM rows are distinct
obligations, not a claim that two unselected devices differ. Profile selection requires accepted
requirements and review, not a guessed RAM cap/vendor or a shared emulator reconfiguration.

### Runner contract, reports and quarantine

`run_required(matrix, stage, runners=...)` is a narrow injection seam keyed by `emulator`,
`managed_device` or `physical`. Each callback receives a `RunnerRequest` containing the immutable
lane, a required subset ID and the validated corpus digest. It returns `AdapterResult(status,
tests_run, skipped)` or a payload-free `Refusal`. A passed subset must execute at least one test and
skip none; unknown status, bool/float/negative counts and invalid results fail. All cells remain in
the result, including blocked cells. Unselected managed/physical profiles never invoke a callback.

**The shipping binding registry is empty.** CLI `run-required` reports missing bindings/unselected
profiles and exits 2. Injected source-fixture results have `evidence_scope: source_adapter`;
`required_runtime_subset_passed`, `runtime_qualified` and `release_qualified` are always false.
These counts are not authenticated instrumented results or physical acceptance.

Reports contain lane/subset IDs, accepted dimension labels (or null), test counts, result codes and
pass rates grouped by OS, memory class and OEM variant. Rates use integer basis points, retain every
required cell in the denominator and exclude quarantined cells from passes. `passed` counts actual
adapter outcomes, while `quarantined` is an overlapping count; individual rows retain both. No
hardware serial, account, stdout/stderr, screenshot, arbitrary exception text or result-file path
is accepted as report metadata.

A quarantined noncritical cell must name a positive Android owning issue number and explicit ISO
opening/expiry dates interpreted in UTC, lasting at most 14 days. Expired/future/duplicate/ownerless entries fail, with
expiry checked again at execution. Security/privacy/money-sensitive subsets cannot be quarantined.
A valid quarantine **still executes and is reported**, never satisfies a required pass, and is not
a reviewer approval or a waiver. The broader repository-wide 2% ceiling, flake-event classification
and deletion approval remain [PenniLogic/infra#28](https://github.com/PenniLogic/infra/issues/28)'s gate.
No automatic retries occur.

### Integration and acceptance still held

| Requirement | This source delivers | Still required |
| --- | --- | --- |
| No real corpus content | Fresh builders, closed selectors, provenance declaration, planted privacy negatives | Independent source/provenance review; a scanner cannot prove historical origin |
| Newly observed format under review | Versioned contribution protocol, immutable in-process snapshot and explicit goldens | Actual future contributions reviewed independently |
| Full parser corpus regression | Complete callback enumeration and failing-mismatch tests | Accepted Android parser/template adapter and full native corpus run |
| Oldest/current parser and permission journeys | API 31/36 required subsets plus large-text, API 33/35 and TalkBack definitions | Implemented journeys and actual instrumented evidence on the required configurations |
| Two OEM variants and low memory | Separate mandatory rows and background/recovery/budget subsets | Approved concrete profiles, managed capacity and real campaigns |
| Physical parser/permission smoke | Explicit Indian-SIM lane and physical runner interface | Provisioned [PenniLogic/android#14](https://github.com/PenniLogic/android/issues/14) device; synthetic account and wipe evidence from its owner |
| Regression blocks merge/release | Source failures and missing-runner exit codes; no retry/quarantine bypass | Native CI adoption, producer-bound results, independent review and protected integration |
| Coverage and compatibility observability | Identifier/count-only corpus coverage and dimension reports | Root publication and genuine native/managed/physical results |

The actual parser and permission screens, background-capture journey, 10000-row timeline,
offline queue and accessibility journeys belong to their feature owners. Do not stub their success.
A real runtime adapter must bind the exact device/API/OEM/memory/configuration, build commit, corpus
digest and required test selectors to **freshly executed** complete native evidence. A count-bearing
`AdapterResult` alone is insufficient. Use the repository's existing JUnit/instrumented tooling and
process-ownership controls; missing selectors, stale/cached/replayed results, skipped tests or
unavailable hardware must fail. Raw logs and screenshots need the owning privacy gates before
publication. Managed/physical campaign setup and wiping are manual owner operations, not actions
authorized by this runner.

## CI adoption, rollout and rollback

Both `scripts/tests/test_*.py` modules are discovered by the existing
`python scripts\privacy_traffic_harness.py self-test --all-scripts` job; the committed-corpus and
platform-reconciliation tests therefore become source checks without a generated workflow edit.
Their inclusion is not an instrumented lane or full parser merge gate.

Canonical adoption belongs to `PenniLogic/infra/governance`, coordinated with
[PenniLogic/android#15](https://github.com/PenniLogic/android/issues/15). The exact future integration
needs are: run the accepted native full-corpus adapter on every parser PR, run all 10 required PR
lane/subset cells and all 37 release-candidate cells on their actual required configurations, fail
the native required `CI`/release gate for missing or regressed evidence, and publish only these
scrubbed identifiers/counts with the actual reviewed commit and strategy version. `run-parser` and
`run-required` currently refuse; they must not be added as green no-op steps, ignored failures, or
replaced with the unit-test fixture probe. No generated workflow, checker or branch protection is
changed by this source delivery. Preserve the outstanding
[PenniLogic/infra#22](https://github.com/PenniLogic/infra/issues/22) conformance hold.

Roll out reviewed versioned source first, then accepted parser/runtime bindings and canonical gates.
Until then keep the issue open and every runtime lane held. Roll back a regressed integration by
pinning the last reviewed corpus/matrix and matching consumer version, retaining historical cases,
reports and failures; do not delete a failing case or weaken a required subset to get green.
